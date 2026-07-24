#!/usr/bin/env python3
from __future__ import annotations

import argparse
import array
import json
import math
import os
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "runtime/settings/analog_receivers.json"
TEMPLATE = ROOT / "config/analog_receivers.example.json"

SERIALS = {"analog_2m": "00000440", "analog_70cm": "00000144"}
RETRY_DELAYS = (0.75, 2.0, 4.0, 7.0, 10.0)
TRANSIENT = (
    "usb_claim_interface error -6",
    "failed to open rtlsdr device",
    "no supported devices found",
    "device or resource busy",
    "resource busy",
)
USB_FATAL = (
    "failed to allocate zero-copy buffer",
    "failed to submit transfer",
    "please increase your allowed usbfs buffer size",
)


class CaptureError(RuntimeError):
    pass


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    source = path if path.exists() else TEMPLATE
    if not source.exists():
        raise CaptureError(f"missing configuration: {path} and {TEMPLATE}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    return payload, source


def normalize_mode(value: Any) -> str:
    text = str(value or "fm").strip().lower().replace("-", "").replace("_", "")
    if text in {"fm", "nfm", "narrowfm", "fmn"}:
        return "fm"
    if text == "am":
        return "am"
    raise CaptureError(f"unsupported mode: {value!r}")


def select_channel(payload: dict[str, Any], role: str) -> dict[str, Any]:
    worker = payload["workers"][role]
    if worker["rtl_serial"] != SERIALS[role]:
        raise CaptureError(f"{role} serial mismatch")
    channels = [
        dict(item)
        for item in worker["channels"]
        if item.get("enabled", True)
    ]
    if not channels:
        raise CaptureError(f"{role} has no enabled channels")

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        name = str(item.get("name") or "").lower()
        continuous = int(
            any(token in name for token in ("noaa", "weather", "atis", "awos", "asos"))
        )
        priority = int(item.get("priority") or 0)
        frequency = int(item["frequency_hz"])
        return (-continuous, -priority, frequency)

    channels.sort(key=score)
    return channels[0]


def build_command(
    serial: str,
    channel: dict[str, Any],
    gain_db: float,
    ppm: int,
) -> list[str]:
    mode = normalize_mode(channel.get("mode"))
    command = [
        "rtl_fm",
        "-d", serial,
        "-f", str(int(channel["frequency_hz"])),
        "-M", mode,
        "-s", "240000",
        "-r", "24000",
        "-g", str(gain_db),
        "-l", "0",
        "-p", str(ppm),
        "-E", "offset",
        "-E", "dc",
    ]
    if mode == "fm":
        command += ["-E", "deemp"]
    return command


def capture_once(command: list[str], seconds: float) -> tuple[bytes, str, int]:
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=2)
    return stdout, stderr.decode("utf-8", errors="replace"), int(proc.returncode or 0)


def capture(command: list[str], seconds: float) -> tuple[bytes, str, int]:
    logs: list[str] = []
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        data, stderr, rc = capture_once(command, seconds)
        logs.append(f"--- attempt {attempt} rc={rc} ---\n{stderr}")
        lower = stderr.lower()
        if any(token in lower for token in USB_FATAL):
            raise CaptureError("USBFS allocation failure detected")
        transient = any(token in lower for token in TRANSIENT)
        if data and not transient:
            return data, "\n".join(logs), attempt
        if transient and attempt <= len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt - 1])
            continue
        if data:
            return data, "\n".join(logs), attempt
        raise CaptureError(f"no PCM produced; stderr={stderr[-1200:]}")
    raise CaptureError("capture retry loop exhausted")


def metrics(data: bytes) -> dict[str, Any]:
    usable = len(data) - (len(data) % 2)
    samples = array.array("h")
    samples.frombytes(data[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise CaptureError("capture contains no samples")
    count = len(samples)
    dc_mean = sum(samples) / count
    rms = math.sqrt(sum(int(v) * int(v) for v in samples) / count)
    peak = max(abs(int(v)) for v in samples)
    clipped = sum(1 for v in samples if abs(int(v)) >= 32700)
    return {
        "sample_count": count,
        "duration_seconds": round(count / 24000, 3),
        "dc_mean": round(dc_mean, 3),
        "rms": round(rms, 3),
        "peak": peak,
        "clipping_percentage": round((clipped / count) * 100.0, 5),
    }


def run(args: argparse.Namespace) -> int:
    payload, source = load_config(Path(args.config))
    channel = select_channel(payload, args.role)
    serial = SERIALS[args.role]
    command = build_command(serial, channel, args.gain_db, args.ppm)
    data, stderr, attempts = capture(command, args.seconds)
    values = metrics(data)
    if values["rms"] <= 0:
        raise CaptureError("capture is silent")

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{args.role}_{int(channel['frequency_hz'])}_run{args.run_number}"
    wav_path = output / f"{stem}.wav"
    json_path = output / f"{stem}.json"
    stderr_path = output / f"{stem}.stderr.log"

    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(data)

    stderr_path.write_text(stderr, encoding="utf-8")
    result = {
        "ok": True,
        "role": args.role,
        "serial": serial,
        "config_source": str(source),
        "channel": {
            "name": channel.get("name"),
            "frequency_hz": int(channel["frequency_hz"]),
            "mode": normalize_mode(channel.get("mode")),
        },
        "profile": {
            "input_sample_rate_hz": 240000,
            "audio_sample_rate_hz": 24000,
            "gain_db": args.gain_db,
            "ppm": args.ppm,
            "offset_tuning": True,
            "dc_block": True,
            "deemphasis": normalize_mode(channel.get("mode")) == "fm",
        },
        "command": command,
        "attempts": attempts,
        "metrics": values,
        "wav_path": str(wav_path),
        "stderr_path": str(stderr_path),
        "human_listening_required": True,
    }
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print("FINAL: PASS")
    return 0


def self_test() -> int:
    fm = build_command("00000440", {"frequency_hz": 162500000, "mode": "fm"}, 49.6, 0)
    am = build_command("00000440", {"frequency_hz": 128525000, "mode": "am"}, 49.6, 0)
    assert fm[fm.index("-s") + 1] == "240000"
    assert fm[fm.index("-r") + 1] == "24000"
    assert "deemp" in fm
    assert "deemp" not in am
    synthetic = array.array("h", [0, 1000, -1000, 2000, -2000] * 100)
    if sys.byteorder != "little":
        synthetic.byteswap()
    assert metrics(synthetic.tobytes())["rms"] > 0
    print("PASS: AM/FM tuning capture self-test")
    print("FINAL: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(SERIALS), default="analog_2m")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--gain-db", type=float, default=49.6)
    parser.add_argument("--ppm", type=int, default=0)
    parser.add_argument("--run-number", type=int, default=1)
    parser.add_argument("--output-dir", default="/tmp/pi-scanner-tuning")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    return self_test() if args.self_test else run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CaptureError, KeyError, ValueError, AssertionError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        print("FINAL: FAIL", file=sys.stderr)
        raise SystemExit(1)

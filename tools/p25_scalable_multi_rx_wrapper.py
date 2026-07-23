#!/usr/bin/env python3
"""Scalable PI-P25-SCANNER OP25 multi_rx launcher.

This program intentionally accepts the established rx.py command-line shape so
the validated backend marker can point at it without changing the confirmed
backend or UI checkpoint.

At each scanner start it:
- reads the persistent receiver role registry;
- keeps p25_control.rtl_serial first and control-only;
- discovers every connected RTL serial matching ^0000025[0-9]$;
- assigns all other matching receivers as voice tuners;
- generates a native boatbod OP25 multi_rx.py JSON configuration;
- falls back to the preserved single-rx command if fewer than two matching
  receivers are present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

RTL_VENDOR_ID = "0bda"
RTL_PRODUCT_IDS = {"2832", "2838"}
DEFAULT_SERIAL_REGEX = r"^0000025[0-9]$"
DEFAULT_MULTI_RX_APP = Path("/home/pi/op25/op25/gr-op25_repeater/apps/multi_rx.py")
DEFAULT_SINGLE_RX_APP = Path("/home/pi/op25/op25/gr-op25_repeater/apps/rx.py")


class MultiRxConfigError(RuntimeError):
    pass


def read_env_marker(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def enumerate_connected_rtl_serials(sysfs_root: Path = Path("/sys/bus/usb/devices")) -> list[str]:
    serials: list[str] = []
    if not sysfs_root.exists():
        return serials
    for device in sorted(sysfs_root.iterdir(), key=lambda item: item.name):
        if not device.is_dir():
            continue
        if read_text(device / "idVendor").lower() != RTL_VENDOR_ID:
            continue
        if read_text(device / "idProduct").lower() not in RTL_PRODUCT_IDS:
            continue
        serial = read_text(device / "serial")
        if serial:
            serials.append(serial)
    return sorted(set(serials))


def normalize_demod(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"fsk4", "c4fm", "fsk", "fm"}:
        return "fsk4"
    return "cqpsk"


def normalize_gain_setting(value: str, fallback: str) -> str:
    text = str(value or fallback).strip()
    if ":" not in text:
        text = f"LNA:{text}"
    name, raw = text.split(":", 1)
    try:
        number = float(raw)
    except ValueError as exc:
        raise MultiRxConfigError(f"invalid gain setting: {text!r}") from exc
    if not 0 <= number <= 60:
        raise MultiRxConfigError(f"gain outside 0..60 dB: {text!r}")
    return f"{name.strip() or 'LNA'}:{number:g}"


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MultiRxConfigError(f"{label} missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MultiRxConfigError(f"{label} invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MultiRxConfigError(f"{label} must contain a JSON object: {path}")
    return payload


def resolve_receiver_order(
    roles_payload: dict[str, Any],
    connected_serials: list[str],
    serial_regex: str,
) -> tuple[str, list[str], list[str]]:
    roles = roles_payload.get("roles")
    if not isinstance(roles, dict):
        raise MultiRxConfigError("receiver role registry has no roles object")

    control_entry = roles.get("p25_control")
    if not isinstance(control_entry, dict):
        raise MultiRxConfigError("receiver role registry has no p25_control role")
    control_serial = str(control_entry.get("rtl_serial") or "").strip()
    if not re.fullmatch(r"\d{8}", control_serial):
        raise MultiRxConfigError(f"invalid p25_control RTL serial: {control_serial!r}")

    try:
        matcher = re.compile(serial_regex)
    except re.error as exc:
        raise MultiRxConfigError(f"invalid receiver serial regex {serial_regex!r}: {exc}") from exc

    matching = sorted(serial for serial in set(connected_serials) if matcher.fullmatch(serial))
    voices = [serial for serial in matching if serial != control_serial]

    preferred_entry = roles.get("p25_voice")
    preferred_voice = (
        str(preferred_entry.get("rtl_serial") or "").strip()
        if isinstance(preferred_entry, dict)
        else ""
    )
    if preferred_voice in voices:
        voices = [preferred_voice] + [serial for serial in voices if serial != preferred_voice]

    return control_serial, voices, matching


def serial_audio_port(serial: str, base_port: int, port_count: int) -> int:
    if not serial or not serial[-1].isdigit():
        raise MultiRxConfigError(f"cannot assign audio port for serial {serial!r}")
    index = int(serial[-1])
    if index >= port_count:
        raise MultiRxConfigError(
            f"serial {serial} maps to audio index {index}, outside configured count {port_count}"
        )
    return base_port + index


def parse_frequency_hz_list(value: str) -> set[int]:
    # Parse comma, space, or semicolon separated Hz/MHz frequencies.
    parsed: set[int] = set()
    for token in re.split(r"[\s,;]+", str(value or "").strip()):
        if not token:
            continue
        try:
            number = float(token)
        except ValueError as exc:
            raise MultiRxConfigError(
                f"invalid excluded control frequency: {token!r}"
            ) from exc
        hz = int(round(number * 1_000_000 if number < 10000 else number))
        if hz <= 0:
            raise MultiRxConfigError(
                f"invalid excluded control frequency: {token!r}"
            )
        parsed.add(hz)
    return parsed


def filter_manifest_control_channels(
    manifest: dict[str, Any],
    excluded_hz: set[int],
) -> tuple[dict[str, Any], list[int], list[int]]:
    # Copy a manifest and remove excluded control-channel frequencies.
    filtered = json.loads(json.dumps(manifest))
    system = select_system(filtered)
    configured = [int(value) for value in system["control_channels_hz"]]
    effective = [value for value in configured if value not in excluded_hz]
    if not effective:
        raise MultiRxConfigError(
            "control-channel exclusion removed every configured channel"
        )
    system["control_channels_hz"] = effective
    system["control_channels_mhz"] = [
        f"{value / 1_000_000:.6f}" for value in effective
    ]
    return filtered, configured, effective


def select_system(manifest: dict[str, Any]) -> dict[str, Any]:
    systems = manifest.get("systems")
    if not isinstance(systems, list) or not systems:
        raise MultiRxConfigError("OP25 manifest has no systems")
    if len(systems) != 1:
        raise MultiRxConfigError(
            f"scalable multi_rx currently requires exactly one enabled P25 system; found {len(systems)}"
        )
    system = systems[0]
    if not isinstance(system, dict):
        raise MultiRxConfigError("OP25 manifest system entry is invalid")
    controls = system.get("control_channels_hz")
    if not isinstance(controls, list) or not controls:
        raise MultiRxConfigError("OP25 manifest system has no control channels")
    return system


def build_multi_rx_config(
    *,
    manifest: dict[str, Any],
    control_serial: str,
    voice_serials: list[str],
    sample_rate: int,
    ppm: float,
    control_gain: str,
    voice_gain: str,
    demod_type: str,
    terminal_type: str,
    crypt_behavior: int,
    audio_base_port: int,
    audio_port_count: int,
    control_only_whitelist: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    system = select_system(manifest)
    system_name = str(system.get("name") or "P25 System")
    controls_hz = [int(value) for value in system["control_channels_hz"]]
    controls_mhz = system.get("control_channels_mhz")
    if not isinstance(controls_mhz, list) or not controls_mhz:
        controls_mhz = [f"{value / 1_000_000:.6f}" for value in controls_hz]

    ordered_serials = [control_serial, *voice_serials]
    initial_frequency = controls_hz[0]
    receiver_rows: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []

    for index, serial in enumerate(ordered_serials):
        role = "control" if index == 0 else "voice"
        receiver_gain = control_gain if role == "control" else voice_gain
        device_name = f"rtl_{serial}"
        audio_port = serial_audio_port(serial, audio_base_port, audio_port_count)
        devices.append(
            {
                "args": f"rtl={serial}",
                "frequency": initial_frequency,
                "gain_mode": False,
                "gains": receiver_gain,
                "name": device_name,
                "offset": 0,
                "ppm": ppm,
                "rate": sample_rate,
                "tunable": True,
                "usable_bw_pct": 0.85,
            }
        )
        channels.append(
            {
                "name": f"P25 {role.title()} {serial}",
                "device": device_name,
                "trunking_sysname": system_name,
                "demod_type": demod_type,
                "destination": f"udp://127.0.0.1:{audio_port}",
                "meta_stream_name": "",
                "excess_bw": 0.2,
                "filter_type": "rc",
                "frequency": initial_frequency,
                "if_rate": 24000,
                "plot": "",
                "symbol_rate": 4800,
                "enable_analog": "off",
                "blacklist": "",
                # A truthy whitelist containing impossible TGID 0 prevents the
                # dedicated control receiver from accepting voice assignments.
                "whitelist": str(control_only_whitelist) if role == "control" else "",
                "crypt_behavior": crypt_behavior,
            }
        )
        receiver_rows.append(
            {
                "serial": serial,
                "role": role,
                "device": device_name,
                "audio_port": audio_port,
                "gain": receiver_gain,
            }
        )

    trunking_channel = {
        "sysname": system_name,
        "control_channel_list": ",".join(str(value) for value in controls_mhz),
        "nac": str(system.get("nac") or "0"),
        "tgid_tags_file": str(system.get("tags_file") or ""),
        "whitelist": str(system.get("whitelist_file") or ""),
        "blacklist": str(system.get("blacklist_file") or ""),
        "crypt_behavior": crypt_behavior,
    }

    config = {
        "channels": channels,
        "devices": devices,
        "trunking": {
            "module": "tk_p25.py",
            "chans": [trunking_channel],
        },
        "terminal": {
            "module": "terminal.py",
            "terminal_type": terminal_type,
            "curses_plot_interval": 0.1,
            "http_plot_interval": 1.0,
            "http_plot_directory": "../www/images",
        },
    }
    return config, receiver_rows


def build_single_rx_command(
    *,
    single_rx_app: Path,
    args: argparse.Namespace,
    unknown: list[str],
) -> list[str]:
    command = [
        str(single_rx_app),
        "--args",
        args.device_args,
        "-S",
        str(args.sample_rate),
        "-q",
        str(args.ppm),
        "-N",
        args.gain,
        "-T",
        args.trunk_tsv,
    ]
    if args.phase1_voice:
        command.append("-V")
    if args.phase2_voice:
        command.append("-2")
    if args.terminal:
        command.extend(["-l", args.terminal])
    if args.crypt_behavior is not None:
        command.extend(["--crypt-behavior", str(args.crypt_behavior)])
    if args.demod_type:
        command.extend(["-D", args.demod_type])
    if args.wireshark:
        command.append("-w")
    if args.audio_host:
        command.extend(["-W", args.audio_host])
    if args.audio_port:
        command.extend(["-u", str(args.audio_port)])
    if args.verbosity is not None:
        command.extend(["-v", str(args.verbosity)])
    command.extend(unknown)
    return command


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="PI-P25-SCANNER rx.py-compatible scalable multi_rx launcher",
        allow_abbrev=False,
    )
    parser.add_argument("--args", dest="device_args", default="rtl=00000251")
    parser.add_argument("-S", dest="sample_rate", type=int, default=960000)
    parser.add_argument("-q", dest="ppm", type=float, default=0.0)
    parser.add_argument("-N", dest="gain", default="LNA:40")
    parser.add_argument("-T", dest="trunk_tsv", required=True)
    parser.add_argument("-V", dest="phase1_voice", action="store_true")
    parser.add_argument("-2", dest="phase2_voice", action="store_true")
    parser.add_argument("-l", dest="terminal", default="")
    parser.add_argument("--crypt-behavior", type=int, default=2)
    parser.add_argument("-D", dest="demod_type", default="")
    parser.add_argument("-w", dest="wireshark", action="store_true")
    parser.add_argument("-W", dest="audio_host", default="127.0.0.1")
    parser.add_argument("-u", dest="audio_port", type=int, default=23456)
    parser.add_argument("-v", dest="verbosity", type=int, default=5)

    parser.add_argument("--project-root", default="")
    parser.add_argument("--marker", default="")
    parser.add_argument("--receiver-roles", default="")
    parser.add_argument("--manifest", default="")
    parser.add_argument("--multi-rx-app", default="")
    parser.add_argument("--single-rx-app", default="")
    parser.add_argument("--multi-rx-config", default="")
    parser.add_argument("--multi-rx-state", default="")
    parser.add_argument("--serial-regex", default="")
    parser.add_argument("--audio-base-port", type=int, default=None)
    parser.add_argument("--audio-port-count", type=int, default=None)
    parser.add_argument("--connected-serial", action="append", default=[])
    parser.add_argument("--multi-rx-dry-run", action="store_true")
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    args, unknown = parse_args(argv)
    project_root = (
        Path(args.project_root).expanduser().resolve()
        if args.project_root
        else Path(__file__).resolve().parents[1]
    )
    marker_path = (
        Path(args.marker).expanduser().resolve()
        if args.marker
        else project_root / "runtime" / "settings" / "op25_validated_rx_command.env"
    )
    marker = read_env_marker(marker_path)
    runtime_log_path = Path(
        os.environ.get("P25_RUNTIME_LOG", "")
        or marker.get("P25_RUNTIME_LOG", "")
        or project_root / "runtime" / "logs" / "op25-runtime.log"
    )
    runtime_log_max_bytes = int(
        os.environ.get("P25_RUNTIME_LOG_MAX_BYTES", "")
        or marker.get("P25_RUNTIME_LOG_MAX_BYTES", "")
        or "8388608"
    )
    runtime_log_backups = int(
        os.environ.get("P25_RUNTIME_LOG_BACKUPS", "")
        or marker.get("P25_RUNTIME_LOG_BACKUPS", "")
        or "5"
    )
    rotating_logger = (
        Path(__file__).resolve().parent
        / "p25_rotating_log_exec.py"
    )
    if not rotating_logger.is_file():
        raise MultiRxConfigError(
            f"rotating OP25 logger missing: {rotating_logger}"
        )
    if not 1_048_576 <= runtime_log_max_bytes <= 67_108_864:
        raise MultiRxConfigError(
            "P25_RUNTIME_LOG_MAX_BYTES must be 1048576..67108864"
        )
    if not 1 <= runtime_log_backups <= 20:
        raise MultiRxConfigError(
            "P25_RUNTIME_LOG_BACKUPS must be 1..20"
        )

    legacy_gain = normalize_gain_setting(str(args.gain), "LNA:40")
    control_gain = normalize_gain_setting(
        os.environ.get("P25_CONTROL_GAIN", "") or marker.get("P25_CONTROL_GAIN", ""),
        legacy_gain,
    )
    voice_gain = normalize_gain_setting(
        os.environ.get("P25_VOICE_GAIN", "") or marker.get("P25_VOICE_GAIN", ""),
        legacy_gain,
    )

    excluded_control_channels_hz = parse_frequency_hz_list(
        os.environ.get("P25_CONTROL_CHANNEL_EXCLUDE_HZ", "")
        or marker.get("P25_CONTROL_CHANNEL_EXCLUDE_HZ", "")
    )

    receiver_roles_path = Path(
        args.receiver_roles
        or marker.get("P25_MULTI_RX_RECEIVER_ROLES", "")
        or project_root / "runtime" / "settings" / "receiver_roles.json"
    )
    manifest_path = Path(
        args.manifest
        or marker.get("P25_MULTI_RX_MANIFEST", "")
        or project_root / "runtime" / "op25" / "manifest.json"
    )
    config_output = Path(
        args.multi_rx_config
        or marker.get("P25_MULTI_RX_CONFIG", "")
        or project_root / "runtime" / "op25" / "multi_rx.json"
    )
    state_output = Path(
        args.multi_rx_state
        or marker.get("P25_MULTI_RX_STATE", "")
        or project_root / "runtime" / "op25" / "multi_rx_state.json"
    )
    single_rx_app = Path(
        args.single_rx_app
        or marker.get("P25_VALIDATED_SINGLE_RX_APP", "")
        or DEFAULT_SINGLE_RX_APP
    )
    multi_rx_app = Path(
        args.multi_rx_app
        or marker.get("P25_VALIDATED_MULTI_RX_APP", "")
        or DEFAULT_MULTI_RX_APP
    )
    serial_regex = (
        args.serial_regex
        or marker.get("P25_MULTI_RX_RECEIVER_REGEX", "")
        or DEFAULT_SERIAL_REGEX
    )
    audio_base_port = int(
        args.audio_base_port
        if args.audio_base_port is not None
        else marker.get("P25_MULTI_RX_AUDIO_BASE_PORT", "23500")
    )
    audio_port_count = int(
        args.audio_port_count
        if args.audio_port_count is not None
        else marker.get("P25_MULTI_RX_AUDIO_PORT_COUNT", "10")
    )

    roles_payload = load_json_object(receiver_roles_path, "receiver role registry")
    connected = (
        sorted(set(args.connected_serial))
        if args.connected_serial
        else enumerate_connected_rtl_serials()
    )
    control_serial, voice_serials, matching = resolve_receiver_order(
        roles_payload,
        connected,
        serial_regex,
    )

    fallback_reasons: list[str] = []
    if control_serial not in matching:
        fallback_reasons.append(f"control receiver {control_serial} is not connected")
    if not voice_serials:
        fallback_reasons.append("no connected voice receiver matches the configured serial pool")
    if not multi_rx_app.exists():
        fallback_reasons.append(f"OP25 multi_rx app missing: {multi_rx_app}")

    if fallback_reasons:
        command = build_single_rx_command(
            single_rx_app=single_rx_app,
            args=args,
            unknown=unknown,
        )
        state = {
            "ok": single_rx_app.exists(),
            "mode": "single_rx_fallback",
            "generated_utc": time.time(),
            "project_root": str(project_root),
            "marker": str(marker_path),
            "control_serial": control_serial,
            "voice_serials": voice_serials,
            "matching_serials": matching,
            "connected_serials": connected,
            "fallback_reasons": fallback_reasons,
            "command": command,
        }
        atomic_write_json(state_output, state)
        print(json.dumps(state, indent=2, sort_keys=True), flush=True)
        if args.multi_rx_dry_run:
            return 0
        if not single_rx_app.exists():
            raise MultiRxConfigError(f"single-rx fallback app missing: {single_rx_app}")
        os.chdir(single_rx_app.parent)
        os.execvpe(command[0], command, os.environ.copy())
        return 127

    manifest = load_json_object(manifest_path, "OP25 manifest")
    (
        manifest,
        configured_control_channels_hz,
        effective_control_channels_hz,
    ) = filter_manifest_control_channels(
        manifest,
        excluded_control_channels_hz,
    )
    system = select_system(manifest)
    demod_type = normalize_demod(
        args.demod_type
        or marker.get("P25_VALIDATED_RX_DEMOD_TYPE", "")
        or str(system.get("modulation") or "")
    )
    terminal_type = args.terminal or "http:127.0.0.1:18091"
    control_only_whitelist = config_output.parent / "multi_rx_control_only_whitelist.tsv"
    atomic_write_text(control_only_whitelist, "0\n")

    config, receiver_rows = build_multi_rx_config(
        manifest=manifest,
        control_serial=control_serial,
        voice_serials=voice_serials,
        sample_rate=int(args.sample_rate),
        ppm=float(args.ppm),
        control_gain=control_gain,
        voice_gain=voice_gain,
        demod_type=demod_type,
        terminal_type=terminal_type,
        crypt_behavior=int(args.crypt_behavior),
        audio_base_port=audio_base_port,
        audio_port_count=audio_port_count,
        control_only_whitelist=control_only_whitelist,
    )
    atomic_write_json(config_output, config)

    sticky_launcher = (
        Path(__file__).resolve().parent
        / "p25_multi_rx_sticky_launcher.py"
    )
    if not sticky_launcher.is_file():
        raise RuntimeError(
            f"sticky control launcher missing: {sticky_launcher}"
        )

    sticky_retries = int(
        os.environ.get(
            "P25_CC_TIMEOUT_RETRIES",
            "10",
        )
    )
    if not 4 <= sticky_retries <= 30:
        raise RuntimeError(
            "P25_CC_TIMEOUT_RETRIES must be 4..30"
        )

    op25_command = [
        sys.executable,
        "-u",
        str(sticky_launcher),
        "--cc-timeout-retries",
        str(sticky_retries),
        "--app",
        str(multi_rx_app),
        "--",
        "-c",
        str(config_output),
        "-v",
        str(max(5, int(args.verbosity or 0))),
    ]
    command = [
        sys.executable,
        "-u",
        str(rotating_logger),
        "--log",
        str(runtime_log_path),
        "--max-bytes",
        str(runtime_log_max_bytes),
        "--backups",
        str(runtime_log_backups),
        "--",
        *op25_command,
    ]
    state = {
        "ok": True,
        "mode": "multi_rx",
        "control_channel_policy": "sticky_consecutive_timeouts",
        "cc_timeout_retries": sticky_retries,
        "sticky_control_launcher": str(sticky_launcher),
        "multi_rx_app": str(multi_rx_app),
        "generated_utc": time.time(),
        "project_root": str(project_root),
        "marker": str(marker_path),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "excluded_control_channels_hz": sorted(
            excluded_control_channels_hz
        ),
        "configured_control_channels_hz": (
            configured_control_channels_hz
        ),
        "effective_control_channels_hz": (
            effective_control_channels_hz
        ),
        "config": str(config_output),
        "config_sha256": sha256_file(config_output),
        "control_only_whitelist": str(control_only_whitelist),
        "control_serial": control_serial,
        "voice_serials": voice_serials,
        "matching_serials": matching,
        "connected_serials": connected,
        "receiver_count": len(receiver_rows),
        "receivers": receiver_rows,
        "serial_regex": serial_regex,
        "demod_type": demod_type,
        "sample_rate": int(args.sample_rate),
        "gain": str(args.gain),
        "control_gain": control_gain,
        "voice_gain": voice_gain,
        "ppm": float(args.ppm),
        "audio_base_port": audio_base_port,
        "audio_port_count": audio_port_count,
        "runtime_log": str(runtime_log_path),
        "runtime_log_max_bytes": runtime_log_max_bytes,
        "runtime_log_backups": runtime_log_backups,
        "rotating_logger": str(rotating_logger),
        "op25_command": op25_command,
        "command": command,
        "ignored_rx_options": unknown,
    }
    atomic_write_json(state_output, state)
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)

    if args.multi_rx_dry_run:
        return 0

    os.chdir(multi_rx_app.parent)
    exec_env = os.environ.copy()
    exec_env["PYTHONUNBUFFERED"] = "1"
    exec_env.setdefault("PYTHONIOENCODING", "utf-8")
    os.execvpe(command[0], command, exec_env)
    return 127


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MultiRxConfigError as exc:
        print(f"MULTI_RX_ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)

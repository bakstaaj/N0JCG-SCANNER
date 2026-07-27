"""Bounded OP25 PPM calibration helper for PI-P25-SCANNER.

This module deliberately performs calibration as an explicit stopped-scanner
workflow. It does not continuously retune while monitoring and it never
changes encryption handling.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .backend_launch import MARKER_RELATIVE_PATH, build_validated_op25_command
from .config_model import ConfigError
from .config_store import read_active_config_payload, write_runtime_config
from .op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAST_REPORT_PATH = PROJECT_ROOT / "runtime" / "settings" / "ppm_calibration_last.json"
DEFAULT_SPAN_PPM = 3
DEFAULT_STEP_PPM = 1
DEFAULT_DWELL_SECONDS = 8
MIN_DWELL_SECONDS = 4
MAX_DWELL_SECONDS = 20
MAX_CANDIDATES = 15


class PpmCalibrationError(RuntimeError):
    """Raised when bounded PPM calibration cannot run."""


@dataclass(slots=True)
class CandidateResult:
    ppm: int
    score: int
    runtime_seconds: float
    return_code: int | None
    timed_out: bool
    positive_counts: dict[str, int] = field(default_factory=dict)
    negative_counts: dict[str, int] = field(default_factory=dict)
    line_count: int = 0
    output_tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


POSITIVE_PATTERNS: dict[str, int] = {
    r"\btsbk\b": 8,
    r"voice update": 8,
    r"\btgid\b": 8,
    r"grpaddr": 8,
    r"\bnac\b": 5,
    r"\bp25\b": 4,
    r"tdma|fdma|phase": 4,
    r"duid": 3,
    r"trunk|control channel|cc ": 3,
    r"encrypted|algid|ciphertxt": 2,
    r"tuner|frequency|freq": 1,
}
NEGATIVE_PATTERNS: dict[str, int] = {
    r"traceback|exception|error": 40,
    r"failed|failure": 25,
    r"no such file|not found": 40,
    r"device busy|resource busy": 35,
    r"sync timeout|timeout waiting|no sync": 6,
}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _candidate_ppms(center: int, span: int, step: int) -> list[int]:
    span = max(0, min(20, int(span)))
    step = max(1, min(10, int(step)))
    values = list(range(center - span, center + span + 1, step))
    if center not in values:
        values.append(center)
    values = sorted(set(values), key=lambda value: (abs(value - center), value))
    values = sorted(values)
    if len(values) > MAX_CANDIDATES:
        raise PpmCalibrationError(f"PPM candidate count too large: {len(values)} > {MAX_CANDIDATES}")
    return values


def _replace_option(command: list[str], flag: str, value: str) -> list[str]:
    updated = list(command)
    if flag in updated:
        index = updated.index(flag)
        if index + 1 >= len(updated):
            updated.append(value)
        else:
            updated[index + 1] = value
    else:
        updated.extend([flag, value])
    return updated


def _remove_option(command: list[str], flag: str, has_value: bool) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(command):
        token = command[index]
        if token == flag:
            index += 2 if has_value else 1
            continue
        out.append(token)
        index += 1
    return out


def _calibration_command(base_command: list[str], ppm: int) -> list[str]:
    command = list(base_command)
    command = _replace_option(command, "-q", str(ppm))
    # Keep calibration quiet and isolated from the browser audio bridge and
    # OP25 HTTP terminal port. stdout still carries enough decode/status text
    # for a conservative score on most OP25 builds.
    for flag, has_value in (("-w", False), ("-W", True), ("-u", True), ("-l", True)):
        command = _remove_option(command, flag, has_value)
    if "-v" not in command and not any(token.startswith("-v") and token != "-V" for token in command):
        command.extend(["-v", "1"])
    return command


def _score_text(text: str) -> tuple[int, dict[str, int], dict[str, int], list[str]]:
    lower = text.lower()
    positive: dict[str, int] = {}
    negative: dict[str, int] = {}
    score = 0
    for pattern, weight in POSITIVE_PATTERNS.items():
        count = len(re.findall(pattern, lower, re.IGNORECASE))
        if count:
            positive[pattern] = count
            score += count * weight
    for pattern, weight in NEGATIVE_PATTERNS.items():
        count = len(re.findall(pattern, lower, re.IGNORECASE))
        if count:
            negative[pattern] = count
            score -= count * weight
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    score += min(len(lines), 25)
    return score, positive, negative, lines[-20:]


def _signal_process_group(
    proc: subprocess.Popen[str],
    sig: int,
) -> None:
    """Signal the candidate process group, falling back to the direct child."""
    try:
        os.killpg(proc.pid, sig)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass


def _terminate_process_group(
    proc: subprocess.Popen[str],
    term_timeout: float = 3.0,
    kill_timeout: float = 3.0,
) -> str:
    """Terminate a process tree and return all output collected before exit."""
    if proc.poll() is not None:
        output, _ = proc.communicate()
        return output or ""

    _signal_process_group(proc, signal.SIGTERM)

    try:
        output, _ = proc.communicate(timeout=term_timeout)
        return output or ""
    except subprocess.TimeoutExpired as exc:
        partial = exc.output or ""

    sigkill = getattr(signal, "SIGKILL", None)
    if sigkill is not None:
        _signal_process_group(proc, sigkill)
    else:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

    try:
        output, _ = proc.communicate(timeout=kill_timeout)
        return (partial or "") + (output or "")
    except subprocess.TimeoutExpired as exc:
        final_partial = exc.output or ""
        return (partial or "") + (final_partial or "")


def _run_candidate(base_command: list[str], cwd: str, env: dict[str, str], ppm: int, dwell_seconds: int) -> CandidateResult:
    command = _calibration_command(base_command, ppm)
    launch_env = os.environ.copy()
    launch_env.update(env or {})
    launch_env["PYTHONUNBUFFERED"] = "1"
    launch_env["PYTHONIOENCODING"] = "utf-8"
    start = time.monotonic()
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=launch_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=dwell_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        output = _terminate_process_group(proc)
    runtime = time.monotonic() - start
    score, positive, negative, tail = _score_text(output or "")
    return CandidateResult(
        ppm=ppm,
        score=score,
        runtime_seconds=round(runtime, 3),
        return_code=proc.returncode,
        timed_out=timed_out,
        positive_counts=positive,
        negative_counts=negative,
        line_count=len([line for line in (output or "").splitlines() if line.strip()]),
        output_tail=tail,
    )


def _read_marker_values(marker_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not marker_path.exists():
        return values
    for raw in marker_path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw or raw.strip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _quote_env(value: Any) -> str:
    text = str(value)
    return "'" + text.replace("'", "'\\''") + "'"


def _update_marker_ppm(marker_path: Path, best_ppm: int, report_path: Path) -> None:
    if not marker_path.exists():
        raise PpmCalibrationError(f"validated OP25 marker not found: {marker_path}")
    lines = marker_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen_ppm = False
    skip_prefixes = (
        "P25_VALIDATED_RX_PPM_CALIBRATED_UTC=",
        "P25_VALIDATED_RX_PPM_CALIBRATION_REPORT=",
    )
    for line in lines:
        if line.startswith("P25_VALIDATED_RX_PPM="):
            out.append(f"P25_VALIDATED_RX_PPM={_quote_env(best_ppm)}")
            seen_ppm = True
        elif any(line.startswith(prefix) for prefix in skip_prefixes):
            continue
        else:
            out.append(line)
    if not seen_ppm:
        out.append(f"P25_VALIDATED_RX_PPM={_quote_env(best_ppm)}")
    out.append(f"P25_VALIDATED_RX_PPM_CALIBRATED_UTC={_quote_env(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))}")
    out.append(f"P25_VALIDATED_RX_PPM_CALIBRATION_REPORT={_quote_env(report_path)}")
    marker_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _update_config_ppm(best_ppm: int, apply_voice: bool) -> dict[str, Any]:
    payload, active_path = read_active_config_payload()
    if not isinstance(payload.get("systems"), list) or not payload["systems"]:
        raise ConfigError("active config has no systems")
    system = payload["systems"][0]
    roles = system.setdefault("receiver_roles", {})
    if not isinstance(roles, dict):
        roles = {}
        system["receiver_roles"] = roles
    for role_name in (["p25_control", "p25_voice"] if apply_voice else ["p25_control"]):
        role = roles.setdefault(role_name, {})
        if not isinstance(role, dict):
            role = {}
            roles[role_name] = role
        role["ppm"] = int(best_ppm)
    write_result = write_runtime_config(payload, backup=True)
    return {"active_path_before": str(active_path), "write_result": write_result, "apply_voice": apply_voice}


def last_ppm_calibration_report() -> dict[str, Any]:
    if not LAST_REPORT_PATH.exists():
        return {"ok": True, "calibrated": False, "report_path": str(LAST_REPORT_PATH)}
    try:
        payload = json.loads(LAST_REPORT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "calibrated": False, "report_path": str(LAST_REPORT_PATH), "error": str(exc)}
    if isinstance(payload, dict):
        payload.setdefault("ok", True)
        payload.setdefault("calibrated", True)
        payload.setdefault("report_path", str(LAST_REPORT_PATH))
        return payload
    return {"ok": False, "calibrated": False, "report_path": str(LAST_REPORT_PATH), "error": "invalid report JSON"}


def calibrate_ppm(request: dict[str, Any] | None = None) -> dict[str, Any]:
    request = request or {}
    span = _as_int(request.get("span_ppm"), DEFAULT_SPAN_PPM)
    step = _as_int(request.get("step_ppm"), DEFAULT_STEP_PPM)
    dwell = _as_int(request.get("dwell_seconds"), DEFAULT_DWELL_SECONDS)
    dwell = max(MIN_DWELL_SECONDS, min(MAX_DWELL_SECONDS, dwell))
    apply_voice = bool(request.get("apply_voice", False))

    marker_path = PROJECT_ROOT / MARKER_RELATIVE_PATH
    marker_values = _read_marker_values(marker_path)
    center = _as_int(request.get("center_ppm"), _as_int(marker_values.get("P25_VALIDATED_RX_PPM"), 0))
    candidates = _candidate_ppms(center, span, step)

    # Ensure trunk.tsv reflects the current active config before probing.
    _config_payload, config_path = read_active_config_payload()
    manifest = generate_op25_configs(config_path, DEFAULT_OUTPUT_DIR).to_dict()
    validated = build_validated_op25_command(PROJECT_ROOT)
    if validated is None:
        raise PpmCalibrationError("validated OP25 marker is required before PPM calibration")

    results: list[CandidateResult] = []
    for ppm in candidates:
        results.append(_run_candidate(validated.command, validated.cwd, validated.env, ppm, dwell))

    if not results:
        raise PpmCalibrationError("no PPM candidates were tested")
    # Highest score wins; ties prefer the candidate closest to the current PPM.
    best = sorted(results, key=lambda item: (-item.score, abs(item.ppm - center), item.ppm))[0]
    status = "ok" if best.score > 0 else "weak"
    config_update = _update_config_ppm(best.ppm, apply_voice=apply_voice)

    report: dict[str, Any] = {
        "ok": True,
        "calibrated": True,
        "ppm_calibration_mode": "bounded-op25-control-sweep-v0.5a1",
        "status": status,
        "message": "Best PPM selected from bounded OP25 control-channel sweep." if status == "ok" else "Best PPM selected, but all candidates scored weakly; verify control channel and antenna.",
        "center_ppm": center,
        "best_ppm": best.ppm,
        "span_ppm": span,
        "step_ppm": step,
        "dwell_seconds": dwell,
        "candidate_count": len(results),
        "candidates": [item.to_dict() for item in results],
        "manifest": manifest,
        "config_update": config_update,
        "marker_path": str(marker_path),
        "updated_utc": time.time(),
    }
    LAST_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _update_marker_ppm(marker_path, best.ppm, LAST_REPORT_PATH)
    return report


if __name__ == "__main__":
    payload = calibrate_ppm({})
    print(json.dumps(payload, indent=2, sort_keys=True))

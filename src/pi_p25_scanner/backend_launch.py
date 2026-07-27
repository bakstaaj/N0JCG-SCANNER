"""Validated OP25 backend launch helpers.

This module intentionally only consumes a command marker produced by the
bounded Pi-side live command probe. It does not guess OP25 command lines.

The web UI depends on marker metadata from /api/status to decide whether the
Start Scanner button can be enabled. Metadata performs the same safe readiness
checks used by the launcher and exposes start_ready/validated status.

V0.3U note:
The validated OP25 command also enables UDP PCM output for the independent raw
browser-audio bridge service:
  -w -W 127.0.0.1 -u 23456
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .rtl_serial_guard import validate_op25_device_args  # V0.5E rtl-serial-pool-0000025X

from .rtl_serial_guard import validate_op25_device_args  # V0.5E rtl-serial-pool-0000025X


MARKER_RELATIVE_PATH = Path("runtime") / "settings" / "op25_validated_rx_command.env"
REQUIRED_MARKER_FIELDS = [
    "P25_VALIDATED_RX_APP",
    "P25_VALIDATED_RX_APP_DIR",
    "P25_VALIDATED_RX_PYTHONPATH",
    "P25_VALIDATED_RX_ARGS",
    "P25_VALIDATED_RX_SAMPLE_RATE",
    "P25_VALIDATED_RX_GAIN",
    "P25_VALIDATED_RX_PPM",
    "P25_VALIDATED_RX_TRUNK_TSV",
]
DEFAULT_AUDIO_HOST = "127.0.0.1"
DEFAULT_AUDIO_PORT = "23456"


class LaunchConfigError(RuntimeError):
    """Raised when a validated OP25 launch marker exists but is unusable."""


@dataclass(slots=True)
class ValidatedOp25Command:
    command: list[str]
    cwd: str
    env: dict[str, str]
    marker_path: str
    app: str
    device_args: str
    trunk_tsv: str
    pythonpath: str
    report: str
    log: str

    def to_status_dict(self) -> dict[str, object]:
        return {
            "source": "validated_marker",
            "exists": True,
            "validated": True,
            "start_ready": True,
            "path": self.marker_path,
            "app": self.app,
            "cwd": self.cwd,
            "device_args": self.device_args,
            "trunk_tsv": self.trunk_tsv,
            "pythonpath": self.pythonpath,
            "report": self.report,
            "log": self.log,
        }


def _marker_path(project_root: Path) -> Path:
    return project_root / MARKER_RELATIVE_PATH


def _read_marker(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise LaunchConfigError(f"validated OP25 marker missing: {path}") from exc

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _metadata_from_values(marker: Path, values: dict[str, str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": "validated_marker",
        "exists": marker.exists(),
        "validated": False,
        "start_ready": False,
        "path": str(marker),
        "app": values.get("P25_VALIDATED_RX_APP", ""),
        "cwd": values.get("P25_VALIDATED_RX_APP_DIR", ""),
        "device_args": values.get("P25_VALIDATED_RX_ARGS", ""),
        "trunk_tsv": values.get("P25_VALIDATED_RX_TRUNK_TSV", ""),
        "pythonpath": values.get("P25_VALIDATED_RX_PYTHONPATH", ""),
        "report": values.get("P25_VALIDATED_RX_REPORT", ""),
        "log": values.get("P25_VALIDATED_RX_LOG", ""),
        "warnings": [],
    }

    missing = [key for key in REQUIRED_MARKER_FIELDS if not values.get(key)]
    if missing:
        metadata["error"] = f"validated OP25 marker is missing required fields: {', '.join(missing)}"
        return metadata

    try:
        values["P25_VALIDATED_RX_ARGS"] = validate_op25_device_args(values.get("P25_VALIDATED_RX_ARGS", ""))
    except ValueError as exc:
        metadata["error"] = str(exc)
        return metadata

    try:
        values["P25_VALIDATED_RX_ARGS"] = validate_op25_device_args(values.get("P25_VALIDATED_RX_ARGS", ""))
    except ValueError as exc:
        metadata["error"] = str(exc)
        return metadata

    app = Path(values["P25_VALIDATED_RX_APP"])
    cwd = Path(values["P25_VALIDATED_RX_APP_DIR"])
    trunk_tsv = Path(values["P25_VALIDATED_RX_TRUNK_TSV"])

    if not app.exists():
        metadata["error"] = f"validated OP25 app does not exist: {app}"
        return metadata
    if not cwd.is_dir():
        metadata["error"] = f"validated OP25 app directory does not exist: {cwd}"
        return metadata

    if not trunk_tsv.exists():
        metadata["warnings"].append(
            f"validated OP25 trunk TSV is not present yet; Start will regenerate config: {trunk_tsv}"
        )

    metadata["validated"] = True
    metadata["start_ready"] = True
    return metadata


def ensure_op25_udp_audio_args(command: list[str]) -> list[str]:
    """Ensure OP25 sends raw PCM UDP audio to the independent browser bridge.

    The browser audio bridge runs separately on TCP 8072 and listens for OP25 UDP
    PCM on localhost:23456. This helper keeps the normal backend stable: it does
    not manage the audio service or poll audio status; it only makes the OP25
    process emit the PCM frames the already-running bridge needs.
    """

    updated = list(command)
    udp_host = os.environ.get("P25_SCANNER_AUDIO_UDP_HOST", "127.0.0.1")
    udp_port = os.environ.get("P25_SCANNER_AUDIO_UDP_PORT", "23456")
    if "-w" not in updated:
        updated.append("-w")
    if "-W" not in updated:
        updated.extend(["-W", udp_host])
    if "-u" not in updated:
        updated.extend(["-u", udp_port])
    return updated


def validated_command_marker_metadata(project_root: Path) -> dict[str, Any]:
    """Return safe marker metadata suitable for UI launch readiness."""

    marker = _marker_path(project_root)
    if not marker.exists():
        return {
            "source": "validated_marker",
            "exists": False,
            "validated": False,
            "start_ready": False,
            "path": str(marker),
        }
    try:
        values = _read_marker(marker)
    except LaunchConfigError as exc:
        return {
            "source": "validated_marker",
            "exists": True,
            "validated": False,
            "start_ready": False,
            "path": str(marker),
            "error": str(exc),
        }
    return _metadata_from_values(marker, values)


def _add_raw_audio_udp_args(command: list[str], values: dict[str, str]) -> list[str]:
    updated = list(command)
    audio_host = values.get("P25_VALIDATED_RX_AUDIO_HOST", DEFAULT_AUDIO_HOST).strip() or DEFAULT_AUDIO_HOST
    audio_port = values.get("P25_VALIDATED_RX_AUDIO_PORT", DEFAULT_AUDIO_PORT).strip() or DEFAULT_AUDIO_PORT
    if "-w" not in updated:
        updated.append("-w")
    if "-W" not in updated:
        updated.extend(["-W", audio_host])
    if "-u" not in updated:
        updated.extend(["-u", audio_port])
    return updated


def prepend_pythonpath(
    required_pythonpath: str,
    existing_pythonpath: str = "",
) -> str:
    """Prepend required Python paths while preserving unique existing entries."""
    combined: list[str] = []
    seen: set[str] = set()

    for raw_value in (required_pythonpath, existing_pythonpath):
        for entry in str(raw_value or "").split(os.pathsep):
            normalized = entry.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            combined.append(normalized)

    return os.pathsep.join(combined)


def build_validated_op25_command(project_root: Path) -> ValidatedOp25Command | None:
    """Build an OP25 command from the validated probe marker, if present."""

    marker = _marker_path(project_root)
    if not marker.exists():
        return None

    values = _read_marker(marker)
    missing = [key for key in REQUIRED_MARKER_FIELDS if not values.get(key)]
    if missing:
        raise LaunchConfigError(f"validated OP25 marker is missing required fields: {', '.join(missing)}")

    try:
        values["P25_VALIDATED_RX_ARGS"] = validate_op25_device_args(values.get("P25_VALIDATED_RX_ARGS", ""))
    except ValueError as exc:
        raise LaunchConfigError(str(exc)) from exc

    try:
        values["P25_VALIDATED_RX_ARGS"] = validate_op25_device_args(values.get("P25_VALIDATED_RX_ARGS", ""))
    except ValueError as exc:
        raise LaunchConfigError(str(exc)) from exc

    app = Path(values["P25_VALIDATED_RX_APP"])
    cwd = Path(values["P25_VALIDATED_RX_APP_DIR"])
    trunk_tsv = Path(values["P25_VALIDATED_RX_TRUNK_TSV"])

    if not app.exists():
        raise LaunchConfigError(f"validated OP25 app does not exist: {app}")
    if not cwd.is_dir():
        raise LaunchConfigError(f"validated OP25 app directory does not exist: {cwd}")
    if not trunk_tsv.exists():
        raise LaunchConfigError(f"validated OP25 trunk TSV does not exist: {trunk_tsv}")

    command = [
        str(app),
        "--args",
        values["P25_VALIDATED_RX_ARGS"],
        "-S",
        values["P25_VALIDATED_RX_SAMPLE_RATE"],
        "-q",
        values["P25_VALIDATED_RX_PPM"],
        "-N",
        values["P25_VALIDATED_RX_GAIN"],
        "-T",
        str(trunk_tsv),
        "-V",
        "-2",
    ]

    terminal = values.get("P25_VALIDATED_RX_TERMINAL", "").strip()
    if terminal:
        command.extend(["-l", terminal])

    crypt_behavior = values.get("P25_VALIDATED_RX_CRYPT_BEHAVIOR", "").strip()
    if crypt_behavior:
        command.extend(["--crypt-behavior", crypt_behavior])

    command = _add_raw_audio_udp_args(command, values)

    command = ensure_op25_udp_audio_args(command)

    env = os.environ.copy()
    env["PYTHONPATH"] = prepend_pythonpath(
        values["P25_VALIDATED_RX_PYTHONPATH"],
        env.get("PYTHONPATH", ""),
    )

    return ValidatedOp25Command(
        command=command,
        cwd=str(cwd),
        env=env,
        marker_path=str(marker),
        app=str(app),
        device_args=values["P25_VALIDATED_RX_ARGS"],
        trunk_tsv=str(trunk_tsv),
        pythonpath=values["P25_VALIDATED_RX_PYTHONPATH"],
        report=values.get("P25_VALIDATED_RX_REPORT", ""),
        log=values.get("P25_VALIDATED_RX_LOG", ""),
    )

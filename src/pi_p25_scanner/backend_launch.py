"""Validated OP25 backend launch helpers.

This module intentionally only consumes a command marker produced by the
bounded Pi-side live command probe. It does not guess OP25 command lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


MARKER_RELATIVE_PATH = Path("runtime") / "settings" / "op25_validated_rx_command.env"


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


def validated_command_marker_metadata(project_root: Path) -> dict[str, object]:
    path = _marker_path(project_root)
    return {
        "source": "validated_marker",
        "exists": path.exists(),
        "validated": False,
        "path": str(path),
    }


def build_validated_op25_command(project_root: Path) -> ValidatedOp25Command | None:
    """Build an OP25 command from the validated probe marker, if present."""

    marker = _marker_path(project_root)
    if not marker.exists():
        return None

    values = _read_marker(marker)
    required = [
        "P25_VALIDATED_RX_APP",
        "P25_VALIDATED_RX_APP_DIR",
        "P25_VALIDATED_RX_PYTHONPATH",
        "P25_VALIDATED_RX_ARGS",
        "P25_VALIDATED_RX_SAMPLE_RATE",
        "P25_VALIDATED_RX_GAIN",
        "P25_VALIDATED_RX_PPM",
        "P25_VALIDATED_RX_TRUNK_TSV",
    ]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise LaunchConfigError(f"validated OP25 marker is missing required fields: {', '.join(missing)}")

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

    env = os.environ.copy()
    env["PYTHONPATH"] = values["P25_VALIDATED_RX_PYTHONPATH"]

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

"""Decoder-engine discovery for PI P25 Scanner."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Op25Capability:
    engine: str = "op25"
    installed: bool = False
    command: str = ""
    candidates: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    phase_ii_support: str = "unknown_until_live_decoder_validation"
    start_mode: str = "disabled_until_command_template_is_configured"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _candidate_paths() -> list[str]:
    home = Path.home()
    paths = [
        "op25_rx.py",
        "rx.py",
        "multi_rx.py",
        "/usr/local/bin/op25_rx.py",
        "/usr/local/bin/rx.py",
        "/usr/local/bin/multi_rx.py",
        str(home / "op25" / "op25" / "gr-op25_repeater" / "apps" / "rx.py"),
        str(home / "op25" / "op25" / "gr-op25_repeater" / "apps" / "multi_rx.py"),
        "/usr/src/op25/op25/gr-op25_repeater/apps/rx.py",
        "/usr/src/op25/op25/gr-op25_repeater/apps/multi_rx.py",
        "/opt/op25/op25/gr-op25_repeater/apps/rx.py",
        "/opt/op25/op25/gr-op25_repeater/apps/multi_rx.py",
    ]
    configured = os.environ.get("P25_SCANNER_OP25_COMMAND", "").strip()
    if configured:
        paths.insert(0, configured)
    return paths


def discover_op25() -> Op25Capability:
    found: list[str] = []
    seen: set[str] = set()
    for candidate in _candidate_paths():
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if not resolved:
            continue
        path = Path(resolved).expanduser()
        if path.exists():
            found.append(str(path))

    capability = Op25Capability(installed=bool(found), candidates=found)
    if found:
        capability.command = found[0]
        if Path(found[0]).name == "rx.py":
            capability.warnings.append("generic rx.py found; confirm this is the OP25 rx.py before enabling live start")
        capability.start_mode = os.environ.get(
            "P25_SCANNER_OP25_COMMAND_TEMPLATE",
            "disabled_until_command_template_is_configured",
        )
    else:
        capability.warnings.append("OP25 command not found in PATH or common install locations")
    return capability


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover OP25 decoder capability")
    parser.add_argument("--json", action="store_true", help="print JSON output")
    args = parser.parse_args()
    capability = discover_op25()
    if args.json:
        print(json.dumps(capability.to_dict(), indent=2, sort_keys=True))
    else:
        if capability.installed:
            print(f"OP25 candidate: {capability.command}")
        else:
            print("OP25 candidate: not found")
        for warning in capability.warnings:
            print(f"WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

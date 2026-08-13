"""Generate OP25-compatible runtime files from PI P25 Scanner JSON config.

The generated files are intentionally kept under runtime/op25/ so they can be
inspected and corrected during early hardware validation without changing the
version-controlled source config.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config_model import DEFAULT_CONFIG_PATH, ConfigError, P25System, hz_to_mhz_string, load_project_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "runtime" / "op25"
TRUNK_HEADER = [
    "Sysname",
    "Control Channel List",
    "Offset",
    "NAC",
    "Modulation",
    "TGID Tags File",
    "Whitelist",
    "Blacklist",
    "Center Frequency",
]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("_") or "p25_system"


def op25_nac_value(system: P25System) -> str:
    if system.nac is None or system.nac == "":
        return "0"
    if isinstance(system.nac, int):
        return hex(system.nac)
    return str(system.nac)


# V0.4H5_BLOCKED_TALKGROUP_BLACKLIST
BLOCKED_TALKGROUP_LABEL_TOKENS = (
    "encrypted",
    "encrypt",
    "ciphertxt",
    "cipher",
    "algid",
    "blocked",
    "block",
    "skip",
    "skipped",
    "mute",
    "muted",
    "no audio",
    "noaudio",
)


def is_blocked_talkgroup(tg: Any) -> bool:
    """Return True when a TG should be excluded from active audio/OP25 whitelist."""

    try:
        if not bool(getattr(tg, "enabled", True)):
            return True
        label = str(getattr(tg, "label", "") or "").lower()
        return any(token in label for token in BLOCKED_TALKGROUP_LABEL_TOKENS)
    except Exception:
        return False


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


@dataclass(slots=True)
class GeneratedOp25Config:
    output_dir: str
    trunk_tsv: str
    systems: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_op25_configs(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> GeneratedOp25Config:
    config = load_project_config(config_path)
    enabled_systems = config.enabled_systems
    if not enabled_systems:
        raise ConfigError("no enabled P25 systems available for OP25 config generation")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trunk_rows = ["\t".join(TRUNK_HEADER)]
    manifest_systems: list[dict[str, Any]] = []
    warnings: list[str] = []

    for system in enabled_systems:
        system_slug = safe_name(system.name)
        tags_file = output / f"{system_slug}_talkgroups.tsv"
        whitelist_file = output / f"{system_slug}_whitelist.tsv"
        blacklist_file = output / f"{system_slug}_blacklist.tsv"

        clear_tgs = [tg for tg in system.talkgroups if bool(getattr(tg, "enabled", True)) and not is_blocked_talkgroup(tg)]
        blocked_tgs = [tg for tg in system.talkgroups if is_blocked_talkgroup(tg)]
        if not clear_tgs:
            warnings.append(f"system {system.name!r} has no clear enabled talkgroups; whitelist will be empty")
        if blocked_tgs:
            warnings.append(f"system {system.name!r} blacklisted {len(blocked_tgs)} disabled/encrypted/blocked talkgroups")

        tag_lines = [f"{tg.tgid}\t{tg.label or tg.tgid}" for tg in system.talkgroups]
        whitelist_lines = [str(tg.tgid) for tg in clear_tgs]
        blacklist_lines = [str(tg.tgid) for tg in blocked_tgs]
        write_lines(tags_file, tag_lines or [])
        write_lines(whitelist_file, whitelist_lines or [])
        write_lines(blacklist_file, blacklist_lines or [])

        cc_list = ",".join(hz_to_mhz_string(freq) for freq in system.control_channels_hz)
        row = [
            system.name,
            cc_list,
            "0",
            op25_nac_value(system),
            system.modulation or "CQPSK",
            str(tags_file.resolve()),
            str(whitelist_file.resolve()),
            str(blacklist_file.resolve()),
            "0",
        ]
        trunk_rows.append("\t".join(row))
        manifest_systems.append(
            {
                "name": system.name,
                "site": system.site,
                "control_channels_hz": system.control_channels_hz,
                "preferred_control_channel_hz": system.preferred_control_channel_hz,
                "control_channel_plan": [
                    {
                        "frequency_hz": freq,
                        "frequency_mhz": hz_to_mhz_string(freq),
                        "role": "preferred" if freq == system.preferred_control_channel_hz else "alternate",
                    }
                    for freq in system.control_channels_hz
                ],
                "control_channels_mhz": [hz_to_mhz_string(freq) for freq in system.control_channels_hz],
                "enabled_talkgroups": [tg.tgid for tg in clear_tgs],
                "blocked_talkgroups": [tg.tgid for tg in blocked_tgs],
                "talkgroup_count": len(clear_tgs),
                "blocked_talkgroup_count": len(blocked_tgs),
                "tags_file": str(tags_file.resolve()),
                "whitelist_file": str(whitelist_file.resolve()),
                "blacklist_file": str(blacklist_file.resolve()),
                "nac": op25_nac_value(system),
                "modulation": system.modulation or "CQPSK",
                "control_demod_type": system.control_demod_type,
            }
        )

    trunk_tsv = output / "trunk.tsv"
    write_lines(trunk_tsv, trunk_rows)
    manifest = GeneratedOp25Config(
        output_dir=str(output.resolve()),
        trunk_tsv=str(trunk_tsv.resolve()),
        systems=manifest_systems,
        warnings=warnings,
    )
    (output / "manifest.json").write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate OP25 runtime files from PI P25 Scanner config")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="P25 scanner JSON config path")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for generated OP25 files")
    parser.add_argument("--json", action="store_true", help="Print machine-readable manifest JSON")
    args = parser.parse_args(argv)

    try:
        manifest = generate_op25_configs(args.config, args.output)
    except ConfigError as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.json:
        print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Wrote OP25 trunk config: {manifest.trunk_tsv}")
        for warning in manifest.warnings:
            print(f"WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

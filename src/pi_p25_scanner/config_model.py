"""Configuration model for PI P25 Scanner.

The project config is intentionally small. It is designed to drive a minimal
P25 trunk-following wrapper without tying the web app to a specific decoder
engine's native config files.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "p25_systems.example.json"


class ConfigError(ValueError):
    """Raised when a P25 scanner config is not usable."""


def frequency_to_hz(value: Any) -> int:
    """Normalize a frequency value to integer Hz.

    Values below 10,000 are treated as MHz, so 851.0125 becomes 851012500 Hz.
    Larger numeric values are treated as Hz. Strings may include MHz/Hz labels,
    commas, or underscores.
    """

    if isinstance(value, bool):
        raise ConfigError(f"invalid frequency value: {value!r}")
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().lower()
        cleaned = cleaned.replace("mhz", "").replace("hz", "")
        cleaned = cleaned.replace(",", "").replace("_", "")
        cleaned = re.sub(r"\s+", "", cleaned)
        if not cleaned:
            raise ConfigError("empty frequency value")
        try:
            numeric = float(cleaned)
        except ValueError as exc:
            raise ConfigError(f"invalid frequency value: {value!r}") from exc
    else:
        raise ConfigError(f"invalid frequency value: {value!r}")

    if numeric <= 0:
        raise ConfigError(f"frequency must be positive: {value!r}")
    if numeric < 10000:
        return int(round(numeric * 1_000_000))
    return int(round(numeric))


def hz_to_mhz_string(value_hz: int) -> str:
    """Format integer Hz as an OP25-friendly MHz string."""

    return f"{value_hz / 1_000_000:.6f}".rstrip("0").rstrip(".")


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return bool(value)


@dataclass(slots=True)
class Talkgroup:
    tgid: int
    label: str = ""
    enabled: bool = True

    @classmethod
    def from_config(cls, item: Any) -> "Talkgroup":
        if isinstance(item, int):
            return cls(tgid=item, label=str(item), enabled=True)
        if not isinstance(item, dict):
            raise ConfigError(f"talkgroup entry must be int or object: {item!r}")
        try:
            tgid = int(item["tgid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"talkgroup entry missing valid tgid: {item!r}") from exc
        return cls(
            tgid=tgid,
            label=str(item.get("label") or tgid),
            enabled=_enabled(item.get("enabled"), True),
        )


@dataclass(slots=True)
class ReceiverRole:
    rtl_serial: str = ""
    gain_db: float | None = 40.2
    ppm: int = 0

    @classmethod
    def from_config(cls, item: Any) -> "ReceiverRole":
        if not isinstance(item, dict):
            item = {}
        gain = item.get("gain_db", 40.2)
        return cls(
            rtl_serial=str(item.get("rtl_serial") or ""),
            gain_db=None if gain is None or gain == "" else float(gain),
            ppm=int(item.get("ppm") or 0),
        )


@dataclass(slots=True)
class P25System:
    name: str
    enabled: bool = True
    mode: str = "p25_trunked"
    site: str = ""
    control_channels_hz: list[int] = field(default_factory=list)
    voice_channels_hz: list[int] = field(default_factory=list)
    talkgroups: list[Talkgroup] = field(default_factory=list)
    receiver_roles: dict[str, ReceiverRole] = field(default_factory=dict)
    decoder: dict[str, Any] = field(default_factory=dict)
    nac: str | int | None = None
    modulation: str = "CQPSK"

    @classmethod
    def from_config(cls, item: dict[str, Any]) -> "P25System":
        if not isinstance(item, dict):
            raise ConfigError("system entry must be an object")
        name = str(item.get("name") or "Unnamed P25 System")
        control_channels = [frequency_to_hz(value) for value in item.get("control_channels_hz", [])]
        voice_channels = [frequency_to_hz(value) for value in item.get("voice_channels_hz", [])]
        if not control_channels:
            raise ConfigError(f"system {name!r} must define at least one control channel")

        tg_items = item.get("talkgroups", [])
        talkgroups = [Talkgroup.from_config(tg) for tg in tg_items]
        roles_raw = item.get("receiver_roles", {}) if isinstance(item.get("receiver_roles", {}), dict) else {}
        roles = {
            "p25_control": ReceiverRole.from_config(roles_raw.get("p25_control", {})),
            "p25_voice": ReceiverRole.from_config(roles_raw.get("p25_voice", {})),
        }
        return cls(
            name=name,
            enabled=_enabled(item.get("enabled"), True),
            mode=str(item.get("mode") or "p25_trunked"),
            site=str(item.get("site") or ""),
            control_channels_hz=control_channels,
            voice_channels_hz=voice_channels,
            talkgroups=talkgroups,
            receiver_roles=roles,
            decoder=dict(item.get("decoder", {}) if isinstance(item.get("decoder", {}), dict) else {}),
            nac=item.get("nac"),
            modulation=str(item.get("modulation") or "CQPSK"),
        )

    @property
    def enabled_talkgroups(self) -> list[Talkgroup]:
        return [tg for tg in self.talkgroups if tg.enabled]

    def to_status_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["control_channels_mhz"] = [hz_to_mhz_string(freq) for freq in self.control_channels_hz]
        data["voice_channels_mhz"] = [hz_to_mhz_string(freq) for freq in self.voice_channels_hz]
        return data


@dataclass(slots=True)
class ProjectConfig:
    schema_version: int
    systems: list[P25System]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProjectConfig":
        if not isinstance(payload, dict):
            raise ConfigError("top-level config must be an object")
        systems_raw = payload.get("systems", [])
        if not isinstance(systems_raw, list):
            raise ConfigError("systems must be a list")
        systems = [P25System.from_config(item) for item in systems_raw]
        if not systems:
            raise ConfigError("at least one P25 system is required")
        return cls(schema_version=int(payload.get("schema_version") or 1), systems=systems)

    @property
    def enabled_systems(self) -> list[P25System]:
        return [system for system in self.systems if system.enabled]

    def first_enabled_system(self) -> P25System:
        enabled = self.enabled_systems
        if not enabled:
            raise ConfigError("no enabled P25 systems in config")
        return enabled[0]


def load_project_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ProjectConfig:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config JSON invalid: {config_path}: {exc}") from exc
    return ProjectConfig.from_dict(payload)

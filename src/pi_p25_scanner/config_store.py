"""Runtime config storage helpers for PI P25 Scanner."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .config_model import DEFAULT_CONFIG_PATH, ConfigError, ProjectConfig, load_project_config
from .rtl_serial_guard import enforce_config_payload_rtl_serial_pool  # V0.5E rtl-serial-pool-0000025X

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "p25_systems.json"
LOCAL_TEMPLATE_PATH = PROJECT_ROOT / "config" / "p25_systems.local.example.json"
NAMED_CONFIG_DIR = PROJECT_ROOT / "runtime" / "settings" / "configs"
CONFIG_BACKUP_LIMIT = 50


def resolve_config_path() -> Path:
    """Return the active config path using env, runtime-local, then example fallback."""

    import os

    env_path = os.environ.get("P25_SCANNER_CONFIG", "").strip()
    if env_path:
        return Path(env_path)
    if RUNTIME_CONFIG_PATH.exists():
        return RUNTIME_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def config_source_for_path(path: Path) -> str:
    resolved = path.resolve()
    if resolved == RUNTIME_CONFIG_PATH.resolve():
        return "runtime_local"
    if resolved == DEFAULT_CONFIG_PATH.resolve():
        return "source_example"
    if resolved == LOCAL_TEMPLATE_PATH.resolve():
        return "source_local_template"
    return "environment"


def active_config_metadata() -> dict[str, Any]:
    path = resolve_config_path()
    return {
        "path": str(path),
        "source": config_source_for_path(path),
        "exists": path.exists(),
        "writable_runtime_path": str(RUNTIME_CONFIG_PATH),
        "runtime_config_exists": RUNTIME_CONFIG_PATH.exists(),
        "named_config_dir": str(NAMED_CONFIG_DIR),
        "named_config_count": named_config_count(),
    }


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config JSON invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("top-level config must be an object")
    return payload


def read_active_config_payload() -> tuple[dict[str, Any], Path]:
    path = resolve_config_path()
    return read_json_file(path), path


def validate_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = enforce_config_payload_rtl_serial_pool(payload, mutate=False)  # V0.5E rtl-serial-pool-0000025X
    config = ProjectConfig.from_dict(payload)
    first = config.first_enabled_system()
    return {
        "schema_version": config.schema_version,
        "system_count": len(config.systems),
        "enabled_system_count": len(config.enabled_systems),
        "first_enabled_system": first.to_status_dict(),
    }


def ensure_runtime_config(force: bool = False) -> dict[str, Any]:
    """Create runtime/settings/p25_systems.json from a checked-in template."""

    if RUNTIME_CONFIG_PATH.exists() and not force:
        payload = read_json_file(RUNTIME_CONFIG_PATH)
        validation = validate_config_payload(payload)
        return {"created": False, "overwritten": False, "config_path": str(RUNTIME_CONFIG_PATH), "validation": validation}

    source = LOCAL_TEMPLATE_PATH if LOCAL_TEMPLATE_PATH.exists() else DEFAULT_CONFIG_PATH
    payload = read_json_file(source)
    payload = enforce_config_payload_rtl_serial_pool(payload, mutate=False)  # V0.5E rtl-serial-pool-0000025X
    validation = validate_config_payload(payload)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "created": True,
        "overwritten": force,
        "source_path": str(source),
        "config_path": str(RUNTIME_CONFIG_PATH),
        "validation": validation,
    }


def rotate_config_backups(
    backup_dir: Path,
    limit: int = CONFIG_BACKUP_LIMIT,
) -> list[Path]:
    """Delete oldest runtime config backups beyond the retention limit."""
    directory = Path(backup_dir)
    if limit < 1 or not directory.exists():
        return []

    backups = sorted(
        (
            candidate
            for candidate in directory.glob("p25_systems_*.json")
            if candidate.is_file()
        ),
        key=lambda candidate: (
            candidate.stat().st_mtime_ns,
            candidate.name,
        ),
        reverse=True,
    )

    removed: list[Path] = []
    for candidate in backups[limit:]:
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        removed.append(candidate)

    return removed


def write_runtime_config(payload: dict[str, Any], backup: bool = True) -> dict[str, Any]:
    """Validate and write a runtime-local config file."""

    payload = enforce_config_payload_rtl_serial_pool(payload, mutate=False)  # V0.5E rtl-serial-pool-0000025X
    validation = validate_config_payload(payload)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and RUNTIME_CONFIG_PATH.exists():
        backup_dir = RUNTIME_CONFIG_PATH.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = backup_dir / f"p25_systems_{stamp}.json"
        shutil.copy2(RUNTIME_CONFIG_PATH, backup_path)
        rotate_config_backups(backup_dir)
    RUNTIME_CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "config_path": str(RUNTIME_CONFIG_PATH),
        "backup_path": str(backup_path) if backup_path else None,
        "validation": validation,
    }


def load_active_project_config() -> tuple[ProjectConfig, Path]:
    path = resolve_config_path()
    return load_project_config(path), path


def _named_config_slug(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip()).strip("-._")
    if not clean:
        raise ConfigError("named config name is required")
    return clean[:80]


def _named_config_path(name_or_id: str) -> Path:
    return NAMED_CONFIG_DIR / f"{_named_config_slug(name_or_id)}.json"


def _unwrap_named_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(payload.get("config"), dict):
        return str(payload.get("name") or payload.get("id") or ""), payload["config"]
    return "", payload


def _normalized_analog_channels(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for role in ("analog_2m", "analog_70cm"):
        channels = value.get(role)
        if not isinstance(channels, list):
            continue
        result[role] = [dict(item) for item in channels if isinstance(item, dict)]
    return result


def _current_analog_channels() -> dict[str, list[dict[str, Any]]]:
    from . import analog_channels

    config = analog_channels.load_config(
        analog_channels.DEFAULT_CONFIG_PATH,
        analog_channels.DEFAULT_TEMPLATE_PATH,
    )
    workers = config.get("workers") if isinstance(config, dict) else {}
    return _normalized_analog_channels(
        {
            role: (workers.get(role) or {}).get("channels", [])
            for role in ("analog_2m", "analog_70cm")
        }
    )


def _apply_analog_channels(
    channels_by_role: dict[str, list[dict[str, Any]]],
    profile_name: str,
) -> dict[str, Any]:
    from . import analog_channels

    config = analog_channels.load_config(
        analog_channels.DEFAULT_CONFIG_PATH,
        analog_channels.DEFAULT_TEMPLATE_PATH,
    )
    workers = config["workers"]
    for role, channels in channels_by_role.items():
        if role not in workers:
            continue
        workers[role]["channels"] = json.loads(json.dumps(channels))
        workers[role]["enabled"] = bool(channels)
    config["source"] = "named_profile"
    config["last_import"] = {
        "filename": profile_name,
        "imported_utc": time.time(),
        "replace_mode": "named_profile",
        "row_count": sum(len(items) for items in channels_by_role.values()),
        "roles": sorted(channels_by_role),
    }
    backup = analog_channels.write_config_payload(
        config,
        analog_channels.DEFAULT_CONFIG_PATH,
    )
    return {
        "applied": True,
        "backup_path": str(backup) if backup else None,
        "channel_counts": {
            role: len((workers.get(role) or {}).get("channels", []))
            for role in ("analog_2m", "analog_70cm")
        },
    }


def named_config_count() -> int:
    if not NAMED_CONFIG_DIR.exists():
        return 0
    return sum(1 for path in NAMED_CONFIG_DIR.glob("*.json") if path.is_file())


def list_named_configs(include_invalid: bool = False) -> dict[str, Any]:
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    configs: list[dict[str, Any]] = []
    for path in sorted(NAMED_CONFIG_DIR.glob("*.json"), key=lambda p: p.name.lower()):
        item: dict[str, Any] = {
            "id": path.stem,
            "slug": path.stem,
            "name": path.stem,
            "path": str(path),
            "valid": False,
            "updated_utc": path.stat().st_mtime,
        }
        try:
            stored = read_json_file(path)
            stored_name, config_payload = _unwrap_named_payload(stored)
            item["name"] = stored_name or path.stem
            item["validation"] = validate_config_payload(config_payload)
            analog = _normalized_analog_channels(stored.get("analog_channels"))
            item["analog_channel_counts"] = {
                role: len(channels) for role, channels in analog.items()
            }
            item["valid"] = True
        except Exception as exc:
            item["error"] = str(exc)
        if item["valid"] or include_invalid:
            configs.append(item)
    return {"ok": True, "config_dir": str(NAMED_CONFIG_DIR), "configs": configs, "count": len(configs)}


def save_named_config(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if payload is None:
        payload, _path = read_active_config_payload()
    if not isinstance(payload, dict):
        raise ConfigError("named config payload must be an object")
    payload = enforce_config_payload_rtl_serial_pool(payload, mutate=False)  # V0.5E rtl-serial-pool-0000025X
    validation = validate_config_payload(payload)
    slug = _named_config_slug(name)
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _named_config_path(slug)
    body = {
        "name": str(name).strip() or slug,
        "id": slug,
        "saved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "config": payload,
        "analog_channels": _current_analog_channels(),
    }
    backup_path = None
    if path.exists():
        backup_dir = NAMED_CONFIG_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = backup_dir / f"{path.stem}_{stamp}.json"
        shutil.copy2(path, backup_path)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "id": slug,
        "slug": slug,
        "name": body["name"],
        "path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "validation": validation,
    }


def load_named_config(name_or_id: str, apply_to_runtime: bool = True, backup: bool = True) -> dict[str, Any]:
    path = _named_config_path(name_or_id)
    stored = read_json_file(path)
    stored_name, config_payload = _unwrap_named_payload(stored)
    validation = validate_config_payload(config_payload)
    result: dict[str, Any] = {
        "ok": True,
        "id": path.stem,
        "slug": path.stem,
        "name": stored_name or path.stem,
        "path": str(path),
        "validation": validation,
    }
    if apply_to_runtime:
        result["applied"] = write_runtime_config(config_payload, backup=backup)
        analog = _normalized_analog_channels(stored.get("analog_channels"))
        if analog:
            result["analog"] = _apply_analog_channels(
                analog,
                stored_name or path.stem,
            )
    return result


def read_named_config_bundle(name_or_id: str) -> dict[str, Any]:
    """Return one validated named profile for an explicit export action."""

    path = _named_config_path(name_or_id)
    stored = read_json_file(path)
    stored_name, config_payload = _unwrap_named_payload(stored)
    validate_config_payload(config_payload)
    return {
        "id": path.stem,
        "name": stored_name or path.stem,
        "config": config_payload,
        "analog_channels": _normalized_analog_channels(
            stored.get("analog_channels")
        ),
    }


def delete_named_config(name_or_id: str) -> dict[str, Any]:
    path = _named_config_path(name_or_id)
    if not path.exists():
        raise ConfigError(f"named config not found: {name_or_id}")
    backup_dir = NAMED_CONFIG_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    recoverable_path = backup_dir / f"{path.stem}_deleted_{stamp}.json"
    shutil.move(path, recoverable_path)
    return {
        "ok": True,
        "id": path.stem,
        "slug": path.stem,
        "deleted": True,
        "path": str(path),
        "recoverable_path": str(recoverable_path),
    }

"""Named local configuration profile runtime helpers for PI P25 Scanner.

These helpers intentionally store user-created profiles below runtime/settings/configs
so field configs remain Pi-local and are not committed as source templates.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .config_model import DEFAULT_CONFIG_PATH, ConfigError, ProjectConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "p25_systems.json"
NAMED_CONFIG_DIR = PROJECT_ROOT / "runtime" / "settings" / "configs"
BACKUP_DIR = PROJECT_ROOT / "runtime" / "settings" / "backups"


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "saved_config"


def _display_name_from_slug(value: str) -> str:
    text = value.replace("_", " ").replace("-", " ").strip()
    return text or value


def _active_config_path() -> Path:
    env_path = os.environ.get("P25_SCANNER_CONFIG", "").strip()
    if env_path:
        return Path(env_path)
    if RUNTIME_CONFIG_PATH.exists():
        return RUNTIME_CONFIG_PATH
    return DEFAULT_CONFIG_PATH


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"named config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"named config JSON invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"named config top-level JSON must be an object: {path}")
    return payload


def _validate(payload: dict[str, Any]) -> dict[str, Any]:
    config = ProjectConfig.from_dict(payload)
    first = config.first_enabled_system()
    return {
        "schema_version": config.schema_version,
        "system_count": len(config.systems),
        "enabled_system_count": len(config.enabled_systems),
        "first_enabled_system": first.to_status_dict(),
    }


def _entry_for_path(path: Path, include_invalid: bool = False) -> dict[str, Any] | None:
    entry: dict[str, Any] = {
        "name": _display_name_from_slug(path.stem),
        "slug": path.stem,
        "filename": path.name,
        "path": str(path),
        "valid": False,
        "validation": {},
        "error": "",
        "updated_utc": path.stat().st_mtime if path.exists() else None,
    }
    try:
        payload = _read_json(path)
        validation = _validate(payload)
        systems = payload.get("systems") if isinstance(payload, dict) else None
        if isinstance(systems, list) and systems:
            first = systems[0]
            if isinstance(first, dict) and str(first.get("name", "")).strip():
                entry["system_name"] = str(first.get("name", "")).strip()
        entry["valid"] = True
        entry["validation"] = validation
    except Exception as exc:  # noqa: BLE001 - return invalid profiles when requested
        entry["error"] = str(exc)
        if not include_invalid:
            return None
    return entry


def list_named_configs(include_invalid: bool = False) -> dict[str, Any]:
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    configs: list[dict[str, Any]] = []
    for path in sorted(NAMED_CONFIG_DIR.glob("*.json"), key=lambda p: p.name.lower()):
        entry = _entry_for_path(path, include_invalid=include_invalid)
        if entry is not None:
            configs.append(entry)
    return {
        "ok": True,
        "config_dir": str(NAMED_CONFIG_DIR),
        "configs": configs,
        "count": len(configs),
    }


def named_config_count(include_invalid: bool = True) -> int:
    return int(list_named_configs(include_invalid=include_invalid).get("count", 0))


def _name_from_payload(payload: dict[str, Any]) -> str:
    for key in ("name", "config_name", "configName", "saved_name", "savedConfigName", "profile", "slug"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ConfigError("named config request requires a name")


def _path_for_name(name: str) -> Path:
    return NAMED_CONFIG_DIR / f"{_slug(name)}.json"


def _find_path(payload: dict[str, Any]) -> Path:
    name = _name_from_payload(payload)
    direct = _path_for_name(name)
    if direct.exists():
        return direct
    wanted = name.strip().lower()
    wanted_slug = _slug(name).lower()
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for path in NAMED_CONFIG_DIR.glob("*.json"):
        if path.stem.lower() in (wanted, wanted_slug) or path.name.lower() == wanted:
            return path
    raise ConfigError(f"named config not found: {name}")


def _write_runtime_config(payload: dict[str, Any], backup: bool = True) -> dict[str, Any]:
    validation = _validate(payload)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and RUNTIME_CONFIG_PATH.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = BACKUP_DIR / f"p25_systems_{stamp}.json"
        shutil.copy2(RUNTIME_CONFIG_PATH, backup_path)
    RUNTIME_CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "config_path": str(RUNTIME_CONFIG_PATH),
        "backup_path": str(backup_path) if backup_path else None,
        "validation": validation,
    }


def save_named_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = payload or {}
    if not isinstance(request, dict):
        raise ConfigError("named config save payload must be an object")
    name = _name_from_payload(request)
    config_payload = request.get("config")
    if config_payload is None:
        config_payload = _read_json(_active_config_path())
    if not isinstance(config_payload, dict):
        raise ConfigError("named config save requires a config object")
    validation = _validate(config_payload)
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for_name(name)
    backup_path = None
    if path.exists():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = path.with_name(f"{path.stem}_{stamp}.bak.json")
        shutil.copy2(path, backup_path)
    path.write_text(json.dumps(config_payload, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "name": name,
        "slug": path.stem,
        "path": str(path),
        "backup_path": str(backup_path) if backup_path else None,
        "validation": validation,
        "configs": list_named_configs(include_invalid=True).get("configs", []),
    }


def load_named_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = payload or {}
    if not isinstance(request, dict):
        raise ConfigError("named config load payload must be an object")
    path = _find_path(request)
    config_payload = _read_json(path)
    runtime_result = _write_runtime_config(config_payload, backup=True)
    return {
        "ok": True,
        "name": request.get("name") or request.get("slug") or path.stem,
        "slug": path.stem,
        "path": str(path),
        "runtime": runtime_result,
        "validation": runtime_result.get("validation", {}),
        "configs": list_named_configs(include_invalid=True).get("configs", []),
    }


def delete_named_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = payload or {}
    if not isinstance(request, dict):
        raise ConfigError("named config delete payload must be an object")
    path = _find_path(request)
    deleted = str(path)
    path.unlink()
    return {
        "ok": True,
        "deleted_path": deleted,
        "configs": list_named_configs(include_invalid=True).get("configs", []),
    }

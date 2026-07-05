"""Runtime config storage helpers for PI P25 Scanner."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .config_model import DEFAULT_CONFIG_PATH, ConfigError, ProjectConfig, load_project_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "p25_systems.json"
LOCAL_TEMPLATE_PATH = PROJECT_ROOT / "config" / "p25_systems.local.example.json"


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


def write_runtime_config(payload: dict[str, Any], backup: bool = True) -> dict[str, Any]:
    """Validate and write a runtime-local config file."""

    validation = validate_config_payload(payload)
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if backup and RUNTIME_CONFIG_PATH.exists():
        backup_dir = PROJECT_ROOT / "runtime" / "settings" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup_path = backup_dir / f"p25_systems_{stamp}.json"
        shutil.copy2(RUNTIME_CONFIG_PATH, backup_path)
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

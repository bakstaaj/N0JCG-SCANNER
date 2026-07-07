"""Runtime config storage helpers for PI P25 Scanner."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .config_model import DEFAULT_CONFIG_PATH, ConfigError, ProjectConfig, load_project_config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "p25_systems.json"
LOCAL_TEMPLATE_PATH = PROJECT_ROOT / "config" / "p25_systems.local.example.json"
NAMED_CONFIG_DIR = PROJECT_ROOT / "runtime" / "settings" / "configs"


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


def _slugify_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        raise ConfigError("named config name is required")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    if not slug:
        raise ConfigError("named config name must contain letters or numbers")
    return slug[:80]


def _named_config_path(name: str) -> Path:
    return NAMED_CONFIG_DIR / f"{_slugify_name(name)}.json"


def named_config_count() -> int:
    try:
        return int(list_named_configs(include_invalid=True).get("count", 0))
    except Exception:
        return 0


def list_named_configs(include_invalid: bool = False) -> dict[str, Any]:
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    active_path = resolve_config_path().resolve()
    items: list[dict[str, Any]] = []
    for path in sorted(NAMED_CONFIG_DIR.glob("*.json"), key=lambda p: p.name.lower()):
        item: dict[str, Any] = {
            "id": path.stem,
            "name": path.stem,
            "path": str(path),
            "active": path.resolve() == active_path,
            "mtime": path.stat().st_mtime if path.exists() else None,
            "valid": False,
        }
        try:
            payload = read_json_file(path)
            validation = validate_config_payload(payload)
            item["name"] = str(payload.get("profile_name") or payload.get("name") or path.stem)
            item["valid"] = True
            item["validation"] = validation
        except ConfigError as exc:
            item["error"] = str(exc)
        if item.get("valid") or include_invalid:
            items.append(item)
    return {
        "ok": True,
        "config_dir": str(NAMED_CONFIG_DIR),
        "count": len(items),
        "configs": items,
        "active_config": active_config_metadata_without_named_count(),
    }


def active_config_metadata_without_named_count() -> dict[str, Any]:
    path = resolve_config_path()
    return {
        "path": str(path),
        "source": config_source_for_path(path),
        "exists": path.exists(),
        "writable_runtime_path": str(RUNTIME_CONFIG_PATH),
        "runtime_config_exists": RUNTIME_CONFIG_PATH.exists(),
        "named_config_dir": str(NAMED_CONFIG_DIR),
    }


def save_named_config(name: str, payload: dict[str, Any] | None = None, apply_to_runtime: bool = False) -> dict[str, Any]:
    if payload is None:
        payload, _path = read_active_config_payload()
    if not isinstance(payload, dict):
        raise ConfigError("named config payload must be an object")
    validation = validate_config_payload(payload)
    slug = _slugify_name(name)
    path = _named_config_path(slug)
    NAMED_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    stored = dict(payload)
    stored.setdefault("profile_name", str(name).strip() or slug)
    stored["saved_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
    result: dict[str, Any] = {
        "ok": True,
        "id": slug,
        "name": stored.get("profile_name", slug),
        "path": str(path),
        "validation": validation,
    }
    if apply_to_runtime:
        result["applied"] = write_runtime_config(stored)
    return result


def load_named_config(name: str, apply_to_runtime: bool = True, backup: bool = True) -> dict[str, Any]:
    path = _named_config_path(name)
    payload = read_json_file(path)
    validation = validate_config_payload(payload)
    result: dict[str, Any] = {
        "ok": True,
        "id": path.stem,
        "name": str(payload.get("profile_name") or payload.get("name") or path.stem),
        "path": str(path),
        "config": payload,
        "validation": validation,
    }
    if apply_to_runtime:
        result["applied"] = write_runtime_config(payload, backup=backup)
    return result


def delete_named_config(name: str) -> dict[str, Any]:
    path = _named_config_path(name)
    if not path.exists():
        raise ConfigError(f"named config not found: {name}")
    path.unlink()
    return {"ok": True, "id": path.stem, "path": str(path), "deleted": True}


# Compatibility aliases from earlier named-config patch attempts.
store_named_config = save_named_config
store_save_named_config = save_named_config
store_load_named_config = load_named_config
store_delete_named_config = delete_named_config

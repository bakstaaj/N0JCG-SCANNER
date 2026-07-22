# Persistent RTL receiver-role registry and read-only hardware inventory.
#
# Receiver assignments are intentionally independent of P25/RadioReference
# profiles. Loading a named P25 configuration must not erase analog or
# external-service receiver reservations.

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "config" / "receiver_roles.example.json"
DEFAULT_ROLE_CONFIG_PATH = PROJECT_ROOT / "runtime" / "settings" / "receiver_roles.json"
DEFAULT_SYSFS_ROOT = Path("/sys/bus/usb/devices")
DEFAULT_PROCESS_ROOT = Path("/proc")
RTL_VENDOR_ID = "0bda"
RTL_PRODUCT_IDS = {"2832", "2838"}
REQUIRED_ROLES = (
    "p25_control",
    "p25_voice",
    "noaa_airband",
    "adsb_1090",
    "uat_978",
    "analog_2m",
    "analog_70cm",
)


class ReceiverInventoryError(ValueError):
    pass


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def ensure_receiver_roles_file(
    role_config_path: Path = DEFAULT_ROLE_CONFIG_PATH,
    template_path: Path = DEFAULT_TEMPLATE_PATH,
) -> dict[str, Any]:
    role_config_path = Path(role_config_path)
    if role_config_path.exists():
        return {"created": False, "path": str(role_config_path)}
    if not template_path.exists():
        raise ReceiverInventoryError(f"receiver role template missing: {template_path}")
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    validate_receiver_role_payload(payload)
    role_config_path.parent.mkdir(parents=True, exist_ok=True)
    role_config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"created": True, "path": str(role_config_path), "template": str(template_path)}


def validate_receiver_role_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReceiverInventoryError("receiver role payload must be an object")
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        raise ReceiverInventoryError("receiver role payload must contain a roles object")

    missing_roles = [name for name in REQUIRED_ROLES if name not in roles]
    if missing_roles:
        raise ReceiverInventoryError("missing required receiver roles: " + ", ".join(missing_roles))

    serial_to_roles: dict[str, list[str]] = {}
    normalized_roles: list[dict[str, Any]] = []
    for role_name, raw in roles.items():
        if not isinstance(raw, dict):
            raise ReceiverInventoryError(f"receiver role {role_name!r} must be an object")
        serial = str(raw.get("rtl_serial") or "").strip()
        if not re.fullmatch(r"\d{8}", serial):
            raise ReceiverInventoryError(
                f"receiver role {role_name!r} has invalid 8-digit rtl_serial: {serial!r}"
            )
        serial_to_roles.setdefault(serial, []).append(str(role_name))
        normalized_roles.append(
            {
                "role": str(role_name),
                "label": str(raw.get("label") or role_name),
                "service": str(raw.get("service") or ""),
                "rtl_serial": serial,
                "enabled": bool(raw.get("enabled", False)),
                "protected": bool(raw.get("protected", False)),
                "notes": str(raw.get("notes") or ""),
            }
        )

    duplicates = {serial: names for serial, names in serial_to_roles.items() if len(names) > 1}
    if duplicates:
        details = "; ".join(
            f"{serial}: {','.join(names)}"
            for serial, names in sorted(duplicates.items())
        )
        raise ReceiverInventoryError("duplicate receiver serial assignments: " + details)

    expected = int(payload.get("expected_rtl_count") or len(normalized_roles))
    if expected < len(normalized_roles):
        raise ReceiverInventoryError(
            f"expected_rtl_count {expected} is smaller than configured role count {len(normalized_roles)}"
        )
    return {
        "schema_version": int(payload.get("schema_version") or 1),
        "expected_rtl_count": expected,
        "role_count": len(normalized_roles),
        "roles": normalized_roles,
    }


def load_receiver_roles(
    role_config_path: Path = DEFAULT_ROLE_CONFIG_PATH,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    ensure_receiver_roles_file(role_config_path=role_config_path)
    path = Path(role_config_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiverInventoryError(f"receiver role file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReceiverInventoryError(f"receiver role JSON invalid: {path}: {exc}") from exc
    validation = validate_receiver_role_payload(payload)
    return payload, path, validation


def enumerate_rtl_sysfs(sysfs_root: Path = DEFAULT_SYSFS_ROOT) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    root = Path(sysfs_root)
    if not root.exists():
        return devices
    for device_path in sorted(root.iterdir(), key=lambda item: item.name):
        if not device_path.is_dir():
            continue
        vendor = _read_text(device_path / "idVendor").lower()
        product_id = _read_text(device_path / "idProduct").lower()
        if vendor != RTL_VENDOR_ID or product_id not in RTL_PRODUCT_IDS:
            continue
        devices.append(
            {
                "rtl_serial": _read_text(device_path / "serial"),
                "usb_path": device_path.name,
                "bus": _read_text(device_path / "busnum"),
                "device": _read_text(device_path / "devnum"),
                "vendor_id": vendor,
                "product_id": product_id,
                "manufacturer": _read_text(device_path / "manufacturer"),
                "product": _read_text(device_path / "product"),
                "speed_mbps": _read_text(device_path / "speed"),
            }
        )
    return devices


def scan_process_claims(
    serials: list[str],
    process_root: Path = DEFAULT_PROCESS_ROOT,
) -> dict[str, list[dict[str, Any]]]:
    claims: dict[str, list[dict[str, Any]]] = {serial: [] for serial in serials}
    root = Path(process_root)
    if not root.exists():
        return claims
    for proc_path in root.iterdir():
        if not proc_path.name.isdigit():
            continue
        try:
            raw = (proc_path / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        if not command:
            continue
        for serial in serials:
            if serial and serial in command:
                claims[serial].append({"pid": int(proc_path.name), "command": command[:1200]})
    return claims


def build_receiver_inventory(
    role_config_path: Path = DEFAULT_ROLE_CONFIG_PATH,
    sysfs_root: Path = DEFAULT_SYSFS_ROOT,
    process_root: Path = DEFAULT_PROCESS_ROOT,
) -> dict[str, Any]:
    _payload, path, validation = load_receiver_roles(role_config_path=role_config_path)
    devices = enumerate_rtl_sysfs(sysfs_root=sysfs_root)
    serials = [str(item.get("rtl_serial") or "") for item in devices]
    duplicate_present_serials = sorted(
        serial for serial in set(serials) if serial and serials.count(serial) > 1
    )
    missing_device_serial_entries = sum(1 for serial in serials if not serial)

    role_entries = validation["roles"]
    configured_serials = [entry["rtl_serial"] for entry in role_entries]
    claims = scan_process_claims(configured_serials, process_root=process_root)
    device_by_serial = {
        str(device.get("rtl_serial") or ""): device
        for device in devices
        if device.get("rtl_serial")
    }

    role_results: list[dict[str, Any]] = []
    for entry in role_entries:
        serial = entry["rtl_serial"]
        device = device_by_serial.get(serial)
        role_claims = claims.get(serial, [])
        if device is None:
            state = "missing"
        elif role_claims:
            state = "active"
        elif entry["enabled"]:
            state = "ready"
        else:
            state = "reserved"
        role_results.append(
            {
                **entry,
                "present": device is not None,
                "active": bool(role_claims),
                "state": state,
                "device": device,
                "processes": role_claims,
            }
        )

    missing_configured_serials = [
        entry["rtl_serial"] for entry in role_results if not entry["present"]
    ]
    assigned_serials = set(configured_serials)
    unassigned_serials = sorted(
        serial for serial in serials if serial and serial not in assigned_serials
    )
    expected = int(validation["expected_rtl_count"])
    warnings: list[str] = []
    if len(devices) != expected:
        warnings.append(f"expected {expected} RTL devices; found {len(devices)}")
    if missing_configured_serials:
        warnings.append(
            "configured receiver serials missing: " + ", ".join(missing_configured_serials)
        )
    if duplicate_present_serials:
        warnings.append(
            "duplicate present RTL serials: " + ", ".join(duplicate_present_serials)
        )
    if missing_device_serial_entries:
        warnings.append(f"{missing_device_serial_entries} present RTL device(s) have no serial")
    if unassigned_serials:
        warnings.append("unassigned present RTL serials: " + ", ".join(unassigned_serials))

    healthy = (
        len(devices) == expected
        and not missing_configured_serials
        and not duplicate_present_serials
        and missing_device_serial_entries == 0
        and not unassigned_serials
    )
    return {
        "ok": healthy,
        "schema_version": validation["schema_version"],
        "source": "pi_p25_scanner.receiver_inventory",
        "role_config_path": str(path),
        "expected_rtl_count": expected,
        "device_count": len(devices),
        "configured_role_count": len(role_results),
        "missing_configured_serials": missing_configured_serials,
        "duplicate_present_serials": duplicate_present_serials,
        "missing_device_serial_entries": missing_device_serial_entries,
        "unassigned_serials": unassigned_serials,
        "roles": role_results,
        "devices": devices,
        "warnings": warnings,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="pi_scanner_receiver_inventory_") as tmp:
        root = Path(tmp)
        sysfs_root = root / "sysfs"
        proc_root = root / "proc"
        role_path = root / "receiver_roles.json"
        sysfs_root.mkdir()
        proc_root.mkdir()
        template = json.loads(DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8"))
        role_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
        for index, (role_name, role) in enumerate(template["roles"].items(), start=1):
            device = sysfs_root / f"3-1.{index}"
            device.mkdir()
            (device / "idVendor").write_text("0bda\n", encoding="utf-8")
            (device / "idProduct").write_text("2838\n", encoding="utf-8")
            (device / "serial").write_text(role["rtl_serial"] + "\n", encoding="utf-8")
            (device / "busnum").write_text("3\n", encoding="utf-8")
            (device / "devnum").write_text(str(index) + "\n", encoding="utf-8")
            (device / "manufacturer").write_text("Test\n", encoding="utf-8")
            (device / "product").write_text(role_name + "\n", encoding="utf-8")
            (device / "speed").write_text("480\n", encoding="utf-8")
        result = build_receiver_inventory(
            role_config_path=role_path,
            sysfs_root=sysfs_root,
            process_root=proc_root,
        )
        if not result["ok"]:
            print(json.dumps(result, indent=2))
            print("FINAL: FAIL")
            return 1
        if result["device_count"] != 7 or result["configured_role_count"] != 7:
            print(json.dumps(result, indent=2))
            print("FINAL: FAIL")
            return 1
        print("PASS: receiver inventory self-test")
        print("FINAL: PASS")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PI-SCANNER receiver inventory")
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.ensure:
        result = ensure_receiver_roles_file()
    else:
        result = build_receiver_inventory()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""RTL-SDR serial pool guard for PI P25 Scanner.

V0.5E policy:
- P25 scanning must only use RTL-SDR devices whose serial numbers are in the
  0000025X pool.
- This keeps ADS-B or other unrelated RTL radios on the same Pi from being
  selected by OP25 or saved scanner configs.
"""

from __future__ import annotations

import copy
import re
from typing import Any

ALLOWED_RTL_SERIAL_PATTERN = r"^0000025\d$"
ALLOWED_RTL_SERIAL_RE = re.compile(ALLOWED_RTL_SERIAL_PATTERN)
DEFAULT_CONTROL_SERIAL = "00000251"
DEFAULT_VOICE_SERIAL = "00000252"
POLICY_NAME = "rtl-serial-pool-0000025X-v0.5e"


def normalize_rtl_serial(value: Any) -> str:
    """Normalize user-entered RTL serials without broadening the allowed pool.

    The newly added radio was reported as 000000252, while the intended pool was
    described as 0000025X. If a value has one extra leading zero but its last
    eight digits are in 0000025X, normalize it to the standard rtl_sdr serial
    form, e.g. 000000252 -> 00000252.
    """

    text = str(value or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D+", "", text)
    if len(digits) > 8 and ALLOWED_RTL_SERIAL_RE.fullmatch(digits[-8:]):
        return digits[-8:]
    return text


def is_allowed_rtl_serial(value: Any) -> bool:
    return bool(ALLOWED_RTL_SERIAL_RE.fullmatch(normalize_rtl_serial(value)))


def first_allowed_serial(values: list[Any], fallback: str) -> str:
    for value in values:
        normalized = normalize_rtl_serial(value)
        if is_allowed_rtl_serial(normalized):
            return normalized
    return fallback


def enforce_config_payload_rtl_serial_pool(payload: dict[str, Any], mutate: bool = False) -> dict[str, Any]:
    """Return a config payload constrained to the 0000025X RTL serial pool."""

    data = payload if mutate else copy.deepcopy(payload)
    systems = data.get("systems")
    if not isinstance(systems, list):
        return data

    for system in systems:
        if not isinstance(system, dict):
            continue
        roles = system.setdefault("receiver_roles", {})
        if not isinstance(roles, dict):
            roles = {}
            system["receiver_roles"] = roles

        control = roles.setdefault("p25_control", {})
        if not isinstance(control, dict):
            control = {}
            roles["p25_control"] = control
        voice = roles.setdefault("p25_voice", {})
        if not isinstance(voice, dict):
            voice = {}
            roles["p25_voice"] = voice

        control_serial = normalize_rtl_serial(control.get("rtl_serial"))
        voice_serial = normalize_rtl_serial(voice.get("rtl_serial"))

        if not is_allowed_rtl_serial(control_serial):
            control_serial = DEFAULT_CONTROL_SERIAL
        if not is_allowed_rtl_serial(voice_serial) or voice_serial == control_serial:
            voice_serial = DEFAULT_VOICE_SERIAL if DEFAULT_VOICE_SERIAL != control_serial else "00000253"

        control["rtl_serial"] = control_serial
        voice["rtl_serial"] = voice_serial

        policy = system.setdefault("hardware_policy", {})
        if isinstance(policy, dict):
            policy["rtl_serial_pool"] = "0000025X"
            policy["policy_name"] = POLICY_NAME
            policy["adsb_isolation"] = True

    return data


def validate_op25_device_args(device_args: str) -> str:
    """Validate OP25 --args text so an ADS-B RTL cannot be launched accidentally."""

    text = str(device_args or "").strip()
    if not text:
        return text
    match = re.search(r"(?:^|[,\s])rtl=([^,\s]+)", text)
    if not match:
        return text
    serial = normalize_rtl_serial(match.group(1))
    if not is_allowed_rtl_serial(serial):
        raise ValueError(
            f"OP25 RTL serial {match.group(1)!r} is outside the allowed P25 pool "
            "0000025X; refusing to use radios reserved for ADS-B/other services"
        )
    if serial != match.group(1):
        return text[: match.start(1)] + serial + text[match.end(1):]
    return text


def replace_or_add_op25_device_serial(device_args: str, serial: str = DEFAULT_CONTROL_SERIAL) -> str:
    serial = normalize_rtl_serial(serial)
    if not is_allowed_rtl_serial(serial):
        serial = DEFAULT_CONTROL_SERIAL
    text = str(device_args or "").strip()
    if not text:
        return f"rtl={serial}"
    if re.search(r"(?:^|[,\s])rtl=([^,\s]+)", text):
        return re.sub(r"rtl=[^,\s]+", f"rtl={serial}", text, count=1)
    sep = "," if "," in text else " "
    return f"{text}{sep}rtl={serial}"

"""Compatibility helpers for the reusable N0JCG licensing client."""

from __future__ import annotations

import re

from n0jcg_licensing.client import (
    installation_serial,
    installation_serial_from_identity,
)


_SERIAL_PATTERN = re.compile(r"^N0JCG-[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$")


def valid_installation_serial(value: str) -> bool:
    return bool(_SERIAL_PATTERN.fullmatch(str(value or "").strip().upper()))

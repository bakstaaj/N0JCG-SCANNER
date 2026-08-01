"""Coordinate the VHF and UHF systemd scanner workers with the P25 backend."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any


ANALOG_SCANNER_UNITS = (
    "pi-scanner-vhf-worker.service",
    "pi-scanner-uhf-worker.service",
)


class ScannerServiceControlError(RuntimeError):
    """Raised when the analog scanner workers cannot reach the requested state."""


class AnalogScannerServiceController:
    """Start and stop both analog workers as one scanner control group."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._runner = runner

    def status(self) -> dict[str, Any]:
        services: dict[str, str] = {}
        for unit in ANALOG_SCANNER_UNITS:
            result = self._runner(
                ["/usr/bin/systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            state = (result.stdout or result.stderr or "unknown").strip()
            services[unit] = state or "unknown"
        return {
            "vhf": services[ANALOG_SCANNER_UNITS[0]],
            "uhf": services[ANALOG_SCANNER_UNITS[1]],
            "services": services,
        }

    def start(self) -> dict[str, Any]:
        self._change_state("start")
        state = self.status()
        if state["vhf"] != "active" or state["uhf"] != "active":
            self._change_state("stop", raise_on_error=False)
            raise ScannerServiceControlError(
                "VHF/UHF scanner services did not both become active"
            )
        return state

    def stop(self) -> dict[str, Any]:
        self._change_state("stop")
        state = self.status()
        if state["vhf"] == "active" or state["uhf"] == "active":
            raise ScannerServiceControlError(
                "VHF/UHF scanner services did not both stop"
            )
        return state

    def _change_state(self, action: str, *, raise_on_error: bool = True) -> None:
        command = [
            "/usr/bin/sudo",
            "-n",
            "/usr/bin/systemctl",
            action,
            *ANALOG_SCANNER_UNITS,
        ]
        result = self._runner(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode == 0 or not raise_on_error:
            return
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise ScannerServiceControlError(
            f"could not {action} VHF/UHF scanner services: {detail}"
        )

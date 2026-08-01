import subprocess
import threading
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path

import pytest

from pi_p25_scanner.scanner_service_control import (
    ANALOG_SCANNER_UNITS,
    AnalogScannerServiceController,
    ScannerServiceControlError,
)
from pi_p25_scanner.backend import ScannerManager, ScannerStatus


ROOT = Path(__file__).resolve().parents[1]


class FakeSystemctl:
    def __init__(self, *, fail_action: str = "") -> None:
        self.active = {unit: False for unit in ANALOG_SCANNER_UNITS}
        self.commands: list[list[str]] = []
        self.fail_action = fail_action

    def __call__(self, command, **_kwargs):
        self.commands.append(list(command))
        action = command[3] if command[0].endswith("sudo") else command[1]
        if action == self.fail_action:
            return subprocess.CompletedProcess(command, 1, "", "injected failure")
        if action in ("start", "stop"):
            value = action == "start"
            for unit in command[4:]:
                self.active[unit] = value
            return subprocess.CompletedProcess(command, 0, "", "")
        unit = command[2]
        state = "active" if self.active[unit] else "inactive"
        return subprocess.CompletedProcess(command, 0 if self.active[unit] else 3, state + "\n", "")


def test_controller_starts_and_stops_both_analog_scanners() -> None:
    runner = FakeSystemctl()
    controller = AnalogScannerServiceController(runner=runner)

    started = controller.start()
    stopped = controller.stop()
    assert started["vhf"] == "active"
    assert started["uhf"] == "active"
    assert stopped["vhf"] == "inactive"
    assert stopped["uhf"] == "inactive"

    state_commands = [command for command in runner.commands if command[0].endswith("sudo")]
    assert state_commands == [
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", *ANALOG_SCANNER_UNITS],
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", *ANALOG_SCANNER_UNITS],
    ]


def test_controller_reports_systemctl_failure() -> None:
    controller = AnalogScannerServiceController(runner=FakeSystemctl(fail_action="start"))

    with pytest.raises(ScannerServiceControlError, match="injected failure"):
        controller.start()


class FakeAnalogController:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1
        return {"vhf": "active", "uhf": "active"}

    def stop(self):
        self.stop_calls += 1
        return {"vhf": "inactive", "uhf": "inactive"}


class FakeP25Process:
    pid = 4321

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


def manager_with_running_p25():
    manager = ScannerManager.__new__(ScannerManager)
    manager.status = ScannerStatus()
    manager.process = FakeP25Process()
    manager.lock = threading.RLock()
    manager.analog_service_controller = FakeAnalogController()
    manager.status_payload = lambda: asdict(manager.status)
    return manager


def test_backend_start_coordinates_existing_p25_with_vhf_and_uhf() -> None:
    manager = manager_with_running_p25()

    payload, status = manager.start()

    assert status == HTTPStatus.ACCEPTED
    assert manager.analog_service_controller.start_calls == 1
    assert payload["coordinated_scanners"] == {
        "p25": "running",
        "vhf": "active",
        "uhf": "active",
    }


def test_backend_stop_stops_p25_vhf_and_uhf() -> None:
    manager = manager_with_running_p25()

    payload, status = manager.stop()

    assert status == HTTPStatus.ACCEPTED
    assert manager.analog_service_controller.stop_calls == 1
    assert payload["coordinated_scanners"] == {
        "p25": "stopped",
        "vhf": "stopped",
        "uhf": "stopped",
    }


def test_runtime_units_are_not_enabled_for_boot() -> None:
    installer = (ROOT / "tools" / "install_audio_runtime_units.sh").read_text(encoding="utf-8")
    for unit in ANALOG_SCANNER_UNITS:
        service = (ROOT / "systemd" / unit).read_text(encoding="utf-8")
        assert "WantedBy=multi-user.target" not in service
        assert "SuccessExitStatus=SIGINT SIGTERM 130" in service
        assert unit in installer
    assert "systemctl disable --now" in installer


def test_dashboard_has_no_scanner_autostart_and_describes_coordinated_controls() -> None:
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    manual = (ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    launcher = (ROOT / "tools" / "open_pi_scanner_dashboard.sh").read_text(encoding="utf-8")
    desktop = (ROOT / "desktop" / "PI-Scanner.desktop").read_text(encoding="utf-8")

    assert "V0_5K_AUTO_START_RTL_POOL" not in app
    assert "window.setTimeout(autoStart" not in app
    assert "Start Scanning + Audio" in html
    assert "scanning are all stopped" in manual
    assert "starts P25, VHF, and UHF scanning together" in manual
    assert "/api/scanner/start" not in launcher
    assert "open_pi_scanner_dashboard.sh" in desktop

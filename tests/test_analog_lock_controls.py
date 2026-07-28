import json
from pathlib import Path

import pytest

from pi_p25_scanner import analog_continuous_scanner
from pi_p25_scanner import backend
from pi_p25_scanner import vhf_fft_scanner


ROLE = "analog_2m"
FREQUENCY_HZ = 154_340_000


def _write_locked_status(root: Path, state: str = "locked") -> None:
    status = root / "runtime/status/analog_2m.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        json.dumps(
            {
                "state": state,
                "current_channel": {
                    "name": "Crpl Crk EMS",
                    "frequency_hz": FREQUENCY_HZ,
                },
            }
        ),
        encoding="utf-8",
    )


def test_skip_block_clear_lock_and_clear_blocks_are_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_path = tmp_path / "runtime/settings/analog_controls.json"
    _write_locked_status(tmp_path)
    monkeypatch.setattr(backend, "_ANALOG_DASHBOARD_ROOT", tmp_path)
    monkeypatch.setattr(backend, "_ANALOG_CONTROL_FILE", control_path)
    monkeypatch.setattr(backend.time, "time", lambda: 1_000.0)

    skipped = backend._analog_control_action(
        {"role": ROLE, "action": "skip"}
    )
    assert skipped["skip_until_epoch"] == 1_600.0
    assert skipped["channel"]["frequency_hz"] == FREQUENCY_HZ

    blocked = backend._analog_control_action(
        {"role": ROLE, "action": "block"}
    )
    assert blocked["controls"]["blocked_frequencies_hz"] == [
        FREQUENCY_HZ
    ]
    assert str(FREQUENCY_HZ) not in blocked["controls"]["skip_until_epoch"]

    cleared_lock = backend._analog_control_action(
        {"role": ROLE, "action": "clear_lock"}
    )
    assert cleared_lock["clear_lock_generation"] == 1
    assert cleared_lock["controls"]["blocked_frequencies_hz"] == [
        FREQUENCY_HZ
    ]

    cleared_blocks = backend._analog_control_action(
        {"role": ROLE, "action": "clear_blocks"}
    )
    assert cleared_blocks["controls"]["blocked_frequencies_hz"] == []
    assert cleared_blocks["controls"]["skip_until_epoch"] == {}
    assert cleared_blocks["controls"]["clear_lock_generation"] == 1


def test_channel_actions_reject_stale_last_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_path = tmp_path / "runtime/settings/analog_controls.json"
    _write_locked_status(tmp_path, state="fft_scanning")
    monkeypatch.setattr(backend, "_ANALOG_DASHBOARD_ROOT", tmp_path)
    monkeypatch.setattr(backend, "_ANALOG_CONTROL_FILE", control_path)

    with pytest.raises(backend.ConfigError, match="not currently locked"):
        backend._analog_control_action({"role": ROLE, "action": "skip"})


def test_workers_read_the_same_clear_lock_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_path = tmp_path / "analog_controls.json"
    control_path.write_text(
        json.dumps(
            {
                "roles": {
                    ROLE: {
                        "clear_lock_generation": 7,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        analog_continuous_scanner,
        "ANALOG_CONTROL_PATH",
        control_path,
    )
    monkeypatch.setattr(
        vhf_fft_scanner,
        "ANALOG_CONTROL_PATH",
        control_path,
    )

    assert analog_continuous_scanner.analog_clear_lock_generation(ROLE) == 7
    assert vhf_fft_scanner.analog_clear_lock_generation() == 7

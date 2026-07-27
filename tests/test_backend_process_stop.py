import subprocess
from unittest.mock import Mock

from pi_p25_scanner.backend import _stop_process_safely


def _running_process() -> Mock:
    process = Mock()
    process.poll.return_value = None
    return process


def test_stop_process_exits_after_terminate() -> None:
    process = _running_process()
    process.wait.return_value = 0

    result = _stop_process_safely(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()
    process.wait.assert_called_once_with(timeout=5.0)

    assert result["was_running"] is True
    assert result["terminated"] is True
    assert result["killed"] is False
    assert result["reaped"] is True
    assert result["return_code"] == 0


def test_stop_process_escalates_to_kill() -> None:
    process = _running_process()
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd=["op25"], timeout=5),
        -9,
    ]

    result = _stop_process_safely(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2

    assert result["terminated"] is True
    assert result["killed"] is True
    assert result["reaped"] is True
    assert result["return_code"] == -9


def test_stop_process_handles_second_timeout() -> None:
    process = _running_process()
    process.wait.side_effect = [
        subprocess.TimeoutExpired(cmd=["op25"], timeout=5),
        subprocess.TimeoutExpired(cmd=["op25"], timeout=5),
    ]
    process.poll.side_effect = [None, None, None]

    result = _stop_process_safely(process)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()

    assert result["terminated"] is True
    assert result["killed"] is True
    assert result["reaped"] is False
    assert result["return_code"] is None


def test_stop_process_handles_already_exited_process() -> None:
    process = Mock()
    process.poll.return_value = 0

    result = _stop_process_safely(process)

    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    process.wait.assert_not_called()

    assert result["was_running"] is False
    assert result["reaped"] is True
    assert result["return_code"] == 0


def test_stop_process_handles_terminate_race() -> None:
    process = _running_process()
    process.terminate.side_effect = ProcessLookupError
    process.poll.side_effect = [None, 0]

    result = _stop_process_safely(process)

    process.kill.assert_not_called()
    assert result["terminated"] is False
    assert result["reaped"] is True
    assert result["return_code"] == 0

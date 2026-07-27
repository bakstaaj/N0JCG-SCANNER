import signal
import subprocess
from unittest.mock import Mock, call, patch

from pi_p25_scanner.ppm_calibration import _terminate_process_group


def _fake_process() -> Mock:
    proc = Mock()
    proc.pid = 12345
    proc.poll.return_value = None
    return proc


def test_terminate_process_group_exits_after_sigterm() -> None:
    proc = _fake_process()
    proc.communicate.return_value = ("finished", None)

    with patch(
        "pi_p25_scanner.ppm_calibration.os.killpg",
        create=True,
    ) as killpg:
        output = _terminate_process_group(proc)

    assert output == "finished"
    killpg.assert_called_once_with(12345, signal.SIGTERM)
    proc.communicate.assert_called_once_with(timeout=3.0)


def test_terminate_process_group_escalates_to_sigkill() -> None:
    proc = _fake_process()
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(
            cmd=["op25"],
            timeout=3,
            output="before-term\n",
        ),
        ("after-kill\n", None),
    ]

    with patch(
        "pi_p25_scanner.ppm_calibration.os.killpg",
        create=True,
    ) as killpg:
        output = _terminate_process_group(proc)

    assert "before-term" in output
    assert "after-kill" in output
    assert killpg.call_args_list == [
        call(12345, signal.SIGTERM),
    ]
    proc.kill.assert_called_once_with()


def test_terminate_process_group_handles_second_timeout() -> None:
    proc = _fake_process()
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(
            cmd=["op25"],
            timeout=3,
            output="term-output\n",
        ),
        subprocess.TimeoutExpired(
            cmd=["op25"],
            timeout=3,
            output="kill-output\n",
        ),
    ]

    with patch(
        "pi_p25_scanner.ppm_calibration.os.killpg",
        create=True,
    ) as killpg:
        output = _terminate_process_group(proc)

    assert "term-output" in output
    assert "kill-output" in output
    assert killpg.call_count == 1
    proc.kill.assert_called_once_with()


def test_terminate_process_group_falls_back_to_direct_signal() -> None:
    proc = _fake_process()
    proc.communicate.return_value = ("done", None)

    with patch(
        "pi_p25_scanner.ppm_calibration.os.killpg",
        side_effect=PermissionError,
        create=True,
    ):
        output = _terminate_process_group(proc)

    assert output == "done"
    proc.send_signal.assert_called_once_with(signal.SIGTERM)

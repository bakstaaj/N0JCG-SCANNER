import subprocess
import sys

from pi_p25_scanner.analog_continuous_scanner import _start_stream_drain


def test_stream_drain_prevents_pipe_deadlock() -> None:
    payload_size = 2 * 1024 * 1024

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.stderr.buffer.write(b'x' * {payload_size}); "
                "sys.stderr.flush()"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    assert process.stderr is not None
    buffer, thread = _start_stream_drain(
        process.stderr,
        limit_bytes=64 * 1024,
    )

    return_code = process.wait(timeout=10)
    thread.join(timeout=2)

    assert return_code == 0
    assert not thread.is_alive()
    assert len(buffer) == 64 * 1024
    assert buffer == b"x" * (64 * 1024)


def test_stream_drain_retains_newest_bytes_only() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stderr.buffer.write(b'a' * 70000); "
                "sys.stderr.buffer.write(b'b' * 1000); "
                "sys.stderr.flush()"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    assert process.stderr is not None
    buffer, thread = _start_stream_drain(
        process.stderr,
        limit_bytes=4096,
    )

    assert process.wait(timeout=10) == 0
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(buffer) == 4096
    assert buffer.endswith(b"b" * 1000)

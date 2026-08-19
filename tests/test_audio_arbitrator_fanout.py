import http.client
import sys
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from pi_scanner_audio_arbitrator import (  # noqa: E402
    CLIENT_JITTER_GRACE_SECONDS,
    FRAME_BYTES,
    Handler,
    Source,
    State,
)


def make_state(max_frames: int = 20) -> State:
    return State(
        0.5,
        1,
        1,
        max_frames,
        {
            23456: Source("P25", 23456),
            23458: Source("VHF", 23458),
            23459: Source("UHF", 23459),
        },
    )


def test_two_browsers_receive_identical_frames_without_competing() -> None:
    state = make_state()
    first_browser = state.register_client()
    second_browser = state.register_client()
    frame_1 = bytes([1]) * FRAME_BYTES
    frame_2 = bytes([2]) * FRAME_BYTES
    now = time.time()

    assert state.process(23456, frame_1, now)
    assert state.process(23456, frame_2, now + 0.02)

    first_1, first_browser = state.read_after(first_browser, 0)
    second_1, second_browser = state.read_after(second_browser, 0)
    first_2, first_browser = state.read_after(first_browser, 0)
    second_2, second_browser = state.read_after(second_browser, 0)

    assert (first_1, first_2) == (frame_1, frame_2)
    assert (second_1, second_2) == (frame_1, frame_2)
    assert first_browser == second_browser == 2
    assert state.snapshot()["clients"] == 2

    state.unregister_client()
    state.unregister_client()
    assert state.snapshot()["clients"] == 0


def test_slow_browser_resumes_at_oldest_retained_frame() -> None:
    state = make_state(max_frames=2)
    sequence = state.register_client()
    now = time.time()
    frames = [bytes([value]) * FRAME_BYTES for value in (1, 2, 3)]
    for index, frame in enumerate(frames):
        assert state.process(23458, frame, now + index * 0.02)

    received, sequence = state.read_after(sequence, 0)

    assert received == frames[1]
    assert sequence == 2


def test_active_stream_keeps_twenty_millisecond_cadence_during_frame_gap() -> None:
    state = make_state()
    state.process(23456, bytes([7]) * FRAME_BYTES, time.time())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.audio_state = state
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_address[1],
        timeout=2,
    )

    try:
        connection.request("GET", "/audio.pcm")
        response = connection.getresponse()
        started = time.monotonic()
        payload = response.read(FRAME_BYTES * 6)
        elapsed = time.monotonic() - started
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert len(payload) == FRAME_BYTES * 6
    assert 0.06 <= elapsed < 0.30


def test_installed_arbitrator_uses_gap_tolerant_start_and_tail() -> None:
    service = (
        ROOT / "systemd" / "pi-p25-raw-audio-bridge.service"
    ).read_text(encoding="utf-8")

    assert "--release-seconds 3.5 --warmup-frames 0 --prebuffer-frames 10" in service


def test_active_source_gets_bounded_late_frame_recovery_window() -> None:
    state = make_state()
    now = time.time()
    state.process(23456, bytes([3]) * FRAME_BYTES, now)

    assert state.source_is_recent(now + 0.05)
    assert not state.source_is_recent(now + 0.20)
    assert CLIENT_JITTER_GRACE_SECONDS == 0.12


def test_source_status_tracks_packet_jitter() -> None:
    state = make_state()
    now = time.time()
    state.process(23459, bytes([1]) * FRAME_BYTES, now)
    state.process(23459, bytes([2]) * FRAME_BYTES, now + 0.06)

    source = state.snapshot()["sources"]["UHF"]
    assert source["packet_gaps_over_40ms"] == 1
    assert source["max_packet_gap_seconds"] == 0.06

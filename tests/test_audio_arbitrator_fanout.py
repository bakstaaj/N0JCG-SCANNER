import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from pi_scanner_audio_arbitrator import FRAME_BYTES, Source, State  # noqa: E402


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

import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from p25_audio_pool import OP25_AUDIO_DRAIN, SourceArbiter  # noqa: E402


def test_p25_audio_starts_on_first_valid_frame_after_each_boundary() -> None:
    arbiter = SourceArbiter(
        min_rms=25,
        release_seconds=2.5,
        warmup_frames=0,
    )
    voice = struct.pack("<160h", *([100] * 160))

    assert arbiter.process_audio(23502, voice, 100.0) is True
    arbiter.process_flag(23502, OP25_AUDIO_DRAIN, 100.02)
    assert arbiter.selected_port is None
    assert arbiter.process_audio(23502, voice, 100.04) is True
    assert arbiter.source(23502).warmup_suppressed_frames == 0


def test_installed_p25_pool_has_no_opening_frame_suppression() -> None:
    service = (
        ROOT / "systemd" / "pi-p25-audio-pool.service"
    ).read_text(encoding="utf-8")

    assert "--min-rms 25" in service
    assert "--warmup-frames 0" in service

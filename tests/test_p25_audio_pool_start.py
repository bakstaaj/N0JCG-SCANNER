import json
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from p25_audio_pool import (  # noqa: E402
    OP25_AUDIO_DRAIN,
    CaptureRecorder,
    SourceArbiter,
)


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


def test_bounded_capture_records_input_output_and_boundaries(tmp_path) -> None:
    recorder = CaptureRecorder(tmp_path, duration_seconds=5)
    voice = struct.pack("<160h", *([100] * 160))
    recorder.record(
        now=100.0,
        port=23502,
        kind="audio",
        payload=voice,
        rms=100,
        selected_before=None,
        selected_after=23502,
        forwarded=True,
    )
    recorder.record(
        now=100.02,
        port=23502,
        kind="flag",
        payload=struct.pack("<H", OP25_AUDIO_DRAIN),
        flag=OP25_AUDIO_DRAIN,
        selected_before=23502,
        selected_after=None,
    )
    recorder.close()

    assert (tmp_path / "pool_input_23502.pcm").read_bytes() == voice
    assert (tmp_path / "pool_forwarded.pcm").read_bytes() == voice
    events = (tmp_path / "pool_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    manifest = json.loads(
        (tmp_path / "capture_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["completed"] is True
    assert manifest["input_audio_frames"] == 1
    assert manifest["forwarded_audio_frames"] == 1

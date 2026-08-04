import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from analyze_p25_audio_capture import analyze  # noqa: E402


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_analyzer_counts_zero_valued_drain_flag_and_decoder_events(tmp_path) -> None:
    write_jsonl(
        tmp_path / "pool_events.jsonl",
        [
            {
                "utc": 1785781810.0,
                "offset_seconds": 0.0,
                "kind": "audio",
                "rms": 100,
                "forwarded": True,
            },
            {
                "utc": 1785781810.02,
                "offset_seconds": 0.02,
                "kind": "flag",
                "flag": 0,
            },
        ],
    )
    write_jsonl(tmp_path / "browser_frames.jsonl", [])
    (tmp_path / "browser_capture_manifest.json").write_text(
        json.dumps(
            {
                "started_utc": 1785781810.0,
                "ended_utc": 1785781812.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "op25-runtime.log").write_text(
        "08/03/26 11:30:10.500000 [1] sync established, tuning time 0.500000 seconds\n"
        "08/03/26 11:30:11.000000 [1] voice channel timeout, freq(852.225000)\n",
        encoding="utf-8",
    )

    report = analyze(tmp_path)

    assert report["pool"]["drain_flags"] == 1
    assert report["pool"]["segments"][0]["media_seconds"] == 0.02
    assert report["op25_decoder"]["sync_events"] == 1
    assert report["op25_decoder"]["voice_channel_timeouts"] == 1

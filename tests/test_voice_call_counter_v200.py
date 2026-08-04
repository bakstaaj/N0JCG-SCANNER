from pathlib import Path


def test_voice_calls_tile_uses_deduplicated_counter() -> None:
    app = Path("web/app.js").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "activity?.distinct_voice_calls ?? activity?.voice_call_events ?? 0" in app
    assert "activity?.clear_voice_events ?? 0" not in app
    assert '<span>Voice Calls</span><strong id="activityClearEvents">' in html
    assert "2.0.0-voice-call-counter" in html

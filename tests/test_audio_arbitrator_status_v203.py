from pathlib import Path


def test_audio_tile_reports_live_arbitrator_state() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")

    assert "Audio Arbitrator" in html
    assert 'id="browserAudioLastEvent">Checking' in html
    assert "audio_arbitrator_live.js?v=" in html

    assert "renderAudioArbitratorStatus" in app
    assert "audioStatus?.playback_started" in app
    assert "`${activeSource} Playing`" in app
    assert "`${activeSource} Buffering`" in app
    assert "Muted · ${activeSource || 'Idle'}" in app
    assert "Idle · Connected" in app
    assert "Idle · No Listener" in app

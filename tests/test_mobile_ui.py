from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_dashboard_is_separate_and_phone_friendly() -> None:
    html = (ROOT / "web" / "mobile.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "mobile.css").read_text(encoding="utf-8")

    assert 'name="viewport"' in html
    assert 'viewport-fit=cover' in html
    assert 'href="/"' in html
    assert 'id="startBtn"' in html
    assert 'id="stopBtn"' in html
    assert 'id="muteBtn"' in html
    assert 'id="volumeSlider"' in html
    assert 'id="voiceCalls"' in html
    assert 'id="vhfLocks"' in html
    assert 'id="uhfLocks"' in html
    assert 'id="skipBtn"' in html
    assert 'id="blockBtn"' in html
    assert 'width: min(100%, 520px)' in css
    assert 'env(safe-area-inset-top)' in css


def test_mobile_controls_use_existing_coordinated_apis_without_autostart() -> None:
    script = (ROOT / "web" / "mobile.js").read_text(encoding="utf-8")

    assert "postJson('/api/scanner/start')" in script
    assert "postJson('/api/scanner/stop')" in script
    assert "postJson('/api/analog/control'" in script
    assert "event.isTrusted === false" in script
    assert "alreadyRunning = scannersRunning()" in script
    assert "? 'Listen'" in script
    assert "window.setInterval(poll, 1000)" in script
    assert script.count("postJson('/api/scanner/start')") == 1

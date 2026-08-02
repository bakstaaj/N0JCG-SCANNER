from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_dashboard_is_separate_and_phone_friendly() -> None:
    html = (ROOT / "web" / "mobile.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "mobile.css").read_text(encoding="utf-8")

    assert 'name="viewport"' in html
    assert 'viewport-fit=cover' in html
    assert '<title>N0JCG SCANNER Mobile</title>' in html
    assert '<span class="eyebrow">N0JCG SCANNER</span>' in html
    assert 'href="/?desktop=1"' in html
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
    assert "/audio.pcm?_=" in script
    assert "new AudioContextClass" in script
    assert ".play()" not in script
    assert "audio.wav" not in script
    assert "event.isTrusted === false" in script
    assert "alreadyRunning = scannersRunning()" in script
    assert "? 'Listen'" in script
    assert "window.setInterval(poll, 1000)" in script
    assert script.count("postJson('/api/scanner/start')") == 1


def test_phone_browser_redirect_has_full_ui_override() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "navigator.userAgentData?.mobile === true" in html
    assert "Android.*Mobile|iPhone|iPod" in html
    assert "window.location.replace('/mobile.html')" in html
    assert "params.get('desktop') === '1'" in html

from pathlib import Path


def test_same_origin_control_override() -> None:
    app = Path("web/app.js").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "SAME_ORIGIN_ANALOG_CONTROLS_V117" in app
    assert "fetchJson('/api/analog/status')" in app
    assert "fetchJson('/api/analog/controls')" in app
    assert "stopImmediatePropagation" in app
    assert "chooseActiveRole" in app
    assert "2.0.1-analog-lock-controls" in html

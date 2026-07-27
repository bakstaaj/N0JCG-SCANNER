from pathlib import Path


def test_analog_controls_are_runtime_backed_and_always_visible() -> None:
    backend = Path("src/pi_p25_scanner/backend.py").read_text(
        encoding="utf-8"
    )
    worker = Path(
        "src/pi_p25_scanner/analog_continuous_scanner.py"
    ).read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    index = Path("web/index.html").read_text(encoding="utf-8")

    assert 'path == "/api/analog/control"' in backend
    assert "time.time() + 600.0" in backend
    assert "blocked_frequencies_hz" in backend
    assert "analog_channel_suppression" in worker
    assert "analog_squelch_offset" in worker

    root_position = worker.index(
        "ROOT = Path(__file__).resolve().parents[2]"
    )
    control_position = worker.index(
        'ANALOG_CONTROL_PATH = ROOT /'
    )
    assert control_position > root_position

    assert 'id="analogLiveControls"' in index
    assert 'id="analogSkipBtn"' in index
    assert 'hidden' not in index.split(
        'id="analogLiveControls"',
        1,
    )[1].split(">", 1)[0]

    assert "setAnalogControlsEnabled(null)" in app
    assert "button.disabled = !enabled" in app
    assert "panel.hidden" not in app

from pathlib import Path


def test_bottom_controls_and_always_enabled_clear() -> None:
    index = Path("web/index.html").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    css = Path("web/app.css").read_text(encoding="utf-8")

    controls_pos = index.index('id="analogLiveControls"')
    stats_pos = index.index('class="stats-grid"')
    radio_setup_pos = index.index('id="radioSetupScreen"')

    assert stats_pos < controls_pos < radio_setup_pos
    assert "Clear Lock" in index
    assert 'id="analogClearLockBtn"' in index
    assert "Clear Blocks" in index
    assert 'id="analogClearBlockBtn"' in index

    assert "clearAllAnalogBlocks" in app
    assert "analog_2m" in app
    assert "analog_70cm" in app
    assert "clearButton.disabled = !hasAnyBlocksOrSkips" in app
    assert "analogClearLockBtn: 'clear_lock'" in app

    assert "grid-template-columns" in css
    assert "repeat(4, minmax(0, 1fr))" in css
    assert "min-height: 3.6rem" in css
    assert "width: 100%" in css

from pathlib import Path


def test_controls_follow_required_enablement_rules() -> None:
    index = Path("web/index.html").read_text(
        encoding="utf-8"
    )
    app = Path("web/app.js").read_text(
        encoding="utf-8"
    )
    css = Path("web/app.css").read_text(
        encoding="utf-8"
    )

    stats_pos = index.index('class="stats-grid"')
    controls_pos = index.index('id="analogLiveControls"')
    radio_pos = index.index('id="radioSetupScreen"')

    assert stats_pos < controls_pos < radio_pos
    assert "ANALOG_SQUELCH_VALUE_LAYOUT_V114" in app
    assert "p25IsActive" in app
    assert "analogScanningAvailable" in app
    assert "hasAnyBlocksOrSkips" in app
    assert "clearButton.disabled = !hasAnyBlocksOrSkips" in app
    assert "Raised VHF and UHF squelch" in app
    assert "Lowered VHF and UHF squelch" in app
    assert "ANALOG_SQUELCH_VALUE_LAYOUT_V114" in css
    assert "clear: both" in css

from pathlib import Path


def test_squelch_value_and_action_rows() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    css = Path("web/app.css").read_text(encoding="utf-8")

    assert 'id="analogSquelchValue"' in html
    assert 'class="analog-squelch-row"' in html
    assert 'class="analog-channel-action-row"' in html

    skip = html.index('id="analogSkipBtn"')
    block = html.index('id="analogBlockBtn"')
    clear = html.index('id="analogClearBlockBtn"')
    row = html.index('class="analog-channel-action-row"')

    assert row < skip < block < clear

    assert "absoluteSquelchForRole" in app
    assert "renderAbsoluteSquelch" in app
    assert "threshold_rms" in app
    assert "VHF ${vhf} · UHF ${uhf}" in app

    assert "ANALOG_SQUELCH_VALUE_LAYOUT_V114" in css
    assert "grid-template-columns: repeat(3" in css

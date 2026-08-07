from pathlib import Path


def test_squelch_controls_are_hidden_but_channel_actions_remain() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")
    app = Path("web/app.js").read_text(encoding="utf-8")
    css = Path("web/app.css").read_text(encoding="utf-8")

    assert 'id="analogSquelchValue"' not in html
    assert 'id="analogSquelchDownBtn"' not in html
    assert 'id="analogSquelchUpBtn"' not in html
    assert 'class="analog-squelch-row"' not in html
    assert 'class="analog-channel-action-row"' in html

    skip = html.index('id="analogSkipBtn"')
    block = html.index('id="analogBlockBtn"')
    clear_lock = html.index('id="analogClearLockBtn"')
    clear = html.index('id="analogClearBlockBtn"')
    row = html.index('class="analog-channel-action-row"')

    assert row < skip < block < clear_lock < clear

    assert "ANALOG_SQUELCH_VALUE_LAYOUT_V114" in app
    assert "ANALOG_SQUELCH_VALUE_LAYOUT_V114" in css
    assert "grid-template-columns: repeat(4" in css


def test_header_uses_compact_title_and_adjacent_status_badges() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "<title>N0JCG Scanner</title>" in html
    assert 'aria-label="N0JCG Scanner"' in html
    assert 'id="dashboardSummary"' not in html

    state = html.index('id="stateBadge"')
    online = html.index('id="connectionStatus"')
    activity = html.index('class="activity-card"')
    assert state < online < activity

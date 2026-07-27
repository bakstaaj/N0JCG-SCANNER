from pathlib import Path


def test_status_counters_cannot_collapse() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")
    css = Path("web/app.css").read_text(encoding="utf-8")

    stats = html.index('<section class="stats-grid"')
    controls = html.index('id="analogLiveControls"')
    radio = html.index('id="radioSetupScreen"')

    assert stats < controls < radio

    for marker in (
        'id="activeSourceStat"',
        'id="activityClearEvents"',
        'id="activityUniqueTgids"',
        'id="analogVhfLocks"',
        'id="analogUhfLocks"',
        'id="activityMutedEvents"',
    ):
        assert marker in html

    assert "PRESERVE_STATUS_COUNTERS_V113" in css
    assert "#dashboardScreen > .stats-grid" in css
    assert "flex: 0 0 auto" in css
    assert "display: grid !important" in css
    assert "visibility: visible !important" in css
    assert "overflow-y: auto" in css

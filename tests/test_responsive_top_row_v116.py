from pathlib import Path


def test_top_row_stays_single_line() -> None:
    css = Path("web/app.css").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "PI_SCANNER_DASHBOARD_LAYOUT_V202" in css
    assert ".control-strip" in css
    assert "grid-template-columns:" in css
    assert "minmax(0, 1.35fr)" in css
    assert "white-space: nowrap" in css
    assert "text-overflow: ellipsis" in css
    assert "2.0.2-dashboard-layout" in html
    assert 'class="big-action audio-mute-button"' in html

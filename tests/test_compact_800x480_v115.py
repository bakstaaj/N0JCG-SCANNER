from pathlib import Path


def test_compact_800x480_layout_exists() -> None:
    css = Path("web/app.css").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "COMPACT_800X480_V115" in css
    assert "@media (max-width: 900px) and (max-height: 540px)" in css
    assert "min-height: 108px" in css
    assert "min-height: 46px" in css
    assert "min-height: 42px" in css
    assert "2.0.2-dashboard-layout" in html

    stats = html.index('class="stats-grid"')
    controls = html.index('id="analogLiveControls"')
    assert stats < controls

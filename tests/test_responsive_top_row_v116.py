from pathlib import Path


def test_top_row_stays_single_line() -> None:
    css = Path("web/app.css").read_text(encoding="utf-8")
    html = Path("web/index.html").read_text(encoding="utf-8")

    assert "RESPONSIVE_TOP_ROW_V116" in css
    assert ".control-strip" in css
    assert "grid-template-columns:" in css
    assert "minmax(0, 1fr)" in css
    assert "white-space: nowrap" in css
    assert "text-overflow: ellipsis" in css
    assert "1.0.16-responsive-top-row" in html

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_uses_approved_n0jcg_product_lockup() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "<title>N0JCG Scanner</title>" in html
    assert 'alt="N0JCG Open Radio Platform"' in html
    assert "assets/brand/N0JCG_Header_Dark_Approved.png" in html
    assert '<strong class="product-name">Scanner</strong>' in html
    assert 'href="n0jcg-brand.css?v=3.2.1-activity-blue"' in html
    assert 'class="return-icon"' in html
    assert 'aria-label="Return to previous application"' in html
    assert "&#8592;" not in html


def test_mobile_uses_same_brand_system_and_product_name() -> None:
    html = (ROOT / "web" / "mobile.html").read_text(encoding="utf-8")

    assert "<title>N0JCG Scanner Mobile</title>" in html
    assert "assets/brand/N0JCG_Header_Dark_Approved.png" in html
    assert '<span class="product-name">Scanner</span>' in html
    assert 'href="n0jcg-brand.css?v=3.2.1-activity-blue"' in html


def test_brand_css_uses_canonical_tokens_and_accessible_states() -> None:
    css = (ROOT / "web" / "n0jcg-brand.css").read_text(encoding="utf-8")

    for token in (
        "--n0-color-navy: #0a1f44",
        "--n0-color-blue: #1565c0",
        "--n0-color-cyan: #00b8d9",
        "--n0-color-mist: #f4f7fa",
        "--n0-color-success: #168a4a",
        "--n0-color-warning: #b25e00",
        "--n0-color-danger: #c62828",
    ):
        assert token in css

    assert "box-shadow: var(--n0-focus)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".badge.on-air" in css


def test_approved_brand_assets_are_present() -> None:
    brand_root = ROOT / "web" / "assets" / "brand"

    assert (brand_root / "N0JCG_Header_Dark_Approved.png").is_file()
    assert (brand_root / "N0JCG_Icon_Approved.png").is_file()


def test_public_user_manual_is_linked() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert 'href="docs/N0JCG_Scanner_User_Manual.pdf"' in html
    assert ">User manual</a>" in html
    assert (ROOT / "web" / "docs" / "N0JCG_Scanner_User_Manual.pdf").is_file()
    assert (ROOT / "docs" / "publications" / "N0JCG_Scanner_User_Manual.docx").is_file()

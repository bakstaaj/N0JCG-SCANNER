from pathlib import Path

from pi_p25_scanner.backend import local_audio_proxy_url


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_rebases_api_and_audio_for_roc_subpath() -> None:
    desktop = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    mobile = (ROOT / "web" / "mobile.js").read_text(encoding="utf-8")
    audio = (ROOT / "web" / "audio_arbitrator_live.js").read_text(
        encoding="utf-8"
    )

    assert "PI_SCANNER_BASE_PATH" in desktop
    assert "p25ApplicationUrl(input)" in desktop
    assert "`${PI_SCANNER_BASE_PATH}${url}`" in desktop
    assert "`${PI_SCANNER_BASE_PATH}/audio-api${url.slice('/radio'.length)}`" in desktop
    assert "applicationUrl(url)" in mobile
    assert "applicationUrl(`/radio/audio.pcm?_=${Date.now()}`)" in mobile
    assert "`/radio/audio.pcm?_=${Date.now()}`" in audio


def test_html_assets_and_navigation_are_subpath_safe() -> None:
    desktop = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    mobile = (ROOT / "web" / "mobile.html").read_text(encoding="utf-8")

    assert 'href="app.css?' in desktop
    assert 'src="app.js?' in desktop
    assert 'src="audio_arbitrator_live.js?' in desktop
    assert 'class="icon-button return-button"' in desktop
    assert '<a id="returnButton" class="icon-button return-button"' in desktop
    assert 'data-return-link' in desktop
    assert 'RETURN_TARGET_STORAGE_KEY' in (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "`${base}/mobile.html`" in desktop
    assert 'href="mobile.css?' in mobile
    assert 'src="mobile.js?' in mobile
    assert "n0jcg-brand.css?v=3.1.2-registration-badge" in desktop
    assert 'href="./?desktop=1"' in mobile
    assert "2.0.19-first-run-radio-setup" in desktop
    assert "source.origin !== window.location.origin" in desktop
    assert "3.0.1-roc-subpath" in mobile


def test_radio_backend_retains_direct_maintenance_audio_route() -> None:
    assert local_audio_proxy_url("/radio/audio.pcm?x=1").endswith(
        ":8072/audio.pcm?x=1"
    )


def test_deployment_manifests_put_complete_scanner_on_radio_pi() -> None:
    roc = (ROOT / "deploy" / "roc-files.txt").read_text(encoding="utf-8")
    radio = (ROOT / "deploy" / "radio-pi-files.txt").read_text(encoding="utf-8")

    assert "web" in roc
    assert "application" not in roc
    assert "src" not in roc
    assert "src" in radio
    assert "config" in radio
    assert "web" in radio

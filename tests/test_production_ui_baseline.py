from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def test_experimental_fast_spectrum_telemetry_is_not_in_release_ui() -> None:
    combined = "\n".join(
        (WEB / filename).read_text(encoding="utf-8")
        for filename in ("index.html", "app.js", "app.css")
    )

    forbidden = (
        "Fast Spectrum Telemetry",
        "spectrum-telemetry",
        "CSV-constrained RF sweep",
    )

    for marker in forbidden:
        assert marker not in combined

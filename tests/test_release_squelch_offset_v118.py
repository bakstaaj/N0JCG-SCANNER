from pathlib import Path


def _source() -> str:
    path = Path(
        "src/pi_p25_scanner/analog_continuous_scanner.py"
    )
    assert path.exists()
    return path.read_text(
        encoding="utf-8",
        errors="ignore",
    )


def test_release_threshold_uses_runtime_offset() -> None:
    source = _source()

    assert "base_squelch" in source
    assert "squelch_offset = analog_squelch_offset(self.role)" in source
    assert "base_release_squelch + squelch_offset" in source
    assert "min(" in source
    assert "configured_squelch" in source


def test_calibrated_release_example() -> None:
    base_lock = 550
    base_release = 475
    offset = 1857

    lock_threshold = base_lock + offset
    release_threshold = min(
        lock_threshold,
        base_release + offset,
    )

    assert lock_threshold == 2407
    assert release_threshold == 2332
    assert 2292 <= release_threshold

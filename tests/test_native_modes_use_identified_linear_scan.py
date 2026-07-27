from pathlib import Path


def test_native_modes_use_identified_linear_scan() -> None:
    source = Path(
        "src/pi_p25_scanner/analog_continuous_scanner.py"
    ).read_text(encoding="utf-8")

    marker = 'if search_mode in {"native_linear", "persistent_linear"}:'
    assert marker in source

    mode_block = source.split(marker, 1)[1].split(
        "while not self.stop_requested:",
        1,
    )[0]

    assert "return self.run_native_linear" not in mode_block
    assert 'search_mode = "linear"' in mode_block

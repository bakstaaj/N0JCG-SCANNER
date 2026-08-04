import csv
from pathlib import Path


EXPECTED_REPEATERS = {
    ("2m", 146.970, "K0ESD"),
    ("70cm", 448.450, "K0ESD"),
    ("70cm", 449.325, "KA4EPS"),
    ("70cm", 449.700, "KC0CVU"),
}


def test_cabin_channel_lists_include_requested_repeaters() -> None:
    for path in (
        Path("config/analog_channels_cabin.csv"),
        Path("config/channel_lists/analog_channels_cabin.csv"),
    ):
        with path.open(newline="", encoding="utf-8") as handle:
            channels = {
                (row["receiver"], float(row["frequency_mhz"]), row["name"])
                for row in csv.DictReader(handle)
            }
        assert EXPECTED_REPEATERS <= channels, path

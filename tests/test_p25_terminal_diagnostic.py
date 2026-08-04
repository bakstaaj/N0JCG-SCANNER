import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from p25_terminal_diagnostic import channel_update, summarize  # noqa: E402
from p25_terminal_plot_snapshot import plot_files, receiver_state, safe_name  # noqa: E402


def test_channel_update_and_frequency_error_summary() -> None:
    messages = [
        {"json_type": "rx_update", "files": []},
        {
            "json_type": "channel_update",
            "0": {"name": "Control", "freq": 853537500, "error": 625, "tag": "Control Channel"},
            "1": {"name": "Voice", "freq": 852225000, "error": -375, "tag": "Dispatch", "tgid": 6142},
        },
    ]
    update = channel_update(messages)
    report = summarize([{"channels": {"0": update["0"], "1": update["1"]}}])

    assert report["receivers"]["0"]["frequency_error_hz"]["mean"] == 625
    assert report["receivers"]["1"]["frequency_error_hz"]["mean"] == -375
    assert report["receivers"]["1"]["talkgroups"] == {"6142": 1}


def test_plot_file_selection_and_safe_name() -> None:
    messages = [
        {
            "json_type": "rx_update",
            "files": ["images/plot-0-spectrum.png", "images/plot-1-constellation.png"],
        }
    ]

    assert plot_files(messages, 0) == ["images/plot-0-spectrum.png"]
    assert safe_name("http://127.0.0.1/images/plot-0-spectrum.png") == "plot-0-spectrum.png"
    channel_messages = [
        {"json_type": "channel_update", "0": {"freq": 853537500, "error": 500}}
    ]
    assert receiver_state(channel_messages, 0)["freq"] == 853537500

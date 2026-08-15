import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from p25_scalable_multi_rx_wrapper import build_multi_rx_config  # noqa: E402


def test_dedicated_voice_receiver_can_use_fixed_wideband_center(tmp_path) -> None:
    manifest = {
        "systems": [
            {
                "name": "Test P25",
                "control_channels_hz": [853_300_000, 853_537_500],
                "control_channels_mhz": ["853.300000", "853.537500"],
            }
        ]
    }
    config, receivers = build_multi_rx_config(
        manifest=manifest,
        control_serial="00000251",
        voice_serials=["00000252"],
        sample_rate=960_000,
        ppm=0.0,
        control_gain="LNA:49",
        voice_gain="LNA:49",
        control_demod_type="cqpsk",
        voice_demod_type="fsk4",
        voice_sample_rate=2_400_000,
        voice_center_hz=852_493_750,
        terminal_type="http:127.0.0.1:18091",
        crypt_behavior=2,
        audio_base_port=23500,
        audio_port_count=10,
        control_only_whitelist=tmp_path / "control.tsv",
    )

    control, voice = config["devices"]
    assert control["frequency"] == 853_300_000
    assert control["rate"] == 960_000
    assert voice["frequency"] == 852_493_750
    assert voice["rate"] == 2_400_000
    assert config["channels"][1]["frequency"] == 852_493_750
    assert receivers[1]["center_frequency_hz"] == 852_493_750

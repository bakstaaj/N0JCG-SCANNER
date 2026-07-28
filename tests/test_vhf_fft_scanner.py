import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np

from pi_p25_scanner.analog_channels import ROLE_DEFAULTS
from pi_p25_scanner.vhf_fft_scanner import (
    AudioMetrics,
    CarrierMetrics,
    NfmDemodulator,
    REQUIRED_SERIAL,
    VhfFftScanner,
    audio_metrics,
    candidate_validation_passes,
    candidate_is_available,
    call_audio_is_present,
    carrier_release_hang_seconds,
    cooldown_allows_candidate,
    enabled_vhf_channels,
    group_channels,
    segment_center_hz,
    signal_rise_score,
    priority_candidates,
    spectrum_candidates,
    strong_carrier_probation_passes,
    write_pcm_wav,
)
from pi_p25_scanner import vhf_fft_scanner


class VhfFftScannerTests(unittest.TestCase):
    def test_receiver_serial_contract_is_not_reversed(self) -> None:
        self.assertEqual(REQUIRED_SERIAL, "00000144")
        self.assertEqual(ROLE_DEFAULTS["analog_2m"]["rtl_serial"], "00000144")
        self.assertEqual(ROLE_DEFAULTS["analog_70cm"]["rtl_serial"], "00000440")

    def test_channel_upload_uses_configured_analog_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["PI_SCANNER_ANALOG_ROOT"] = temporary
            environment["PYTHONPATH"] = "src"
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from pi_p25_scanner.analog_channels import "
                        "DEFAULT_CONFIG_PATH; print(DEFAULT_CONFIG_PATH)"
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            expected = Path(temporary) / "runtime/settings/analog_receivers.json"
            self.assertEqual(Path(result.stdout.strip()), expected)

    def test_runtime_skip_block_and_squelch_controls_are_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            control_path = Path(temporary) / "analog_controls.json"
            control_path.write_text(
                json.dumps(
                    {
                        "roles": {
                            "analog_2m": {
                                "blocked_frequencies_hz": [154_340_000],
                                "skip_until_epoch": {"155000000": 200.0},
                                "squelch_offset_rms": 100,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(vhf_fft_scanner, "ANALOG_CONTROL_PATH", control_path):
                self.assertEqual(
                    vhf_fft_scanner.analog_channel_suppression(154_340_000),
                    ("blocked", None),
                )
                self.assertEqual(
                    vhf_fft_scanner.analog_channel_suppression(
                        155_000_000, now_epoch=100.0
                    ),
                    ("skipped", 200.0),
                )
                self.assertEqual(vhf_fft_scanner.analog_squelch_offset(), 100)

    def test_channels_are_deduplicated_and_non_nfm_entries_are_ignored(self) -> None:
        channels = enabled_vhf_channels(
            {
                "channels": [
                    {"frequency_hz": 154_340_000, "mode": "FM", "enabled": True},
                    {"frequency_hz": 154_340_000, "mode": "NFM", "enabled": True},
                    {"frequency_hz": 155_000_000, "mode": "AM", "enabled": True},
                    {"frequency_hz": 446_000_000, "mode": "NFM", "enabled": True},
                ]
            }
        )
        self.assertEqual([item["frequency_hz"] for item in channels], [154_340_000])
        self.assertEqual(channels[0]["mode"], "nfm")

    def test_fft_finds_only_matching_configured_frequency(self) -> None:
        sample_rate = 2_400_000
        sample_count = 65_536
        center = 154_000_000
        positions = np.arange(sample_count, dtype=np.float64)
        rng = np.random.default_rng(144)
        iq = (
            0.02
            * (rng.normal(size=sample_count) + 1j * rng.normal(size=sample_count))
            + 0.7
            * np.exp(2j * math.pi * 340_000 * positions / sample_rate)
        ).astype(np.complex64)
        channels = [
            {"name": "active", "frequency_hz": 154_340_000, "priority": 0},
            {"name": "quiet", "frequency_hz": 154_890_000, "priority": 0},
        ]
        found = spectrum_candidates(iq, center, sample_rate, channels, 8.0)
        self.assertEqual([item.channel["name"] for item in found], ["active"])

    def test_voice_band_audio_is_active_but_silence_and_noise_are_not(self) -> None:
        positions = np.arange(8_000, dtype=np.float64)
        tone = (4_000 * np.sin(2 * math.pi * 1_000 * positions / 8_000)).astype("<i2")
        silence = np.zeros(8_000, dtype="<i2")
        rng = np.random.default_rng(1)
        noise = rng.normal(0, 4_000, 8_000).astype("<i2")
        self.assertTrue(audio_metrics(tone).active)
        self.assertFalse(audio_metrics(silence).active)
        self.assertFalse(audio_metrics(noise).active)

    def test_nfm_demodulator_accepts_tone_and_rejects_modulated_noise(self) -> None:
        sample_rate = 240_000
        sample_count = 120_000
        positions = np.arange(sample_count, dtype=np.float64) / sample_rate
        tone_phase = (
            2 * math.pi * -50_000 * positions
            + 2.5 * np.sin(2 * math.pi * 1_000 * positions)
        )
        tone_iq = np.exp(1j * tone_phase).astype(np.complex64)

        rng = np.random.default_rng(3)
        noise_deviation = rng.normal(0, 3_000, sample_count)
        noise_phase = np.cumsum(
            2 * math.pi * (-50_000 + noise_deviation) / sample_rate
        )
        noise_iq = np.exp(1j * noise_phase).astype(np.complex64)

        def demodulate(iq: np.ndarray) -> np.ndarray:
            demodulator = NfmDemodulator()
            chunks = [
                demodulator.process(iq[index : index + 24_000])[1]
                for index in range(0, len(iq), 24_000)
            ]
            return np.concatenate(chunks)[800:]

        self.assertTrue(audio_metrics(demodulate(tone_iq)).active)
        self.assertFalse(audio_metrics(demodulate(noise_iq)).active)

    def test_validation_accepts_two_strong_chunks_from_short_transmission(self) -> None:
        carriers = [
            CarrierMetrics(14.0, -50_200.0, -200.0),
            CarrierMetrics(11.0, -49_800.0, 200.0),
            CarrierMetrics(4.0, -50_100.0, -100.0),
            CarrierMetrics(3.0, -50_000.0, 0.0),
            CarrierMetrics(2.0, -49_900.0, 100.0),
        ]
        audio = AudioMetrics(7_000, -13.4, 0.40, 0.95, True)
        accepted, good_chunks = candidate_validation_passes(
            carriers, audio, 8.0, 4_000.0, 2
        )
        self.assertTrue(accepted)
        self.assertEqual(good_chunks, 2)

    def test_active_audio_accepts_one_end_of_transmission_carrier_slice(self) -> None:
        carriers = [
            CarrierMetrics(11.35, -40_430.0, 5_147.0),
            CarrierMetrics(1.8, -57_280.0, -37.0),
        ]
        audio = AudioMetrics(20_108, -4.24, 0.44, 0.94, True)
        accepted, good_chunks = candidate_validation_passes(
            carriers, audio, 8.0, 6_000.0, 1
        )
        self.assertTrue(accepted)
        self.assertEqual(good_chunks, 1)

    def test_real_signal_rise_overrides_noise_candidate_cooldown(self) -> None:
        self.assertFalse(cooldown_allows_candidate(14.0, 110.0, 12.0, 100.0, 6.0))
        self.assertTrue(cooldown_allows_candidate(30.0, 110.0, 12.0, 100.0, 6.0))
        self.assertTrue(cooldown_allows_candidate(8.0, 90.0, 12.0, 100.0, 6.0))
        self.assertEqual(signal_rise_score(40.0, 39.0), 1.0)
        self.assertEqual(signal_rise_score(25.0, 10.0), 15.0)
        self.assertEqual(signal_rise_score(25.0, None), 25.0)
        self.assertTrue(candidate_is_available(100, 20.0, 110.0, 40.0, 100.0))
        self.assertFalse(candidate_is_available(0, 20.0, 110.0, 40.0, 100.0))

    def test_priority_hit_short_circuits_remaining_fft_survey(self) -> None:
        candidates = [
            vhf_fft_scanner.SpectrumCandidate(
                {"frequency_hz": 146_600_000, "priority": 100}, 20.0, 1.0, 0.0
            ),
            vhf_fft_scanner.SpectrumCandidate(
                {"frequency_hz": 146_520_000, "priority": 0}, 40.0, 1.0, 0.0
            ),
        ]
        self.assertEqual(priority_candidates(candidates, 15.0), [candidates[0]])
        self.assertEqual(priority_candidates(candidates, 25.0), [])
        self.assertEqual(carrier_release_hang_seconds(50.0), 1.5)
        self.assertEqual(carrier_release_hang_seconds(5.0), 0.45)

    def test_last_call_diagnostic_is_valid_mono_wav(self) -> None:
        import wave

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "last.wav"
            write_pcm_wav(path, np.arange(800, dtype="<i2"))
            with wave.open(str(path), "rb") as recording:
                self.assertEqual(recording.getnchannels(), 1)
                self.assertEqual(recording.getsampwidth(), 2)
                self.assertEqual(recording.getframerate(), 8_000)
                self.assertEqual(recording.getnframes(), 800)

    def test_audio_classifier_still_rejects_carrier_without_audio(self) -> None:
        carriers = [CarrierMetrics(56.0, -50_020.0, -20.0)] * 5
        noise = AudioMetrics(10_000, -10.3, 0.55, 0.93, False)
        accepted, good_chunks = candidate_validation_passes(
            carriers, noise, 8.0, 4_000.0, 2
        )
        self.assertFalse(accepted)
        self.assertEqual(good_chunks, 5)
        probation, strong_chunks = strong_carrier_probation_passes(
            carriers, noise, 20.0, 4_000.0, 3
        )
        self.assertTrue(probation)
        self.assertEqual(strong_chunks, 5)
        self.assertFalse(
            call_audio_is_present(CarrierMetrics(3.0, -50_000.0, 0.0), noise, 250)
        )
        self.assertTrue(
            call_audio_is_present(CarrierMetrics(48.0, -50_000.0, 0.0), noise, 250)
        )
        silent = AudioMetrics(10, -70.0, 1.0, 0.0, False)
        self.assertFalse(
            call_audio_is_present(CarrierMetrics(48.0, -50_000.0, 0.0), silent, 250)
        )

    def test_segments_keep_channels_in_capture_span_and_avoid_dc(self) -> None:
        channels = [
            {"frequency_hz": 146_520_000},
            {"frequency_hz": 147_015_000},
            {"frequency_hz": 154_340_000},
        ]
        segments = group_channels(channels, 1_800_000)
        self.assertEqual(len(segments), 2)
        for segment in segments:
            center = segment_center_hz(segment, 2_400_000)
            self.assertTrue(
                all(abs(int(item["frequency_hz"]) - center) >= 30_000 for item in segment)
            )

    def test_configuration_fails_closed_on_reversed_serial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "analog.json"
            config.write_text(
                json.dumps(
                    {
                        "workers": {
                            "analog_2m": {
                                "rtl_serial": "00000440",
                                "audio_udp_port": 23458,
                                "channels": [
                                    {
                                        "frequency_hz": 154_340_000,
                                        "mode": "nfm",
                                        "enabled": True,
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(Exception, "00000144"):
                VhfFftScanner(config, config, root / "status.json", True)

    def test_status_preserves_dashboard_compatibility_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "analog.json"
            status_path = root / "status.json"
            config.write_text(
                json.dumps(
                    {
                        "workers": {
                            "analog_2m": {
                                "rtl_serial": "00000144",
                                "audio_udp_port": 23458,
                                "channels": [
                                    {
                                        "frequency_hz": 154_340_000,
                                        "mode": "nfm",
                                        "enabled": True,
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            scanner = VhfFftScanner(config, config, status_path, True)
            try:
                scanner.status("fft_scanning")
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            finally:
                scanner.close()
            self.assertEqual(payload["rtl_serial"], "00000144")
            self.assertEqual(payload["channel_count"], 1)
            self.assertEqual(payload["scan_cycles"], 0)
            self.assertEqual(payload["threshold_rms"], 250)
            self.assertEqual(payload["search_mode"], "fft_directed_nfm_v2")
            self.assertIsNone(payload["last_lock"])

    def test_close_preserves_terminal_smoke_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "analog.json"
            status_path = root / "status.json"
            config.write_text(
                json.dumps(
                    {
                        "workers": {
                            "analog_2m": {
                                "rtl_serial": "00000144",
                                "audio_udp_port": 23458,
                                "channels": [
                                    {
                                        "frequency_hz": 154_340_000,
                                        "mode": "nfm",
                                        "enabled": True,
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            scanner = VhfFftScanner(config, config, status_path, True)
            scanner.status("smoke_passed")
            scanner.close()
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "smoke_passed")


if __name__ == "__main__":
    unittest.main()

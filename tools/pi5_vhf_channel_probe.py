#!/usr/bin/env python3
"""Bounded receive-only VHF carrier/NFM diagnostic for one channel."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/home/pi/PI-SCANNER"))
    parser.add_argument("--frequency-hz", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()

    sys.path.insert(0, str(args.root / "src"))
    from pi_p25_scanner.vhf_fft_scanner import (  # noqa: PLC0415
        NfmDemodulator,
        VhfFftScanner,
        audio_metrics,
        carrier_metrics,
    )

    scanner = VhfFftScanner(
        args.root / "runtime/settings/analog_receivers.json",
        args.root / "config/analog_receivers.example.json",
        Path("/tmp/pi-scanner-vhf-channel-probe-status.json"),
        True,
    )
    channel = next(
        (
            item
            for item in scanner.channels
            if int(item["frequency_hz"]) == args.frequency_hz
        ),
        None,
    )
    if channel is None:
        raise SystemExit(f"frequency is not in enabled VHF list: {args.frequency_hz}")

    sample_rate = int(scanner.worker.get("nfm_sample_rate_hz") or 240_000)
    offset = int(scanner.worker.get("nfm_tuner_offset_hz") or 50_000)
    chunk_samples = int(scanner.worker.get("nfm_chunk_samples") or 24_000)
    gain = float(scanner.worker.get("nfm_audio_output_gain") or 70_000.0)
    orientations = {
        "signal_below_tuner": {
            "expected_offset_hz": -float(offset),
            "demodulator": NfmDemodulator(sample_rate, 8_000, offset, gain),
            "carrier": [],
            "pcm": [],
        },
        "signal_above_tuner": {
            "expected_offset_hz": float(offset),
            "demodulator": NfmDemodulator(sample_rate, 8_000, -offset, gain),
            "carrier": [],
            "pcm": [],
        },
    }

    try:
        scanner.start_receiver()
        assert scanner.rtl is not None
        scanner.set_rate(sample_rate)
        scanner.rtl.tune(args.frequency_hz + offset)
        time.sleep(0.25)
        scanner.rtl.read_iq(int(sample_rate * 0.10))
        deadline = time.monotonic() + max(1.0, args.seconds)
        while time.monotonic() < deadline:
            iq = scanner.rtl.read_iq(chunk_samples)
            for item in orientations.values():
                item["carrier"].append(
                    carrier_metrics(
                        iq,
                        sample_rate,
                        item["expected_offset_hz"],
                    )
                )
                _, pcm = item["demodulator"].process(iq)
                item["pcm"].append(pcm)
    finally:
        scanner.close()

    report = {
        "ok": True,
        "receive_only": True,
        "frequency_hz": args.frequency_hz,
        "channel": channel,
        "receiver_serial": "00000144",
        "tuner_frequency_hz": args.frequency_hz + offset,
        "sample_rate_hz": sample_rate,
        "seconds": args.seconds,
        "orientations": {},
    }
    for name, item in orientations.items():
        carriers = item["carrier"]
        pcm = np.concatenate(item["pcm"]) if item["pcm"] else np.zeros(0, dtype="<i2")
        audio = audio_metrics(pcm, minimum_rms=0)
        report["orientations"][name] = {
            "expected_offset_hz": item["expected_offset_hz"],
            "carrier_snr_db_median": round(
                statistics.median(value.snr_db for value in carriers), 3
            ),
            "carrier_snr_db_max": round(max(value.snr_db for value in carriers), 3),
            "frequency_error_hz_median": round(
                statistics.median(value.frequency_error_hz for value in carriers), 3
            ),
            "audio": {
                "rms": audio.rms,
                "rms_dbfs": round(audio.rms_dbfs, 3),
                "spectral_flatness": round(audio.spectral_flatness, 6),
                "voice_band_ratio": round(audio.voice_band_ratio, 6),
                "active_with_zero_rms_floor": audio.active,
            },
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    print("FINAL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

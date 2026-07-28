#!/usr/bin/env python3
from __future__ import annotations

import array
import json
import math
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(os.environ.get("PI_SCANNER_ROOT", "/home/pi/PI-SCANNER"))
CONFIG_PATH = ROOT / "runtime/settings/analog_receivers.json"
STATUS_PATH = ROOT / "runtime/status/analog_2m.json"
RTL_TCP_HOST = "127.0.0.1"
RTL_TCP_PORT = 12344
SERIAL_FALLBACK = "00000144"
UDP_FALLBACK = ("127.0.0.1", 23458)

running = True


def handle_stop(_signum: int, _frame: Any) -> None:
    global running
    running = False


signal.signal(signal.SIGTERM, handle_stop)
signal.signal(signal.SIGINT, handle_stop)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def load_worker() -> dict[str, Any]:
    data = json.loads(CONFIG_PATH.read_text())
    return data["workers"]["analog_2m"]


def enabled_channels(worker: dict[str, Any]) -> list[dict[str, Any]]:
    channels = [
        dict(item)
        for item in worker.get("channels", [])
        if item.get("enabled", True)
        and int(item.get("frequency_hz") or 0) > 0
    ]
    channels.sort(key=lambda item: int(item["frequency_hz"]))
    return channels


def resolve_serial(worker: dict[str, Any]) -> str:
    for key in ("rtl_serial", "device_serial", "serial", "receiver_serial"):
        if worker.get(key):
            return str(worker[key])
    return SERIAL_FALLBACK


def resolve_udp(worker: dict[str, Any]) -> tuple[str, int]:
    host = str(
        worker.get("udp_host")
        or worker.get("audio_udp_host")
        or UDP_FALLBACK[0]
    )
    port = int(
        worker.get("udp_port")
        or worker.get("audio_udp_port")
        or UDP_FALLBACK[1]
    )
    return host, port


def group_segments(
    channels: list[dict[str, Any]],
    usable_span_hz: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    start = 0

    for channel in channels:
        frequency = int(channel["frequency_hz"])
        if not current:
            current = [channel]
            start = frequency
        elif frequency - start <= usable_span_hz:
            current.append(channel)
        else:
            groups.append(current)
            current = [channel]
            start = frequency

    if current:
        groups.append(current)
    return groups


def fir_lowpass(
    cutoff_hz: float,
    sample_rate: float,
    taps: int = 101,
) -> np.ndarray:
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2
    kernel = (
        2
        * cutoff_hz
        / sample_rate
        * np.sinc(2 * cutoff_hz * n / sample_rate)
    )
    kernel *= np.hamming(taps)
    kernel /= np.sum(kernel)
    return kernel.astype(np.float32)


class RtlTcpClient:
    def __init__(self, host: str, port: int) -> None:
        self.sock = socket.create_connection((host, port), timeout=10)
        self.sock.settimeout(5)
        header = self.read_exact(12)
        if header[:4] != b"RTL0":
            raise RuntimeError(f"Unexpected rtl_tcp header: {header!r}")

    def command(self, command_id: int, value: int) -> None:
        self.sock.sendall(
            struct.pack(">BI", int(command_id), int(value) & 0xFFFFFFFF)
        )

    def set_frequency(self, hz: int) -> None:
        self.command(0x01, hz)

    def set_sample_rate(self, rate: int) -> None:
        self.command(0x02, rate)

    def set_gain_mode(self, manual: bool) -> None:
        self.command(0x03, 1 if manual else 0)

    def set_gain_tenths_db(self, gain: int) -> None:
        self.command(0x04, gain)

    def set_ppm(self, ppm: int) -> None:
        self.command(0x05, ppm)

    def reset_buffer(self) -> None:
        self.command(0x0E, 0)

    def read_exact(self, count: int) -> bytes:
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise EOFError("rtl_tcp disconnected")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read_iq(self, complex_samples: int) -> np.ndarray:
        raw = np.frombuffer(
            self.read_exact(complex_samples * 2),
            dtype=np.uint8,
        ).astype(np.float32)
        iq = (raw - 127.5) / 127.5
        return iq[0::2] + 1j * iq[1::2]

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class PersistentVhfScanner:
    def __init__(self) -> None:
        self.worker = load_worker()
        self.channels = enabled_channels(self.worker)
        if not self.channels:
            raise RuntimeError("No enabled VHF channels")

        self.serial = resolve_serial(self.worker)
        self.udp_target = resolve_udp(self.worker)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.scan_rate = int(
            self.worker.get("fft_scan_sample_rate") or 2_400_000
        )
        self.scan_span = int(
            self.worker.get("fft_scan_usable_span_hz") or 1_900_000
        )
        self.scan_samples = int(
            self.worker.get("fft_scan_samples") or 65536
        )
        self.scan_discard = int(
            self.worker.get("fft_scan_discard_samples") or 32768
        )
        self.carrier_margin_db = float(
            self.worker.get("fft_carrier_margin_db") or 10.0
        )

        self.lock_rate = int(
            self.worker.get("fft_lock_sample_rate") or 240_000
        )
        self.lock_offset = int(
            self.worker.get("fft_lock_offset_hz") or 50000
        )
        self.lock_chunk = int(
            self.worker.get("fft_lock_chunk_samples") or 24000
        )
        self.audio_rate = int(
            self.worker.get("audio_rate_hz")
            or self.worker.get("audio_sample_rate_hz")
            or self.worker.get("audio_sample_rate")
            or 8000
        )
        self.audio_frame_bytes = int(
            self.worker.get("frame_bytes") or 320
        )
        self.audio_pending = bytearray()
        self.fm_quieting_lock_threshold = float(
            self.worker.get("fm_quieting_lock_threshold")
            or 100.0
        )
        self.fm_quieting_validation_ceiling = float(
            self.worker.get("fm_quieting_validation_ceiling")
            or 300.0
        )
        self.fm_quieting_validation_chunks = int(
            self.worker.get("fm_quieting_validation_chunks")
            or 3
        )
        self.fm_quieting_required_good_chunks = int(
            self.worker.get("fm_quieting_required_good_chunks")
            or 2
        )
        self.fm_quieting_settle_seconds = float(
            self.worker.get("fm_quieting_settle_seconds")
            or 0.50
        )
        self.fm_quieting_release_threshold = float(
            self.worker.get("fm_quieting_release_threshold")
            or 800.0
        )
        self.audio_gain = float(
            self.worker.get("audio_output_gain") or 15000
        )
        self.release_seconds = float(
            self.worker.get("release_seconds") or 1.0
        )
        self.minimum_hold_seconds = float(
            self.worker.get("hold_seconds") or 0.5
        )
        self.release_margin_db = float(
            self.worker.get("fft_release_margin_db") or 4.0
        )
        self.candidate_validation_rms = int(
            self.worker.get("fft_candidate_validation_rms") or 200
        )
        self.candidate_validation_max_rms = int(
            self.worker.get("fft_candidate_validation_max_rms") or 500
        )
        self.candidate_validation_chunks = int(
            self.worker.get("fft_candidate_validation_chunks") or 4
        )
        self.candidate_repeat_required = int(
            self.worker.get("fft_candidate_repeat_required") or 2
        )
        self.candidate_repeat_frequency: int | None = None
        self.candidate_repeat_count = 0
        self.candidate_cooldown_seconds = float(
            self.worker.get("fft_candidate_cooldown_seconds") or 3.0
        )
        self.candidate_cooldown: dict[int, float] = {}
        self.candidate_rf_margin_db = float(
            self.worker.get("fft_candidate_rf_margin_db") or 8.0
        )
        self.candidate_rf_min_margin_db = float(
            self.worker.get("fft_candidate_rf_min_margin_db") or 4.0
        )
        self.lock_release_margin_db = float(
            self.worker.get("fft_lock_release_margin_db") or 3.0
        )

        self.segments = group_segments(self.channels, self.scan_span)
        self.audio_filter = fir_lowpass(4200.0, self.lock_rate)
        self.started_epoch = time.time()
        self.sweeps = 0
        self.locks = 0
        self.frames_forwarded = 0
        self.last_candidate: dict[str, Any] | None = None
        self.rtl_process: subprocess.Popen[bytes] | None = None
        self.rtl: RtlTcpClient | None = None
        self.current_rate = 0

    def status(
        self,
        state: str,
        channel: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {
            "worker": "analog_2m",
            "state": state,
            "search_mode": "persistent_fft_rtl_tcp",
            "updated_epoch": time.time(),
            "started_epoch": self.started_epoch,
            "receiver_serial": self.serial,
            "segment_count": len(self.segments),
            "spectrum_sweeps": self.sweeps,
            "spectrum_failures": 0,
            "lock_count": self.locks,
            "frames_forwarded": self.frames_forwarded,
            "current_channel": channel,
            "last_candidate": self.last_candidate,
            "udp_target": f"{self.udp_target[0]}:{self.udp_target[1]}",
            "audio_sample_rate_hz": self.audio_rate,
            "audio_frame_bytes": self.audio_frame_bytes,
            "voice_demodulator": (
                "separate_vhf_rtl_fm_dc_deemp"
            ),
        }
        payload.update(extra)
        atomic_json(STATUS_PATH, payload)

    def start_rtl_tcp(self) -> None:
        command = [
            "/usr/bin/rtl_tcp",
            "-a",
            RTL_TCP_HOST,
            "-p",
            str(RTL_TCP_PORT),
            "-d",
            self.serial,
        ]

        gain = self.worker.get("rf_gain_db")
        if gain is not None:
            command.extend(["-g", str(float(gain))])

        ppm = int(self.worker.get("ppm") or 0)
        if ppm:
            command.extend(["-P", str(ppm)])

        self.rtl_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        deadline = time.time() + 10
        last_error: Exception | None = None
        while time.time() < deadline:
            if self.rtl_process.poll() is not None:
                stderr = b""
                if self.rtl_process.stderr is not None:
                    stderr = self.rtl_process.stderr.read()
                raise RuntimeError(
                    "rtl_tcp exited: "
                    + stderr.decode(errors="replace").strip()
                )
            try:
                self.rtl = RtlTcpClient(RTL_TCP_HOST, RTL_TCP_PORT)
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.25)

        if self.rtl is None:
            raise RuntimeError(f"Could not connect to rtl_tcp: {last_error}")

        gain = self.worker.get("rf_gain_db")
        if gain is not None:
            self.rtl.set_gain_mode(True)
            self.rtl.set_gain_tenths_db(int(round(float(gain) * 10)))
        else:
            self.rtl.set_gain_mode(False)

        self.rtl.set_ppm(int(self.worker.get("ppm") or 0))

    def set_rate(self, sample_rate: int) -> None:
        assert self.rtl is not None
        if self.current_rate != sample_rate:
            self.rtl.set_sample_rate(sample_rate)
            self.current_rate = sample_rate
            self.rtl.reset_buffer()
            time.sleep(0.03)

    def tune(self, frequency_hz: int) -> None:
        assert self.rtl is not None
        self.rtl.set_frequency(frequency_hz)
        self.rtl.reset_buffer()
        time.sleep(0.02)



    def scan_segment(
        self,
        segment: list[dict[str, Any]],
    ) -> list[tuple[dict[str, Any], float, float]]:
        assert self.rtl is not None
        low = int(segment[0]["frequency_hz"])
        high = int(segment[-1]["frequency_hz"])
        center = int((low + high) / 2)

        self.set_rate(self.scan_rate)
        self.tune(center)
        self.rtl.read_iq(self.scan_discard)
        samples = self.rtl.read_iq(
            self.scan_samples
        ).astype(np.complex64)

        window = np.hanning(len(samples)).astype(np.float32)
        fft = np.fft.fftshift(np.fft.fft(samples * window))
        power = 20.0 * np.log10(np.abs(fft) + 1e-12)
        noise_db = float(np.median(power))
        bin_hz = self.scan_rate / len(samples)
        half_bins = max(2, int(round(6250 / bin_hz)))

        candidates: list[
            tuple[dict[str, Any], float, float]
        ] = []

        for channel in segment:
            frequency = int(channel["frequency_hz"])

            if time.monotonic() < self.candidate_cooldown.get(
                frequency,
                0.0,
            ):
                continue

            offset = frequency - center
            index = int(
                round(len(samples) / 2 + offset / bin_hz)
            )
            start = max(0, index - half_bins)
            stop = min(len(power), index + half_bins + 1)
            if start >= stop:
                continue

            channel_power = float(np.max(power[start:stop]))
            margin_db = channel_power - noise_db
            if margin_db < self.carrier_margin_db:
                continue

            candidates.append(
                (channel, channel_power, noise_db)
            )

        return candidates

    def lock_power_db(self, samples: np.ndarray) -> float:
        power = float(np.mean(np.abs(samples) ** 2)) + 1e-15
        return 10.0 * math.log10(power)

    def demodulate_quieting(self, samples: np.ndarray) -> np.ndarray:
        n = np.arange(len(samples), dtype=np.float32)
        mixer = np.exp(
            2j * np.pi * self.lock_offset * n / self.lock_rate
        ).astype(np.complex64)
        baseband = samples * mixer

        discriminator = np.angle(
            baseband[1:] * np.conj(baseband[:-1])
        )
        filtered = np.convolve(
            discriminator,
            self.audio_filter,
            mode="same",
        )

        decimation = max(1, int(round(self.lock_rate / self.audio_rate)))
        audio = filtered[::decimation]
        audio -= float(np.mean(audio))
        pcm = np.clip(
            audio * (self.audio_gain / math.pi),
            -32767,
            32767,
        )
        return pcm.astype("<i2")

    def carrier_metrics(
        self,
        samples: np.ndarray,
    ) -> tuple[float, float, float]:
        window = np.hanning(len(samples)).astype(np.float32)
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        power = 20.0 * np.log10(np.abs(spectrum) + 1e-12)
        bin_hz = self.lock_rate / len(samples)

        expected_offset_hz = -self.lock_offset
        center_index = int(
            round(len(samples) / 2 + expected_offset_hz / bin_hz)
        )
        carrier_half_bins = max(2, int(round(6250 / bin_hz)))
        carrier_start = max(0, center_index - carrier_half_bins)
        carrier_stop = min(
            len(power),
            center_index + carrier_half_bins + 1,
        )
        carrier_power_db = float(
            np.max(power[carrier_start:carrier_stop])
        )

        guard_bins = max(
            carrier_half_bins + 2,
            int(round(15000 / bin_hz)),
        )
        noise_mask = np.ones(len(power), dtype=bool)
        noise_mask[
            max(0, center_index - guard_bins):
            min(len(power), center_index + guard_bins + 1)
        ] = False

        dc_index = len(power) // 2
        dc_guard = max(2, int(round(10000 / bin_hz)))
        noise_mask[
            max(0, dc_index - dc_guard):
            min(len(power), dc_index + dc_guard + 1)
        ] = False

        usable_noise = power[noise_mask]
        noise_db = float(np.median(usable_noise))
        margin_db = carrier_power_db - noise_db
        return margin_db, carrier_power_db, noise_db

    def demodulate_voice(
        self,
        samples: np.ndarray,
    ) -> np.ndarray:
        values = samples.astype(np.complex64)
        if len(values) < 4:
            return np.zeros(0, dtype=np.int16)

        # The successful comparison was E_NEGATIVE_750US.  With the
        # tuner above the channel by lock_offset, mix by -lock_offset.
        index = np.arange(len(values), dtype=np.float64)
        oscillator = np.exp(
            1j
            * 2.0
            * np.pi
            * float(self.lock_offset)
            * index
            / float(self.lock_rate)
        )
        shifted = values * oscillator

        product = shifted[1:] * np.conj(shifted[:-1])
        discriminator = np.angle(product).astype(np.float64)

        # Voice low-pass before decimation.
        taps = 129
        tap_index = (
            np.arange(taps, dtype=np.float64)
            - (taps - 1) / 2
        )
        cutoff_hz = 3400.0
        coefficients = (
            2.0
            * cutoff_hz
            / float(self.lock_rate)
            * np.sinc(
                2.0
                * cutoff_hz
                / float(self.lock_rate)
                * tap_index
            )
        )
        coefficients *= np.hamming(taps)
        coefficients /= np.sum(coefficients)
        filtered = np.convolve(
            discriminator,
            coefficients,
            mode="same",
        )

        decimation = int(self.lock_rate // self.audio_rate)
        if (
            decimation < 1
            or int(self.lock_rate) % int(self.audio_rate) != 0
        ):
            raise RuntimeError(
                "VHF lock rate must be divisible by audio rate"
            )
        audio = filtered[::decimation]

        # 750 microsecond FM voice de-emphasis.
        tau_seconds = 750e-6
        alpha = np.exp(
            -1.0
            / (float(self.audio_rate) * tau_seconds)
        )
        deemphasized = np.empty_like(audio)
        previous = 0.0
        for sample_index, value in enumerate(audio):
            previous = (
                (1.0 - alpha) * float(value)
                + alpha * previous
            )
            deemphasized[sample_index] = previous

        # DC block after de-emphasis.
        blocked = np.empty_like(deemphasized)
        previous_input = 0.0
        previous_output = 0.0
        dc_alpha = 0.995
        for sample_index, value in enumerate(deemphasized):
            current = (
                float(value)
                - previous_input
                + dc_alpha * previous_output
            )
            blocked[sample_index] = current
            previous_input = float(value)
            previous_output = current

        # Automatic speech gain based on a robust peak estimate.
        robust_peak = float(
            np.percentile(np.abs(blocked), 99.5)
        )
        if robust_peak < 1e-9:
            return np.zeros(len(blocked), dtype=np.int16)

        gain = 14000.0 / robust_peak
        pcm = np.clip(
            blocked * gain,
            -30000.0,
            30000.0,
        )
        return pcm.astype(np.int16)

    def fm_quieting_metric(self, pcm: np.ndarray) -> float:
        values = pcm.astype(np.float64)
        if len(values) < 3:
            return 999999.0
        diff = np.diff(values)
        return float(np.sqrt(np.mean(diff * diff)))

    def centered_carrier_metrics(
        self,
        samples: np.ndarray,
    ) -> tuple[float, float, float, float]:
        window = np.hanning(len(samples)).astype(np.float32)
        spectrum = np.fft.fftshift(np.fft.fft(samples * window))
        power = 20.0 * np.log10(np.abs(spectrum) + 1e-12)
        bin_hz = self.lock_rate / len(samples)

        expected_offset_hz = -float(self.lock_offset)
        expected_index = int(
            round(len(samples) / 2 + expected_offset_hz / bin_hz)
        )

        search_half_bins = max(
            2,
            int(round(12000.0 / bin_hz)),
        )
        search_start = max(0, expected_index - search_half_bins)
        search_stop = min(
            len(power),
            expected_index + search_half_bins + 1,
        )

        local = power[search_start:search_stop]
        local_peak_relative = int(np.argmax(local))
        peak_index = search_start + local_peak_relative
        peak_offset_hz = (
            peak_index - len(samples) / 2
        ) * bin_hz
        frequency_error_hz = peak_offset_hz - expected_offset_hz
        carrier_power_db = float(power[peak_index])

        exclusion_half_bins = max(
            2,
            int(round(10000.0 / bin_hz)),
        )
        noise_mask = np.ones(len(power), dtype=bool)
        noise_mask[
            max(0, peak_index - exclusion_half_bins):
            min(len(power), peak_index + exclusion_half_bins + 1)
        ] = False

        dc_index = len(power) // 2
        dc_half_bins = max(
            2,
            int(round(10000.0 / bin_hz)),
        )
        noise_mask[
            max(0, dc_index - dc_half_bins):
            min(len(power), dc_index + dc_half_bins + 1)
        ] = False

        noise_db = float(np.median(power[noise_mask]))
        margin_db = carrier_power_db - noise_db

        return (
            margin_db,
            carrier_power_db,
            noise_db,
            frequency_error_hz,
        )

    def validate_candidate(
        self,
        channel: dict[str, Any],
        scan_power_db: float,
        scan_noise_db: float,
    ) -> tuple[bool, float]:
        assert self.rtl is not None
        frequency = int(channel["frequency_hz"])

        cooldown_until = self.candidate_cooldown.get(frequency, 0.0)
        if time.monotonic() < cooldown_until:
            return False, 999999.0

        self.set_rate(self.lock_rate)
        self.tune(frequency + self.lock_offset)
        self.rtl.read_iq(
            int(self.lock_rate * self.fm_quieting_settle_seconds)
        )

        required_rf_margin_db = float(
            self.worker.get(
                "fft_candidate_centered_rf_margin_db",
                10.0,
            )
        )
        maximum_frequency_error_hz = float(
            self.worker.get(
                "fft_candidate_max_frequency_error_hz",
                4000.0,
            )
        )

        quieting_metrics: list[float] = []
        rf_margins: list[float] = []
        rf_frequency_errors: list[float] = []
        combined_good_chunks = 0

        for _ in range(self.fm_quieting_validation_chunks):
            samples = self.rtl.read_iq(
                self.lock_chunk
            ).astype(np.complex64)

            quieting_pcm = self.demodulate_quieting(samples)
            quieting = self.fm_quieting_metric(quieting_pcm)
            (
                rf_margin_db,
                _carrier_power_db,
                _noise_db,
                frequency_error_hz,
            ) = self.centered_carrier_metrics(samples)

            quieting_metrics.append(quieting)
            rf_margins.append(rf_margin_db)
            rf_frequency_errors.append(frequency_error_hz)

            quieting_good = (
                quieting <= self.fm_quieting_validation_ceiling
            )
            rf_good = (
                rf_margin_db >= required_rf_margin_db
                and abs(frequency_error_hz)
                <= maximum_frequency_error_hz
            )
            if quieting_good and rf_good:
                combined_good_chunks += 1

        median_quieting = float(
            np.median(np.asarray(quieting_metrics))
        )
        median_rf_margin = float(
            np.median(np.asarray(rf_margins))
        )
        median_frequency_error = float(
            np.median(np.asarray(rf_frequency_errors))
        )

        valid = (
            median_quieting <= self.fm_quieting_lock_threshold
            and median_rf_margin >= required_rf_margin_db
            and abs(median_frequency_error)
            <= maximum_frequency_error_hz
            and combined_good_chunks
            >= self.fm_quieting_required_good_chunks
        )

        self.status(
            "candidate_validating",
            channel,
            candidate_valid=valid,
            validation_method="quieting_and_centered_rf_carrier",
            fm_quieting_metric=median_quieting,
            fm_quieting_metric_values=quieting_metrics,
            centered_rf_margin_db=median_rf_margin,
            centered_rf_margin_values=rf_margins,
            centered_rf_required_margin_db=required_rf_margin_db,
            centered_rf_frequency_error_hz=median_frequency_error,
            centered_rf_frequency_error_values=rf_frequency_errors,
            centered_rf_max_frequency_error_hz=(
                maximum_frequency_error_hz
            ),
            combined_good_chunks=combined_good_chunks,
            combined_required_good_chunks=(
                self.fm_quieting_required_good_chunks
            ),
            scan_power_db=scan_power_db,
            scan_noise_db=scan_noise_db,
        )

        if not valid:
            self.candidate_cooldown[frequency] = (
                time.monotonic()
                + self.candidate_cooldown_seconds
            )

        return valid, median_quieting


    def stop_rtl_tcp_receiver(self) -> None:
        if self.rtl is not None:
            self.rtl.close()
            self.rtl = None

        process = self.rtl_process
        self.rtl_process = None
        self.current_rate = 0

        if process is None:
            return

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

    def downsample_rtl_fm_pcm(self, data: bytes) -> bytes:
        usable = len(data) - (len(data) % 6)
        if usable <= 0:
            return b""

        samples = array.array("h")
        samples.frombytes(data[:usable])
        if sys.byteorder != "little":
            samples.byteswap()

        output = array.array("h")
        for index in range(0, len(samples), 3):
            if index + 2 >= len(samples):
                break
            value = (
                int(samples[index])
                + int(samples[index + 1])
                + int(samples[index + 2])
            ) // 3
            output.append(value)

        if sys.byteorder != "little":
            output.byteswap()
        return output.tobytes()

    def rtl_fm_audio_command(
        self,
        channel: dict[str, Any],
    ) -> list[str]:
        gain = float(
            channel.get("gain_db")
            or self.worker.get("gain_db")
            or 49.6
        )
        ppm = int(self.worker.get("ppm") or 0)
        squelch = int(
            channel.get("native_rtl_fm_squelch_level")
            or self.worker.get("native_rtl_fm_squelch_level")
            or 600
        )
        return [
            "/usr/bin/rtl_fm",
            "-d",
            self.serial,
            "-f",
            str(int(channel["frequency_hz"])),
            "-M",
            "fm",
            "-s",
            "24000",
            "-g",
            str(gain),
            "-l",
            str(squelch),
            "-p",
            str(ppm),
            "-E",
            "dc",
            "-E",
            "deemp",
        ]



    def lock_channel(
        self,
        channel: dict[str, Any],
        scan_power_db: float,
        scan_noise_db: float,
    ) -> None:
        demod_frame_bytes = 960
        audio_frame_bytes = 320

        no_pcm_release_seconds = float(
            self.worker.get("rtl_fm_no_pcm_release_seconds")
            or 0.75
        )
        startup_timeout_seconds = float(
            self.worker.get("rtl_fm_probe_timeout_seconds")
            or 0.90
        )
        confirm_frames = int(
            self.worker.get("rtl_fm_lock_confirm_frames")
            or 3
        )
        no_pcm_cooldown_seconds = float(
            self.worker.get(
                "rtl_fm_no_pcm_candidate_cooldown_seconds"
            )
            or 8.0
        )

        self.status(
            "rtl_fm_probe",
            channel,
            audio_method="separate_vhf_rtl_fm",
            lock_confirmed=False,
            required_confirm_frames=confirm_frames,
            audio_udp_port=int(self.udp_target[1]),
            scan_power_db=scan_power_db,
            scan_noise_db=scan_noise_db,
        )

        self.stop_rtl_tcp_receiver()
        time.sleep(0.20)

        command = self.rtl_fm_audio_command(channel)
        process: subprocess.Popen[bytes] | None = None
        demod_buffer = bytearray()
        pending_frames: list[tuple[bytes, int]] = []
        probe_started_at = time.monotonic()
        last_pcm_at = probe_started_at
        lock_started_at: float | None = None
        lock_confirmed = False

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                bufsize=0,
            )
            assert process.stdout is not None

            while running:
                now = time.monotonic()
                ready, _, _ = select.select(
                    [process.stdout],
                    [],
                    [],
                    0.10,
                )

                if process.poll() is not None:
                    break

                if not ready:
                    if not lock_confirmed:
                        if (
                            now - probe_started_at
                            >= startup_timeout_seconds
                        ):
                            break
                    elif (
                        now - last_pcm_at
                        >= no_pcm_release_seconds
                    ):
                        break
                    continue

                chunk = os.read(
                    process.stdout.fileno(),
                    demod_frame_bytes - len(demod_buffer),
                )
                if not chunk:
                    break

                demod_buffer.extend(chunk)
                if len(demod_buffer) < demod_frame_bytes:
                    continue

                demod_data = bytes(
                    demod_buffer[:demod_frame_bytes]
                )
                del demod_buffer[:demod_frame_bytes]

                frame = self.downsample_rtl_fm_pcm(demod_data)
                if len(frame) != audio_frame_bytes:
                    continue

                pcm = np.frombuffer(frame, dtype="<i2")
                rms = (
                    int(
                        np.sqrt(
                            np.mean(
                                pcm.astype(np.float64) ** 2
                            )
                        )
                    )
                    if len(pcm)
                    else 0
                )

                last_pcm_at = time.monotonic()

                if not lock_confirmed:
                    pending_frames.append((frame, rms))

                    self.status(
                        "rtl_fm_probe",
                        channel,
                        audio_method="separate_vhf_rtl_fm",
                        lock_confirmed=False,
                        confirm_frames_received=len(
                            pending_frames
                        ),
                        required_confirm_frames=confirm_frames,
                        audio_udp_port=int(self.udp_target[1]),
                        rms=rms,
                        scan_power_db=scan_power_db,
                        scan_noise_db=scan_noise_db,
                    )

                    if len(pending_frames) < confirm_frames:
                        continue

                    lock_confirmed = True
                    lock_started_at = time.monotonic()
                    self.locks += 1

                    for buffered_frame, _ in pending_frames:
                        self.udp.sendto(
                            buffered_frame,
                            self.udp_target,
                        )
                        self.frames_forwarded += 1
                    pending_frames.clear()

                    self.status(
                        "locked",
                        channel,
                        audio_method="separate_vhf_rtl_fm",
                        lock_confirmed=True,
                        lock_confirmation_method=(
                            "rtl_fm_pcm_frames"
                        ),
                        required_confirm_frames=confirm_frames,
                        rtl_fm_command=command,
                        audio_udp_port=int(self.udp_target[1]),
                        audio_sample_rate_hz=8000,
                        audio_frame_bytes=320,
                        rms=rms,
                        lock_elapsed_seconds=0.0,
                        release_method="rtl_fm_no_pcm_timeout",
                        no_pcm_release_seconds=(
                            no_pcm_release_seconds
                        ),
                        scan_power_db=scan_power_db,
                        scan_noise_db=scan_noise_db,
                    )
                    continue

                self.udp.sendto(frame, self.udp_target)
                self.frames_forwarded += 1

                elapsed = (
                    0.0
                    if lock_started_at is None
                    else time.monotonic() - lock_started_at
                )

                self.status(
                    "locked",
                    channel,
                    audio_method="separate_vhf_rtl_fm",
                    lock_confirmed=True,
                    lock_confirmation_method=(
                        "rtl_fm_pcm_frames"
                    ),
                    required_confirm_frames=confirm_frames,
                    rtl_fm_command=command,
                    audio_udp_port=int(self.udp_target[1]),
                    audio_sample_rate_hz=8000,
                    audio_frame_bytes=320,
                    rms=rms,
                    lock_elapsed_seconds=elapsed,
                    release_method="rtl_fm_no_pcm_timeout",
                    no_pcm_release_seconds=(
                        no_pcm_release_seconds
                    ),
                    scan_power_db=scan_power_db,
                    scan_noise_db=scan_noise_db,
                )
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=2.0)
                except (
                    ProcessLookupError,
                    subprocess.TimeoutExpired,
                ):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass

            if lock_confirmed:
                self.status(
                    "releasing",
                    channel,
                    audio_method="separate_vhf_rtl_fm",
                    lock_confirmed=True,
                    release_method="rtl_fm_no_pcm_timeout",
                )
            else:
                self.status(
                    "candidate_rejected",
                    channel,
                    audio_method="separate_vhf_rtl_fm",
                    lock_confirmed=False,
                    rejection_reason=(
                        "rtl_fm_no_pcm_confirmation"
                    ),
                    confirm_frames_received=len(pending_frames),
                    required_confirm_frames=confirm_frames,
                )
                self.candidate_cooldown[
                    int(channel["frequency_hz"])
                ] = (
                    time.monotonic()
                    + no_pcm_cooldown_seconds
                )

            time.sleep(0.20)
            self.start_rtl_tcp()
            self.status(
                "spectrum_scanning",
                resumed_after_audio=lock_confirmed,
                rejected_after_rtl_fm_probe=(
                    not lock_confirmed
                ),
            )




    def run(self) -> None:
        self.start_rtl_tcp()
        self.status("starting")

        while running:
            sweep_started = time.monotonic()
            all_candidates: list[
                tuple[dict[str, Any], float, float, int]
            ] = []

            for index, segment in enumerate(
                self.segments,
                start=1,
            ):
                if not running:
                    break

                self.status(
                    "spectrum_scanning",
                    segment_index=index,
                    segment_count=len(self.segments),
                    candidates_collected=len(all_candidates),
                )

                for (
                    channel,
                    power_db,
                    noise_db,
                ) in self.scan_segment(segment):
                    all_candidates.append(
                        (
                            channel,
                            power_db,
                            noise_db,
                            index,
                        )
                    )

            if not running:
                break

            all_candidates.sort(
                key=lambda item: (
                    -(item[1] - item[2]),
                    int(item[0]["frequency_hz"]),
                )
            )

            self.sweeps += 1
            confirmed_lock = False

            self.status(
                "spectrum_scanning",
                sweep_elapsed_seconds=(
                    time.monotonic() - sweep_started
                ),
                ranked_candidate_count=len(all_candidates),
                ranked_candidates=[
                    {
                        "frequency_hz": int(
                            channel["frequency_hz"]
                        ),
                        "name": channel.get("name"),
                        "margin_db": power_db - noise_db,
                        "segment_index": segment_index,
                    }
                    for (
                        channel,
                        power_db,
                        noise_db,
                        segment_index,
                    ) in all_candidates[:12]
                ],
            )

            for rank, item in enumerate(
                all_candidates,
                start=1,
            ):
                if not running:
                    break

                (
                    candidate,
                    power_db,
                    noise_db,
                    segment_index,
                ) = item

                self.last_candidate = {
                    "frequency_hz": int(
                        candidate["frequency_hz"]
                    ),
                    "name": candidate.get("name"),
                    "power_db": power_db,
                    "noise_db": noise_db,
                    "margin_db": power_db - noise_db,
                    "rank": rank,
                    "epoch": time.time(),
                }

                self.status(
                    "spectrum_candidate",
                    candidate,
                    rf_power_db=power_db,
                    noise_db=noise_db,
                    margin_db=power_db - noise_db,
                    candidate_rank=rank,
                    candidate_count=len(all_candidates),
                    segment_index=segment_index,
                )

                valid, validation_metric = (
                    self.validate_candidate(
                        candidate,
                        power_db,
                        noise_db,
                    )
                )

                if not valid:
                    self.status(
                        "candidate_rejected",
                        candidate,
                        fm_quieting_metric=validation_metric,
                        fm_quieting_lock_threshold=(
                            self.fm_quieting_lock_threshold
                        ),
                        candidate_rank=rank,
                        candidate_count=len(all_candidates),
                        scan_power_db=power_db,
                        scan_noise_db=noise_db,
                    )
                    continue

                self.status(
                    "candidate_accepted",
                    candidate,
                    fm_quieting_metric=validation_metric,
                    fm_quieting_lock_threshold=(
                        self.fm_quieting_lock_threshold
                    ),
                    candidate_rank=rank,
                    candidate_count=len(all_candidates),
                    scan_power_db=power_db,
                    scan_noise_db=noise_db,
                )

                locks_before = self.locks
                self.lock_channel(
                    candidate,
                    power_db,
                    noise_db,
                )

                if self.locks > locks_before:
                    confirmed_lock = True
                    break

            self.status(
                "spectrum_scanning",
                sweep_elapsed_seconds=(
                    time.monotonic() - sweep_started
                ),
                candidate_found=confirmed_lock,
                ranked_candidate_count=len(all_candidates),
            )

    def close(self) -> None:
        if self.rtl is not None:
            self.rtl.close()
        if self.rtl_process is not None:
            self.rtl_process.terminate()
            try:
                self.rtl_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.rtl_process.kill()
        self.udp.close()
        try:
            self.status("stopped")
        except Exception:
            pass


def main() -> int:
    scanner = PersistentVhfScanner()
    try:
        scanner.run()
        return 0
    except Exception as exc:
        try:
            scanner.status(
                "error",
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        raise
    finally:
        scanner.close()


if __name__ == "__main__":
    raise SystemExit(main())

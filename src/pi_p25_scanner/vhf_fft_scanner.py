#!/usr/bin/env python3
"""FFT-directed VHF NFM scanner for the dedicated PI-SCANNER receiver.

The worker keeps one rtl_tcp process attached to RTL serial 00000144.  It
surveys only configured VHF channels, validates candidates off the tuner DC
spike, demodulates NFM in-process, and forwards 8 kHz mono PCM to the existing
audio arbitrator on UDP 23458.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import struct
import subprocess
import threading
import time
import wave
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "runtime/settings/analog_receivers.json"
DEFAULT_TEMPLATE = PROJECT_ROOT / "config/analog_receivers.example.json"
DEFAULT_STATUS = PROJECT_ROOT / "runtime/status/analog_2m.json"
ANALOG_CONTROL_PATH = PROJECT_ROOT / "runtime/settings/analog_controls.json"
ROLE = "analog_2m"
REQUIRED_SERIAL = "00000144"
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 23458


class ScannerError(RuntimeError):
    pass


def analog_role_controls() -> dict[str, Any]:
    try:
        payload = json.loads(ANALOG_CONTROL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    roles = payload.get("roles") if isinstance(payload, dict) else None
    controls = roles.get(ROLE) if isinstance(roles, dict) else None
    return controls if isinstance(controls, dict) else {}


def analog_channel_suppression(
    frequency_hz: int, now_epoch: float | None = None
) -> tuple[str | None, float | None]:
    controls = analog_role_controls()
    frequency_key = str(int(frequency_hz))
    blocked = {
        str(value) for value in (controls.get("blocked_frequencies_hz") or [])
    }
    if frequency_key in blocked:
        return "blocked", None
    skips = controls.get("skip_until_epoch") or {}
    if isinstance(skips, dict):
        try:
            until = float(skips.get(frequency_key) or 0.0)
        except (TypeError, ValueError):
            until = 0.0
        now = time.time() if now_epoch is None else now_epoch
        if until > now:
            return "skipped", until
    return None, None


def analog_squelch_offset() -> int:
    try:
        return int(analog_role_controls().get("squelch_offset_rms") or 0)
    except (TypeError, ValueError):
        return 0


def analog_clear_lock_generation() -> int:
    try:
        return int(
            analog_role_controls().get("clear_lock_generation") or 0
        )
    except (TypeError, ValueError):
        return 0


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_pcm_wav(path: Path, pcm: np.ndarray, sample_rate_hz: int = 8_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(temporary), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate_hz)
        output.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
    os.replace(temporary, path)


def enabled_vhf_channels(worker: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one enabled NFM entry per configured VHF frequency."""
    by_frequency: dict[int, dict[str, Any]] = {}
    for raw in worker.get("channels") or []:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        try:
            frequency = int(raw.get("frequency_hz") or 0)
        except (TypeError, ValueError):
            continue
        mode = str(raw.get("mode") or "nfm").strip().lower()
        if not 136_000_000 <= frequency <= 174_000_000:
            continue
        if mode not in {"fm", "nfm", "narrowfm", "fmn"}:
            continue
        channel = dict(raw)
        channel["frequency_hz"] = frequency
        channel["mode"] = "nfm"
        previous = by_frequency.get(frequency)
        if previous is None or int(channel.get("priority") or 0) > int(
            previous.get("priority") or 0
        ):
            by_frequency[frequency] = channel
    return sorted(
        by_frequency.values(),
        key=lambda item: (
            -int(item.get("priority") or 0),
            int(item["frequency_hz"]),
        ),
    )


def group_channels(
    channels: list[dict[str, Any]], usable_span_hz: int
) -> list[list[dict[str, Any]]]:
    ordered = sorted(channels, key=lambda item: int(item["frequency_hz"]))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for channel in ordered:
        frequency = int(channel["frequency_hz"])
        if current and frequency - int(current[0]["frequency_hz"]) > usable_span_hz:
            groups.append(current)
            current = []
        current.append(channel)
    if current:
        groups.append(current)
    return groups


def segment_center_hz(
    channels: list[dict[str, Any]],
    sample_rate_hz: int,
    dc_guard_hz: int = 30_000,
) -> int:
    low = int(channels[0]["frequency_hz"])
    high = int(channels[-1]["frequency_hz"])
    center = (low + high) // 2
    if min(abs(int(item["frequency_hz"]) - center) for item in channels) >= dc_guard_hz:
        return center
    shift = max(dc_guard_hz * 2, 75_000)
    usable_half_span = int(sample_rate_hz * 0.44)
    if high - (center + shift) <= usable_half_span and (center + shift) - low <= usable_half_span:
        return center + shift
    return center - shift


def fir_lowpass(cutoff_hz: float, sample_rate_hz: float, taps: int) -> np.ndarray:
    positions = np.arange(taps, dtype=np.float64) - (taps - 1) / 2
    kernel = (
        2.0
        * cutoff_hz
        / sample_rate_hz
        * np.sinc(2.0 * cutoff_hz * positions / sample_rate_hz)
    )
    kernel *= np.hamming(taps)
    kernel /= np.sum(kernel)
    return kernel.astype(np.float64)


class StreamingFir:
    def __init__(self, coefficients: np.ndarray) -> None:
        self.coefficients = coefficients.astype(np.float64)
        self.history = np.zeros(len(coefficients) - 1, dtype=np.float64)

    def process(self, values: np.ndarray) -> np.ndarray:
        combined = np.concatenate((self.history, values.astype(np.float64)))
        output = np.convolve(combined, self.coefficients, mode="valid")
        self.history = combined[-len(self.history) :].copy()
        return output


class NfmDemodulator:
    """Stateful narrow-FM discriminator producing 8 kHz float/PCM audio."""

    def __init__(
        self,
        sample_rate_hz: int = 240_000,
        audio_rate_hz: int = 8_000,
        tuner_offset_hz: int = 50_000,
        output_gain: float = 70_000.0,
    ) -> None:
        if sample_rate_hz % audio_rate_hz:
            raise ScannerError("lock sample rate must divide exactly to audio rate")
        self.sample_rate_hz = sample_rate_hz
        self.audio_rate_hz = audio_rate_hz
        self.tuner_offset_hz = tuner_offset_hz
        self.output_gain = output_gain
        self.decimation = sample_rate_hz // audio_rate_hz
        self.phase = 0.0
        self.previous_iq: complex | None = None
        self.decimation_phase = 0
        self.filter = StreamingFir(fir_lowpass(3_400.0, sample_rate_hz, 129))
        self.deemphasis_alpha = math.exp(-1.0 / (audio_rate_hz * 750e-6))
        self.deemphasis_state = 0.0
        self.dc_previous_input = 0.0
        self.dc_previous_output = 0.0

    def process(self, iq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values = np.asarray(iq, dtype=np.complex64)
        if not len(values):
            empty = np.zeros(0, dtype=np.float64)
            return empty, empty.astype("<i2")

        positions = np.arange(len(values), dtype=np.float64)
        phase_step = 2.0 * math.pi * self.tuner_offset_hz / self.sample_rate_hz
        oscillator = np.exp(1j * (self.phase + phase_step * positions))
        self.phase = (self.phase + phase_step * len(values)) % (2.0 * math.pi)
        baseband = values * oscillator

        if self.previous_iq is None:
            previous = baseband[:-1]
            current = baseband[1:]
        else:
            previous = np.concatenate(
                (np.asarray([self.previous_iq], dtype=np.complex128), baseband[:-1])
            )
            current = baseband
        self.previous_iq = complex(baseband[-1])
        discriminator = np.angle(current * np.conj(previous)).astype(np.float64)
        filtered = self.filter.process(discriminator)

        start = (-self.decimation_phase) % self.decimation
        audio = filtered[start:: self.decimation]
        self.decimation_phase = (self.decimation_phase + len(filtered)) % self.decimation

        output = np.empty_like(audio)
        for index, value in enumerate(audio):
            deemphasized = (
                (1.0 - self.deemphasis_alpha) * float(value)
                + self.deemphasis_alpha * self.deemphasis_state
            )
            self.deemphasis_state = deemphasized
            blocked = (
                deemphasized
                - self.dc_previous_input
                + 0.995 * self.dc_previous_output
            )
            self.dc_previous_input = deemphasized
            self.dc_previous_output = blocked
            output[index] = blocked

        pcm = np.clip(output * self.output_gain, -30_000, 30_000).astype("<i2")
        return output, pcm


@dataclass(frozen=True)
class AudioMetrics:
    rms: int
    rms_dbfs: float
    spectral_flatness: float
    voice_band_ratio: float
    active: bool


def audio_metrics(
    pcm: np.ndarray,
    audio_rate_hz: int = 8_000,
    minimum_rms: int = 250,
    maximum_flatness: float = 0.45,
    minimum_voice_ratio: float = 0.85,
) -> AudioMetrics:
    values = np.asarray(pcm, dtype=np.float64)
    if len(values) < 64:
        return AudioMetrics(0, -120.0, 1.0, 0.0, False)
    values -= float(np.mean(values))
    rms = int(round(math.sqrt(float(np.mean(values * values)))))
    rms_dbfs = 20.0 * math.log10(max(rms, 1) / 32768.0)
    # Undo most of the FM de-emphasis before measuring flatness. Demodulated
    # static is otherwise strongly colored and can resemble voice-band audio.
    # Speech and alert tones retain spectral structure; noise becomes flat.
    whitened = np.diff(values)
    windowed = whitened * np.hanning(len(whitened))
    power = np.abs(np.fft.rfft(windowed)) ** 2 + 1e-18
    frequencies = np.fft.rfftfreq(len(whitened), 1.0 / audio_rate_hz)
    usable = power[(frequencies >= 200.0) & (frequencies <= 3_700.0)]
    voice = power[(frequencies >= 300.0) & (frequencies <= 3_400.0)]
    total = float(np.sum(usable))
    flatness = float(
        math.exp(float(np.mean(np.log(usable)))) / float(np.mean(usable))
    ) if len(usable) else 1.0
    voice_ratio = float(np.sum(voice)) / total if total > 0.0 else 0.0
    active = (
        rms >= minimum_rms
        and flatness <= maximum_flatness
        and voice_ratio >= minimum_voice_ratio
    )
    return AudioMetrics(rms, rms_dbfs, flatness, voice_ratio, active)


@dataclass(frozen=True)
class CarrierMetrics:
    snr_db: float
    peak_offset_hz: float
    frequency_error_hz: float


def candidate_validation_passes(
    carriers: list[CarrierMetrics],
    audio: AudioMetrics,
    minimum_snr_db: float,
    maximum_frequency_error_hz: float,
    required_good_chunks: int,
) -> tuple[bool, int]:
    """Require repeatable carrier evidence without hiding short transmissions."""
    good_chunks = sum(
        1
        for carrier in carriers
        if carrier.snr_db >= minimum_snr_db
        and abs(carrier.frequency_error_hz) <= maximum_frequency_error_hz
    )
    return audio.active and good_chunks >= required_good_chunks, good_chunks


def strong_carrier_probation_passes(
    carriers: list[CarrierMetrics],
    audio: AudioMetrics,
    minimum_snr_db: float,
    maximum_frequency_error_hz: float,
    required_chunks: int,
) -> tuple[bool, int]:
    strong_chunks = sum(
        1
        for carrier in carriers
        if carrier.snr_db >= minimum_snr_db
        and abs(carrier.frequency_error_hz) <= maximum_frequency_error_hz
    )
    return not audio.active and strong_chunks >= required_chunks, strong_chunks


def cooldown_allows_candidate(
    candidate_snr_db: float,
    cooldown_until: float,
    rejected_baseline_snr_db: float | None,
    now: float,
    override_rise_db: float = 6.0,
) -> bool:
    if now >= cooldown_until:
        return True
    if rejected_baseline_snr_db is None:
        return False
    return candidate_snr_db >= rejected_baseline_snr_db + override_rise_db


def signal_rise_score(
    candidate_snr_db: float, rejected_baseline_snr_db: float | None
) -> float:
    return (
        candidate_snr_db
        if rejected_baseline_snr_db is None
        else candidate_snr_db - rejected_baseline_snr_db
    )


def candidate_is_available(
    priority: int,
    candidate_snr_db: float,
    cooldown_until: float,
    rejected_baseline_snr_db: float | None,
    now: float,
    override_rise_db: float = 6.0,
) -> bool:
    return priority > 0 or cooldown_allows_candidate(
        candidate_snr_db,
        cooldown_until,
        rejected_baseline_snr_db,
        now,
        override_rise_db,
    )


def call_audio_is_present(
    carrier: CarrierMetrics,
    audio: AudioMetrics,
    minimum_rms: int,
    strong_carrier_snr_db: float = 20.0,
) -> bool:
    return audio.active or (
        carrier.snr_db >= strong_carrier_snr_db and audio.rms >= minimum_rms
    )


def priority_candidates(
    candidates: list[SpectrumCandidate],
    minimum_snr_db: float = 25.0,
) -> list[SpectrumCandidate]:
    return [
        candidate
        for candidate in candidates
        if int(candidate.channel.get("priority") or 0) > 0
        and candidate.snr_db >= minimum_snr_db
    ]


def carrier_release_hang_seconds(
    initial_carrier_snr_db: float,
    normal_hang_seconds: float = 0.45,
    strong_hang_seconds: float = 1.5,
    strong_carrier_snr_db: float = 20.0,
) -> float:
    return (
        strong_hang_seconds
        if initial_carrier_snr_db >= strong_carrier_snr_db
        else normal_hang_seconds
    )


def carrier_metrics(
    iq: np.ndarray,
    sample_rate_hz: int,
    expected_offset_hz: float,
    channel_half_width_hz: float = 7_500.0,
) -> CarrierMetrics:
    values = np.asarray(iq, dtype=np.complex64)
    window = np.hanning(len(values))
    power = np.abs(np.fft.fftshift(np.fft.fft(values * window))) ** 2 + 1e-18
    frequencies = np.fft.fftshift(np.fft.fftfreq(len(values), 1.0 / sample_rate_hz))
    search = np.abs(frequencies - expected_offset_hz) <= 10_000.0
    if not np.any(search):
        return CarrierMetrics(-120.0, expected_offset_hz, 0.0)
    search_indices = np.flatnonzero(search)
    peak_index = int(search_indices[int(np.argmax(power[search]))])
    peak_offset = float(frequencies[peak_index])
    signal = np.abs(frequencies - peak_offset) <= channel_half_width_hz
    noise = (
        (np.abs(frequencies - peak_offset) >= 15_000.0)
        & (np.abs(frequencies - peak_offset) <= 70_000.0)
        & (np.abs(frequencies) >= 12_000.0)
    )
    if not np.any(noise):
        return CarrierMetrics(-120.0, peak_offset, peak_offset - expected_offset_hz)
    signal_power = float(np.mean(power[signal]))
    noise_power = float(np.median(power[noise]))
    snr = 10.0 * math.log10(max(signal_power, 1e-18) / max(noise_power, 1e-18))
    centroid_region = np.abs(frequencies - expected_offset_hz) <= 10_000.0
    centroid_power = power[centroid_region]
    centroid = float(
        np.sum(frequencies[centroid_region] * centroid_power)
        / np.sum(centroid_power)
    )
    return CarrierMetrics(snr, peak_offset, centroid - expected_offset_hz)


@dataclass(frozen=True)
class SpectrumCandidate:
    channel: dict[str, Any]
    snr_db: float
    power_db: float
    noise_db: float


def spectrum_candidates(
    iq: np.ndarray,
    center_hz: int,
    sample_rate_hz: int,
    channels: list[dict[str, Any]],
    minimum_snr_db: float,
) -> list[SpectrumCandidate]:
    values = np.asarray(iq, dtype=np.complex64)
    window = np.hanning(len(values))
    power = np.abs(np.fft.fftshift(np.fft.fft(values * window))) ** 2 + 1e-18
    power_db = 10.0 * np.log10(power)
    offsets = np.fft.fftshift(np.fft.fftfreq(len(values), 1.0 / sample_rate_hz))
    results: list[SpectrumCandidate] = []
    for channel in channels:
        target = int(channel["frequency_hz"]) - center_hz
        signal = np.abs(offsets - target) <= 7_500.0
        noise = (
            (np.abs(offsets - target) >= 15_000.0)
            & (np.abs(offsets - target) <= 80_000.0)
            & (np.abs(offsets) >= 15_000.0)
        )
        if not np.any(signal) or not np.any(noise):
            continue
        channel_power = float(np.max(power_db[signal]))
        # A peak-to-median comparison promotes ordinary FFT noise maxima when
        # many bins are examined. Compare against the upper local-noise
        # distribution while retaining sensitivity to a narrow carrier.
        noise_floor = float(np.percentile(power_db[noise], 95.0))
        snr = channel_power - noise_floor
        if snr >= minimum_snr_db:
            results.append(SpectrumCandidate(channel, snr, channel_power, noise_floor))
    results.sort(
        key=lambda item: (item.snr_db, int(item.channel.get("priority") or 0)),
        reverse=True,
    )
    return results


class RtlTcpClient:
    def __init__(self, host: str, port: int) -> None:
        self.socket = socket.create_connection((host, port), timeout=8.0)
        self.socket.settimeout(5.0)
        header = self.read_exact(12)
        if header[:4] != b"RTL0":
            raise ScannerError(f"unexpected rtl_tcp header {header!r}")

    def command(self, command_id: int, value: int) -> None:
        self.socket.sendall(struct.pack(">BI", command_id, int(value) & 0xFFFFFFFF))

    def configure(self, sample_rate_hz: int, gain_db: float, ppm: int) -> None:
        self.command(0x02, sample_rate_hz)
        self.command(0x03, 1)
        self.command(0x04, int(round(gain_db * 10.0)))
        self.command(0x05, ppm)
        self.command(0x0E, 0)

    def set_sample_rate(self, sample_rate_hz: int) -> None:
        self.command(0x02, sample_rate_hz)
        self.command(0x0E, 0)

    def tune(self, frequency_hz: int) -> None:
        self.command(0x01, frequency_hz)
        self.command(0x0E, 0)

    def read_exact(self, byte_count: int) -> bytes:
        chunks: list[bytes] = []
        remaining = byte_count
        while remaining:
            chunk = self.socket.recv(remaining)
            if not chunk:
                raise ScannerError("rtl_tcp disconnected")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read_iq(self, sample_count: int) -> np.ndarray:
        raw = np.frombuffer(self.read_exact(sample_count * 2), dtype=np.uint8)
        scaled = (raw.astype(np.float32) - 127.5) / 127.5
        return (scaled[0::2] + 1j * scaled[1::2]).astype(np.complex64)

    def drain_for(self, seconds: float) -> int:
        """Consume queued IQ while a tuner/rate change settles.

        rtl_tcp continuously produces samples, including while the client is
        computing an FFT. Sleeping after a retune leaves those old-frequency
        samples queued in TCP and can associate them with the new center.
        """
        deadline = time.monotonic() + max(seconds, 0.0)
        byte_count = 0
        self.socket.settimeout(0.01)
        try:
            while time.monotonic() < deadline:
                try:
                    payload = self.socket.recv(65_536)
                except (BlockingIOError, socket.timeout):
                    continue
                if not payload:
                    raise ScannerError("rtl_tcp disconnected while draining IQ")
                byte_count += len(payload)
        finally:
            self.socket.settimeout(5.0)
        return byte_count

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class VhfFftScanner:
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG,
        template_path: Path = DEFAULT_TEMPLATE,
        status_path: Path = DEFAULT_STATUS,
        no_forward: bool = False,
    ) -> None:
        self.config_path = config_path
        self.template_path = template_path
        self.status_path = status_path
        self.no_forward = no_forward
        self.stop_requested = False
        self.started_epoch = time.time()
        self.worker: dict[str, Any] = {}
        self.channels: list[dict[str, Any]] = []
        self.segments: list[list[dict[str, Any]]] = []
        self.config_source = Path()
        self.config_mtime_ns = -1
        self.udp_host = DEFAULT_UDP_HOST
        self.udp_port = DEFAULT_UDP_PORT
        self.udp: socket.socket | None = None
        self.rtl_process: subprocess.Popen[bytes] | None = None
        self.rtl: RtlTcpClient | None = None
        self.current_rate = 0
        self.sweeps = 0
        self.lock_count = 0
        self.rejected_count = 0
        self.frames_generated = 0
        self.frames_forwarded = 0
        self.cooldown: dict[int, float] = {}
        self.cooldown_baseline_snr: dict[int, float] = {}
        self.last_candidate: dict[str, Any] | None = None
        self.last_lock: dict[str, Any] | None = None
        self.run_deadline: float | None = None
        self.rtl_stderr: deque[str] = deque(maxlen=40)
        self.last_state = "initializing"
        self.last_validation_details: dict[str, Any] = {}
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self.channel_events: dict[str, dict[str, Any]] = {}
        self.load_configuration(force=True)
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def request_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True

    def load_configuration(self, force: bool = False) -> bool:
        source = self.config_path if self.config_path.exists() else self.template_path
        if not source.exists():
            raise ScannerError(f"VHF configuration missing: {self.config_path}")
        mtime = source.stat().st_mtime_ns
        if not force and source == self.config_source and mtime == self.config_mtime_ns:
            return False
        payload = json.loads(source.read_text(encoding="utf-8"))
        worker = payload.get("workers", {}).get(ROLE)
        if not isinstance(worker, dict):
            raise ScannerError(f"configuration has no {ROLE} worker")
        serial = str(worker.get("rtl_serial") or "")
        if serial != REQUIRED_SERIAL:
            raise ScannerError(
                f"VHF worker must own RTL serial {REQUIRED_SERIAL}; configured {serial!r}"
            )
        channels = enabled_vhf_channels(worker)
        if not channels:
            raise ScannerError("VHF worker has no enabled NFM channels")
        span = int(worker.get("fft_scan_usable_span_hz") or 1_800_000)
        self.worker = dict(worker)
        self.channels = channels
        self.segments = group_channels(channels, span)
        self.config_source = source
        self.config_mtime_ns = mtime
        self.udp_host = str(
            worker.get("audio_udp_host")
            or payload.get("audio_udp_host")
            or DEFAULT_UDP_HOST
        )
        self.udp_port = int(worker.get("audio_udp_port") or DEFAULT_UDP_PORT)
        if self.udp_port != DEFAULT_UDP_PORT:
            raise ScannerError(f"VHF audio must use arbitrator UDP port {DEFAULT_UDP_PORT}")
        return True

    def status(self, state: str, channel: dict[str, Any] | None = None, **extra: Any) -> None:
        self.last_state = state
        payload: dict[str, Any] = {
            "worker": ROLE,
            "state": state,
            "search_mode": "fft_directed_nfm_v2",
            "receiver_serial": REQUIRED_SERIAL,
            "rtl_serial": REQUIRED_SERIAL,
            "updated_epoch": time.time(),
            "started_epoch": self.started_epoch,
            "config_path": str(self.config_source),
            "configured_channel_count": len(self.channels),
            "channel_count": len(self.channels),
            "segment_count": len(self.segments),
            "spectrum_sweeps": self.sweeps,
            "scan_cycles": self.sweeps,
            "lock_count": self.lock_count,
            "rejected_candidate_count": self.rejected_count,
            "frames_generated": self.frames_generated,
            "frames_forwarded": self.frames_forwarded,
            "current_channel": channel,
            "last_candidate": self.last_candidate,
            "last_lock": self.last_lock,
            "last_validation": self.last_validation_details,
            "recent_events": list(self.recent_events),
            "channel_events": self.channel_events,
            "audio_udp_target": f"{self.udp_host}:{self.udp_port}",
            "audio_sample_rate_hz": 8_000,
            "audio_frame_bytes": 320,
            "no_forward": self.no_forward,
            "threshold_rms": self._minimum_audio_rms(channel or {}),
        }
        payload.update(extra)
        atomic_json(self.status_path, payload)

    def start_receiver(self) -> None:
        port = int(self.worker.get("rtl_tcp_port") or 12344)
        command = [
            "/usr/bin/rtl_tcp",
            "-a", "127.0.0.1",
            "-p", str(port),
            "-d", REQUIRED_SERIAL,
        ]
        self.rtl_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if self.rtl_process.stderr is not None:
            def drain_stderr() -> None:
                assert self.rtl_process is not None
                assert self.rtl_process.stderr is not None
                for raw in iter(self.rtl_process.stderr.readline, b""):
                    self.rtl_stderr.append(raw.decode(errors="replace").rstrip())

            threading.Thread(target=drain_stderr, daemon=True).start()
        deadline = time.monotonic() + 10.0
        last_error: Exception | None = None
        while time.monotonic() < deadline and not self.stop_requested:
            if self.rtl_process.poll() is not None:
                raise ScannerError(
                    "rtl_tcp exited: " + " | ".join(self.rtl_stderr)
                )
            try:
                self.rtl = RtlTcpClient("127.0.0.1", port)
                break
            except (OSError, ScannerError) as exc:
                last_error = exc
                time.sleep(0.2)
        if self.rtl is None:
            raise ScannerError(f"could not connect to rtl_tcp: {last_error}")
        scan_rate = int(self.worker.get("fft_scan_sample_rate") or 2_400_000)
        self.rtl.configure(
            scan_rate,
            float(self.worker.get("gain_db") or 49.6),
            int(self.worker.get("ppm") or 0),
        )
        self.current_rate = scan_rate

    def set_rate(self, sample_rate_hz: int) -> None:
        if self.rtl is None:
            raise ScannerError("RTL receiver is not connected")
        if self.current_rate != sample_rate_hz:
            self.rtl.set_sample_rate(sample_rate_hz)
            self.current_rate = sample_rate_hz
            self.rtl.drain_for(0.04)

    def scan_segment(self, segment: list[dict[str, Any]]) -> list[SpectrumCandidate]:
        if self.rtl is None:
            raise ScannerError("RTL receiver is not connected")
        scan_rate = int(self.worker.get("fft_scan_sample_rate") or 2_400_000)
        sample_count = int(self.worker.get("fft_scan_samples") or 65_536)
        discard = int(self.worker.get("fft_scan_discard_samples") or 96_000)
        center = segment_center_hz(segment, scan_rate)
        self.set_rate(scan_rate)
        self.rtl.tune(center)
        self.rtl.drain_for(
            float(self.worker.get("fft_scan_settle_seconds") or 0.08)
        )
        self.rtl.read_iq(discard)
        samples = self.rtl.read_iq(sample_count)
        available = []
        for channel in segment:
            frequency = int(channel["frequency_hz"])
            suppression, _ = analog_channel_suppression(frequency)
            if suppression:
                continue
            available.append(channel)
        candidates = spectrum_candidates(
            samples,
            center,
            scan_rate,
            available,
            float(self.worker.get("fft_carrier_snr_db") or 8.0),
        )
        now = time.monotonic()
        override_rise = float(
            self.worker.get("noise_cooldown_override_rise_db") or 6.0
        )
        return [
            candidate
            for candidate in candidates
            if candidate_is_available(
                int(candidate.channel.get("priority") or 0),
                candidate.snr_db,
                self.cooldown.get(int(candidate.channel["frequency_hz"]), 0.0),
                self.cooldown_baseline_snr.get(
                    int(candidate.channel["frequency_hz"])
                ),
                now,
                override_rise,
            )
        ]

    def _minimum_audio_rms(self, channel: dict[str, Any]) -> int:
        configured = int(
            channel.get("audio_activity_min_rms")
            or self.worker.get("audio_activity_min_rms")
            or 250
        )
        return max(0, configured + analog_squelch_offset())

    def candidate_rank_score(self, candidate: SpectrumCandidate) -> float:
        frequency = int(candidate.channel["frequency_hz"])
        if int(candidate.channel.get("priority") or 0) > 0:
            return candidate.snr_db
        return signal_rise_score(
            candidate.snr_db, self.cooldown_baseline_snr.get(frequency)
        )

    def validate_candidate(
        self, candidate: SpectrumCandidate
    ) -> tuple[bool, NfmDemodulator, list[np.ndarray], CarrierMetrics, AudioMetrics]:
        if self.rtl is None:
            raise ScannerError("RTL receiver is not connected")
        channel = candidate.channel
        frequency = int(channel["frequency_hz"])
        lock_rate = int(self.worker.get("nfm_sample_rate_hz") or 240_000)
        offset = int(self.worker.get("nfm_tuner_offset_hz") or 50_000)
        chunk_samples = int(self.worker.get("nfm_chunk_samples") or 24_000)
        configured_validation_chunks = int(
            self.worker.get("candidate_validation_chunks") or 5
        )
        validation_chunks = (
            min(
                configured_validation_chunks,
                int(self.worker.get("priority_candidate_validation_chunks") or 3),
            )
            if int(channel.get("priority") or 0) > 0
            else configured_validation_chunks
        )
        minimum_snr = float(self.worker.get("candidate_carrier_snr_db") or 8.0)
        maximum_error = float(
            self.worker.get("candidate_max_frequency_error_hz") or 6_000.0
        )
        precheck_chunks = min(
            validation_chunks,
            int(self.worker.get("candidate_precheck_chunks") or 2),
        )
        self.set_rate(lock_rate)
        self.rtl.tune(frequency + offset)
        self.rtl.drain_for(
            float(self.worker.get("candidate_settle_seconds") or 0.35)
        )
        self.rtl.read_iq(
            int(
                lock_rate
                * float(self.worker.get("candidate_discard_seconds") or 0.15)
            )
        )
        demodulator = NfmDemodulator(
            lock_rate,
            8_000,
            offset,
            float(self.worker.get("nfm_audio_output_gain") or 70_000.0),
        )
        pcm_chunks: list[np.ndarray] = []
        carrier_results: list[CarrierMetrics] = []
        for index in range(validation_chunks):
            iq = self.rtl.read_iq(chunk_samples)
            carrier_results.append(carrier_metrics(iq, lock_rate, -float(offset)))
            _, pcm = demodulator.process(iq)
            pcm_chunks.append(pcm)
            if index + 1 == precheck_chunks:
                promising = any(
                    item.snr_db >= minimum_snr
                    and abs(item.frequency_error_hz) <= maximum_error
                    for item in carrier_results
                )
                if not promising:
                    break
        carrier = CarrierMetrics(
            float(np.median([item.snr_db for item in carrier_results])),
            float(np.median([item.peak_offset_hz for item in carrier_results])),
            float(np.median([item.frequency_error_hz for item in carrier_results])),
        )
        combined = np.concatenate(pcm_chunks) if pcm_chunks else np.zeros(0, dtype="<i2")
        audio = audio_metrics(
            combined,
            minimum_rms=self._minimum_audio_rms(channel),
            maximum_flatness=float(self.worker.get("audio_noise_max_flatness") or 0.45),
            minimum_voice_ratio=float(self.worker.get("audio_min_voice_band_ratio") or 0.85),
        )
        required_good_chunks = int(
            self.worker.get("candidate_required_good_chunks") or 2
        )
        valid, good_chunks = candidate_validation_passes(
            carrier_results,
            audio,
            minimum_snr,
            maximum_error,
            required_good_chunks,
        )
        strong_carrier_snr = float(
            self.worker.get("candidate_strong_carrier_snr_db") or 40.0
        )
        required_strong_chunks = int(
            self.worker.get("candidate_required_strong_chunks") or 3
        )
        strong_carrier_probation, strong_chunks = strong_carrier_probation_passes(
            carrier_results,
            audio,
            strong_carrier_snr,
            maximum_error,
            required_strong_chunks,
        )
        strong_carrier_probation = (
            good_chunks >= required_good_chunks and strong_carrier_probation
        )
        valid = valid or strong_carrier_probation
        self.last_validation_details = {
            "accepted": valid,
            "strong_carrier_probation": strong_carrier_probation,
            "good_carrier_chunks": good_chunks,
            "required_good_carrier_chunks": required_good_chunks,
            "minimum_carrier_snr_db": minimum_snr,
            "strong_carrier_snr_db": strong_carrier_snr,
            "strong_carrier_chunks": strong_chunks,
            "required_strong_carrier_chunks": required_strong_chunks,
            "maximum_frequency_error_hz": maximum_error,
            "carrier_chunks": [asdict(item) for item in carrier_results],
            "carrier_median": asdict(carrier),
            "audio": asdict(audio),
        }
        return valid, demodulator, pcm_chunks, carrier, audio

    def _send_pcm(self, pcm: np.ndarray, pending: bytearray) -> None:
        pending.extend(np.asarray(pcm, dtype="<i2").tobytes())
        while len(pending) >= 320:
            frame = bytes(pending[:320])
            del pending[:320]
            self.frames_generated += 1
            if not self.no_forward:
                if self.udp is None:
                    raise ScannerError("audio UDP socket is closed")
                self.udp.sendto(frame, (self.udp_host, self.udp_port))
                self.frames_forwarded += 1

    def receive_channel(
        self,
        candidate: SpectrumCandidate,
        demodulator: NfmDemodulator,
        prebuffer: list[np.ndarray],
        initial_carrier: CarrierMetrics,
        initial_audio: AudioMetrics,
    ) -> None:
        if self.rtl is None:
            raise ScannerError("RTL receiver is not connected")
        channel = candidate.channel
        frequency = int(channel["frequency_hz"])
        lock_rate = demodulator.sample_rate_hz
        chunk_samples = int(self.worker.get("nfm_chunk_samples") or 24_000)
        carrier_release_snr = float(self.worker.get("carrier_release_snr_db") or 6.0)
        carrier_hang = carrier_release_hang_seconds(
            initial_carrier.snr_db,
            float(self.worker.get("carrier_release_seconds") or 0.45),
            float(self.worker.get("strong_carrier_release_seconds") or 1.5),
            float(self.worker.get("strong_carrier_audio_hold_snr_db") or 20.0),
        )
        audio_hang = float(self.worker.get("audio_release_seconds") or 1.5)
        maximum_call = float(self.worker.get("maximum_call_seconds") or 180.0)
        started = time.monotonic()
        last_carrier = started
        last_audio = started
        pending = bytearray()
        recent_pcm: deque[np.ndarray] = deque(maxlen=4)
        recorded_pcm: list[np.ndarray] = []
        maximum_recording_samples = 8_000 * int(
            self.worker.get("diagnostic_last_call_max_seconds") or 30
        )
        for pcm in prebuffer:
            if sum(len(item) for item in recorded_pcm) < maximum_recording_samples:
                recorded_pcm.append(pcm.copy())
            self._send_pcm(pcm, pending)
        self.lock_count += 1
        self.last_lock = {
            "frequency_hz": frequency,
            "name": channel.get("name"),
            "started_epoch": time.time(),
            "scan_snr_db": candidate.snr_db,
            "carrier_snr_db": initial_carrier.snr_db,
            "frequency_error_hz": initial_carrier.frequency_error_hz,
            "rms": initial_audio.rms,
            "audio_metrics": asdict(initial_audio),
        }
        lock_event = {
            "event": "locked",
            "epoch": time.time(),
            "frequency_hz": frequency,
            "name": channel.get("name"),
            "validation": self.last_validation_details,
        }
        self.recent_events.append(lock_event)
        self.channel_events[str(frequency)] = lock_event
        self.status(
            "locked",
            channel,
            lock_confirmed=True,
            carrier_snr_db=initial_carrier.snr_db,
            frequency_error_hz=initial_carrier.frequency_error_hz,
            rms=initial_audio.rms,
            audio_metrics=asdict(initial_audio),
        )
        heartbeat = 0.0
        release_reason: str | None = None
        clear_lock_generation = analog_clear_lock_generation()
        initially_strong_carrier = initial_carrier.snr_db >= float(
            self.worker.get("strong_carrier_audio_hold_snr_db") or 20.0
        )
        while not self.stop_requested:
            suppression, skip_until = analog_channel_suppression(frequency)
            if suppression:
                release_reason = f"operator_{suppression}"
                self.status(
                    suppression,
                    channel,
                    skip_until_epoch=skip_until,
                    release_reason=release_reason,
                )
                break
            if analog_clear_lock_generation() != clear_lock_generation:
                release_reason = "operator_clear_lock"
                self.status(
                    "clearing_lock",
                    channel,
                    lock_confirmed=True,
                    release_reason=release_reason,
                )
                break
            iq = self.rtl.read_iq(chunk_samples)
            carrier = carrier_metrics(iq, lock_rate, -float(demodulator.tuner_offset_hz))
            _, pcm = demodulator.process(iq)
            if sum(len(item) for item in recorded_pcm) < maximum_recording_samples:
                recorded_pcm.append(pcm.copy())
            recent_pcm.append(pcm)
            combined = np.concatenate(tuple(recent_pcm))
            audio = audio_metrics(
                combined,
                minimum_rms=self._minimum_audio_rms(channel),
                maximum_flatness=float(self.worker.get("audio_noise_max_flatness") or 0.45),
                minimum_voice_ratio=float(self.worker.get("audio_min_voice_band_ratio") or 0.85),
            )
            now = time.monotonic()
            if carrier.snr_db >= carrier_release_snr:
                last_carrier = now
            if call_audio_is_present(
                carrier,
                audio,
                self._minimum_audio_rms(channel),
                (
                    carrier_release_snr
                    if initially_strong_carrier
                    else float(
                        self.worker.get("strong_carrier_audio_hold_snr_db") or 20.0
                    )
                ),
            ):
                last_audio = now
            self._send_pcm(pcm, pending)
            release_reason = None
            if now - last_carrier >= carrier_hang:
                release_reason = "carrier_ended"
            elif now - last_audio >= audio_hang:
                release_reason = "audio_ended_or_noise_only"
            elif now - started >= maximum_call:
                release_reason = "maximum_call_time"
            elif self.run_deadline is not None and now >= self.run_deadline:
                release_reason = "smoke_deadline"
            if now >= heartbeat or release_reason:
                self.status(
                    "releasing" if release_reason else "locked",
                    channel,
                    lock_confirmed=True,
                    carrier_snr_db=carrier.snr_db,
                    frequency_error_hz=carrier.frequency_error_hz,
                    rms=audio.rms,
                    audio_metrics=asdict(audio),
                    lock_elapsed_seconds=round(now - started, 2),
                    release_reason=release_reason,
                )
                heartbeat = now + 0.25
            if release_reason:
                break
        self.cooldown[frequency] = time.monotonic() + float(
            self.worker.get("post_call_cooldown_seconds") or 0.35
        )
        recording = self.status_path.parent / "vhf_last_call.wav"
        samples = (
            np.concatenate(recorded_pcm)[:maximum_recording_samples]
            if recorded_pcm
            else np.zeros(0, dtype="<i2")
        )
        write_pcm_wav(recording, samples)
        self.last_lock["recording_path"] = str(recording)
        self.last_lock["recording_seconds"] = round(len(samples) / 8_000.0, 3)
        self.last_lock["release_reason"] = release_reason or "scanner_stopped"
        self.last_lock["lock_elapsed_seconds"] = round(time.monotonic() - started, 3)

    def run(self, maximum_seconds: float | None = None) -> int:
        run_started = time.monotonic()
        self.run_deadline = (
            run_started + maximum_seconds
            if maximum_seconds is not None
            else None
        )
        self.start_receiver()
        self.status("starting")
        while not self.stop_requested:
            if self.run_deadline is not None and time.monotonic() >= self.run_deadline:
                self.status("smoke_passed")
                return 0
            config_reloaded = self.load_configuration()
            candidates: list[SpectrumCandidate] = []
            sweep_started = time.monotonic()
            priority_short_circuit = False
            for index, segment in enumerate(self.segments, 1):
                if self.stop_requested:
                    break
                self.status(
                    "fft_scanning",
                    segment_index=index,
                    config_reloaded=config_reloaded,
                )
                segment_candidates = self.scan_segment(segment)
                priority_floor = float(
                    self.worker.get("priority_fft_carrier_snr_db") or 25.0
                )
                urgent = priority_candidates(segment_candidates, priority_floor)
                if urgent:
                    candidates = urgent
                    priority_short_circuit = True
                    break
                candidates.extend(
                    candidate
                    for candidate in segment_candidates
                    if int(candidate.channel.get("priority") or 0) <= 0
                    or candidate.snr_db >= priority_floor
                )
            self.sweeps += 1
            candidates.sort(
                key=lambda item: (
                    int(item.channel.get("priority") or 0),
                    self.candidate_rank_score(item),
                ),
                reverse=True,
            )
            self.status(
                "fft_candidates" if candidates else "fft_scanning",
                sweep_elapsed_seconds=round(time.monotonic() - sweep_started, 3),
                priority_short_circuit=priority_short_circuit,
                candidate_count=len(candidates),
                ranked_candidates=[
                    {
                        "name": item.channel.get("name"),
                        "frequency_hz": int(item.channel["frequency_hz"]),
                        "snr_db": round(item.snr_db, 2),
                        "rank_score_db": round(self.candidate_rank_score(item), 2),
                    }
                    for item in candidates[:12]
                ],
            )
            validation_limit = max(
                1,
                int(self.worker.get("maximum_candidate_validations_per_sweep") or 1),
            )
            for rank, candidate in enumerate(candidates[:validation_limit], 1):
                if self.stop_requested:
                    break
                self.last_candidate = {
                    "name": candidate.channel.get("name"),
                    "frequency_hz": int(candidate.channel["frequency_hz"]),
                    "scan_snr_db": round(candidate.snr_db, 2),
                    "rank_score_db": round(self.candidate_rank_score(candidate), 2),
                    "rank": rank,
                    "epoch": time.time(),
                }
                self.status("candidate_validating", candidate.channel, candidate_rank=rank)
                valid, demodulator, pcm, carrier, audio = self.validate_candidate(candidate)
                if not valid:
                    self.rejected_count += 1
                    frequency = int(candidate.channel["frequency_hz"])
                    good_chunks = int(
                        self.last_validation_details.get("good_carrier_chunks", 0)
                    )
                    required_chunks = int(
                        self.last_validation_details.get(
                            "required_good_carrier_chunks", 2
                        )
                    )
                    rejection_reason = (
                        "weak_or_off_frequency_carrier"
                        if good_chunks < required_chunks
                        else "no_nfm_audio_or_noise_only"
                    )
                    rejection_event = {
                        "event": "candidate_rejected",
                        "epoch": time.time(),
                        "frequency_hz": frequency,
                        "name": candidate.channel.get("name"),
                        "reason": rejection_reason,
                        "scan_snr_db": candidate.snr_db,
                        "validation": self.last_validation_details,
                    }
                    self.recent_events.append(rejection_event)
                    self.channel_events[str(frequency)] = rejection_event
                    self.cooldown[frequency] = time.monotonic() + float(
                        self.worker.get("noise_candidate_cooldown_seconds") or 15.0
                    )
                    self.cooldown_baseline_snr[frequency] = candidate.snr_db
                    self.status(
                        "candidate_rejected",
                        candidate.channel,
                        rejection_reason=rejection_reason,
                        carrier_metrics=asdict(carrier),
                        audio_metrics=asdict(audio),
                        validation=self.last_validation_details,
                    )
                    continue
                self.receive_channel(candidate, demodulator, pcm, carrier, audio)
                break
        self.status("stopped")
        return 0

    def close(self) -> None:
        if self.rtl is not None:
            self.rtl.close()
            self.rtl = None
        if self.rtl_process is not None and self.rtl_process.poll() is None:
            try:
                os.killpg(self.rtl_process.pid, signal.SIGTERM)
                self.rtl_process.wait(timeout=3.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.rtl_process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.udp is not None:
            self.udp.close()
            self.udp = None
        if self.last_state not in {"error", "smoke_passed", "stopped"}:
            try:
                self.status("stopped")
            except OSError:
                pass


def self_test() -> int:
    channels = [
        {"name": "A", "frequency_hz": 146_520_000, "mode": "fm", "enabled": True},
        {"name": "B", "frequency_hz": 154_340_000, "mode": "nfm", "enabled": True},
    ]
    if len(enabled_vhf_channels({"channels": channels})) != 2:
        raise ScannerError("channel normalization failed")
    tone = (np.sin(2.0 * math.pi * 1_000.0 * np.arange(4_000) / 8_000.0) * 4_000).astype("<i2")
    if not audio_metrics(tone).active:
        raise ScannerError("audio activity classifier rejected a voice-band tone")
    if audio_metrics(np.zeros(4_000, dtype="<i2")).active:
        raise ScannerError("audio activity classifier accepted silence")
    print("PASS: FFT-directed VHF NFM scanner self-test")
    print("FINAL: PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PI-SCANNER FFT-directed VHF NFM worker")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--smoke-seconds", type=float)
    parser.add_argument("--no-forward", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    scanner = VhfFftScanner(args.config, args.template, args.status_path, args.no_forward)
    signal.signal(signal.SIGTERM, scanner.request_stop)
    signal.signal(signal.SIGINT, scanner.request_stop)
    try:
        return scanner.run(args.smoke_seconds)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, ScannerError) as exc:
        scanner.status("error", error=f"{type(exc).__name__}: {exc}")
        print(f"FAIL: {exc}", flush=True)
        print("FINAL: FAIL", flush=True)
        return 1
    finally:
        scanner.close()


if __name__ == "__main__":
    raise SystemExit(main())

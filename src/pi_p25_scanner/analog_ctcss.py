# PI-SCANNER CTCSS tone detector.

from __future__ import annotations

import argparse
import array
import json
import math
import sys
import time
from collections import deque
from typing import Any

STANDARD_CTCSS_TONES = (
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0,
    127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8,
    162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5,
    186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7,
    218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
)


def pcm16le_samples(frame: bytes) -> list[int]:
    usable = len(frame) - (len(frame) % 2)
    values = array.array("h")
    values.frombytes(frame[:usable])
    if sys.byteorder != "little":
        values.byteswap()
    return [int(value) for value in values]


def goertzel_power(
    samples: list[int],
    frequency_hz: float,
    sample_rate_hz: int,
) -> float:
    count = len(samples)
    if count < 2:
        return 0.0
    bin_index = int(0.5 + (count * float(frequency_hz)) / sample_rate_hz)
    omega = (2.0 * math.pi * bin_index) / count
    coefficient = 2.0 * math.cos(omega)
    q1 = 0.0
    q2 = 0.0
    for sample in samples:
        q0 = coefficient * q1 - q2 + float(sample)
        q2 = q1
        q1 = q0
    return max(0.0, q1 * q1 + q2 * q2 - coefficient * q1 * q2)


def nearest_guard_tones(target_hz: float) -> tuple[float, float]:
    index = min(
        range(len(STANDARD_CTCSS_TONES)),
        key=lambda item: abs(STANDARD_CTCSS_TONES[item] - target_hz),
    )
    lower = (
        STANDARD_CTCSS_TONES[index - 1]
        if index > 0
        else target_hz * 0.94
    )
    upper = (
        STANDARD_CTCSS_TONES[index + 1]
        if index + 1 < len(STANDARD_CTCSS_TONES)
        else target_hz * 1.06
    )
    return float(lower), float(upper)


def tone_metrics(
    samples: list[int],
    target_hz: float,
    sample_rate_hz: int,
) -> dict[str, float]:
    if not samples:
        return {
            "normalized_power": 0.0,
            "dominance": 0.0,
            "confidence": 0.0,
        }
    signal_energy = sum(float(sample) * float(sample) for sample in samples)
    target_power = goertzel_power(samples, target_hz, sample_rate_hz)
    lower_hz, upper_hz = nearest_guard_tones(target_hz)
    guard_power = max(
        goertzel_power(samples, lower_hz, sample_rate_hz),
        goertzel_power(samples, upper_hz, sample_rate_hz),
    )
    normalized = target_power / (
        max(1.0, signal_energy * len(samples))
    )
    dominance = target_power / max(1.0, guard_power)
    confidence = min(1.0, normalized / 0.20) * min(1.0, dominance / 4.0)
    return {
        "normalized_power": float(normalized),
        "dominance": float(dominance),
        "confidence": float(confidence),
        "lower_guard_hz": lower_hz,
        "upper_guard_hz": upper_hz,
    }


class CtcssDetector:
    def __init__(
        self,
        target_hz: float,
        sample_rate_hz: int = 8000,
        window_seconds: float = 0.32,
        evaluation_seconds: float = 0.08,
        minimum_normalized_power: float = 0.05,
        minimum_dominance: float = 2.0,
        hits_to_lock: int = 2,
        misses_to_release: int = 3,
    ) -> None:
        self.target_hz = float(target_hz)
        self.sample_rate_hz = int(sample_rate_hz)
        self.window_samples = max(
            800,
            int(round(self.sample_rate_hz * float(window_seconds))),
        )
        self.evaluation_samples = max(
            160,
            int(round(self.sample_rate_hz * float(evaluation_seconds))),
        )
        self.minimum_normalized_power = float(minimum_normalized_power)
        self.minimum_dominance = float(minimum_dominance)
        self.hits_to_lock = max(1, int(hits_to_lock))
        self.misses_to_release = max(1, int(misses_to_release))
        self.samples: deque[int] = deque(maxlen=self.window_samples)
        self.samples_since_evaluation = 0
        self.consecutive_hits = 0
        self.consecutive_misses = 0
        self.locked = False
        self.confidence = 0.0
        self.normalized_power = 0.0
        self.dominance = 0.0
        self.evaluations = 0
        self.lock_events = 0
        self.last_match_utc: float | None = None
        self.last_evaluation_utc: float | None = None

    def reset(self) -> None:
        self.samples.clear()
        self.samples_since_evaluation = 0
        self.consecutive_hits = 0
        self.consecutive_misses = 0
        self.locked = False
        self.confidence = 0.0
        self.normalized_power = 0.0
        self.dominance = 0.0
        self.last_match_utc = None
        self.last_evaluation_utc = None

    def feed(self, frame: bytes) -> dict[str, Any]:
        values = pcm16le_samples(frame)
        self.samples.extend(values)
        self.samples_since_evaluation += len(values)
        if (
            len(self.samples) < self.window_samples
            or self.samples_since_evaluation < self.evaluation_samples
        ):
            return self.snapshot()

        self.samples_since_evaluation = 0
        metrics = tone_metrics(
            list(self.samples),
            self.target_hz,
            self.sample_rate_hz,
        )
        self.evaluations += 1
        self.last_evaluation_utc = time.time()
        self.confidence = metrics["confidence"]
        self.normalized_power = metrics["normalized_power"]
        self.dominance = metrics["dominance"]

        matched = (
            self.normalized_power >= self.minimum_normalized_power
            and self.dominance >= self.minimum_dominance
        )
        if matched:
            self.consecutive_hits += 1
            self.consecutive_misses = 0
            self.last_match_utc = self.last_evaluation_utc
            if not self.locked and self.consecutive_hits >= self.hits_to_lock:
                self.locked = True
                self.lock_events += 1
        else:
            self.consecutive_hits = 0
            self.consecutive_misses += 1
            if self.locked and self.consecutive_misses >= self.misses_to_release:
                self.locked = False

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "target_hz": self.target_hz,
            "locked": self.locked,
            "detected_hz": self.target_hz if self.locked else None,
            "confidence": round(self.confidence, 4),
            "normalized_power": round(self.normalized_power, 6),
            "dominance": round(self.dominance, 3),
            "consecutive_hits": self.consecutive_hits,
            "consecutive_misses": self.consecutive_misses,
            "evaluations": self.evaluations,
            "lock_events": self.lock_events,
            "last_match_utc": self.last_match_utc,
            "last_evaluation_utc": self.last_evaluation_utc,
            "window_samples": self.window_samples,
        }


def synthetic_pcm(
    frequency_hz: float,
    seconds: float,
    amplitude: int = 5000,
    sample_rate_hz: int = 8000,
) -> bytes:
    values = array.array("h")
    count = int(round(seconds * sample_rate_hz))
    for index in range(count):
        values.append(
            int(
                amplitude
                * math.sin(
                    2.0
                    * math.pi
                    * frequency_hz
                    * index
                    / sample_rate_hz
                )
            )
        )
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def feed_in_frames(
    detector: CtcssDetector,
    payload: bytes,
    frame_bytes: int = 320,
) -> dict[str, Any]:
    result = detector.snapshot()
    for offset in range(0, len(payload), frame_bytes):
        result = detector.feed(payload[offset : offset + frame_bytes])
    return result


def self_test() -> int:
    target = CtcssDetector(100.0)
    correct = feed_in_frames(target, synthetic_pcm(100.0, 0.8))
    wrong_detector = CtcssDetector(100.0)
    wrong = feed_in_frames(
        wrong_detector,
        synthetic_pcm(103.5, 0.8),
    )
    release_payload = synthetic_pcm(350.0, 0.8)
    released = feed_in_frames(target, release_payload)

    checks = [
        correct["locked"] is True,
        float(correct["confidence"]) >= 0.5,
        wrong["locked"] is False,
        released["locked"] is False,
        int(correct["lock_events"]) >= 1,
    ]
    if not all(checks):
        print(
            json.dumps(
                {
                    "correct": correct,
                    "wrong": wrong,
                    "released": released,
                    "checks": checks,
                },
                indent=2,
            )
        )
        print("FINAL: FAIL")
        return 1

    print(
        json.dumps(
            {
                "correct_tone": correct,
                "wrong_tone": wrong,
                "released": released,
            },
            indent=2,
        )
    )
    print("PASS: CTCSS detector self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER CTCSS detector"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    parser.error("no action selected")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

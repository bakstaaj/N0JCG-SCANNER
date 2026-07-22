# PI-SCANNER DCS / DPL decoder for demodulated 8 kHz PCM audio.

from __future__ import annotations

import argparse
import array
import json
import math
import re
import sys
import time
from typing import Any

DCS_BIT_RATE = 134.4
DCS_CODEWORD_BITS = 23
DCS_CODEWORD_MASK = (1 << DCS_CODEWORD_BITS) - 1
GOLAY_23_12_GENERATOR = 0xAE3
DEFAULT_PHASE_HYPOTHESES = 8
DEFAULT_DISTANCE_THRESHOLD = 2
DEFAULT_HITS_TO_LOCK = 12
DEFAULT_MISSES_TO_RELEASE = 40


class DcsError(RuntimeError):
    pass


def parse_dcs_code(value: str) -> dict[str, str]:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"([0-7]{3})([NI])?", text)
    if not match:
        raise DcsError(
            f"DCS code must be three octal digits with optional N or I suffix: {value!r}"
        )
    suffix = match.group(2) or ""
    return {
        "code": match.group(1),
        "requested_polarity": suffix or "BOTH",
        "display": match.group(1) + suffix,
    }


def golay_encode_23_12(data: int) -> int:
    data &= 0xFFF
    register = data << 11
    for bit in range(22, 10, -1):
        if register & (1 << bit):
            register ^= GOLAY_23_12_GENERATOR << (bit - 11)
    parity = register & 0x7FF
    return ((data << 11) | parity) & DCS_CODEWORD_MASK


def dcs_codeword(value: str) -> int:
    parsed = parse_dcs_code(value)
    information = (int(parsed["code"], 8) << 3) | 0b100
    return golay_encode_23_12(information)


def cyclic_left_23(value: int) -> int:
    value &= DCS_CODEWORD_MASK
    most_significant = (value >> 22) & 1
    return ((value << 1) | most_significant) & DCS_CODEWORD_MASK


def dcs_targets(value: str) -> list[dict[str, Any]]:
    parsed = parse_dcs_code(value)
    base = dcs_codeword(parsed["code"])
    targets: list[dict[str, Any]] = []
    current = base
    for rotation in range(23):
        if parsed["requested_polarity"] in ("BOTH", "N"):
            targets.append(
                {
                    "word": current,
                    "polarity": "N",
                    "rotation": rotation,
                }
            )
        if parsed["requested_polarity"] in ("BOTH", "I"):
            targets.append(
                {
                    "word": (~current) & DCS_CODEWORD_MASK,
                    "polarity": "I",
                    "rotation": rotation,
                }
            )
        current = cyclic_left_23(current)
    return targets


def pcm16le_samples(frame: bytes) -> list[int]:
    usable = len(frame) - (len(frame) % 2)
    values = array.array("h")
    values.frombytes(frame[:usable])
    if sys.byteorder != "little":
        values.byteswap()
    return [int(value) for value in values]


def hamming_distance(left: int, right: int) -> int:
    return int((left ^ right).bit_count())


class _BitTimingHypothesis:
    def __init__(self, initial_phase: float) -> None:
        self.phase = float(initial_phase)
        self.accumulator = 0.0
        self.window = 0
        self.bits_have = 0
        self.bits_total = 0
        self.hit_streak = 0
        self.miss_streak = 0
        self.last_polarity = ""
        self.best_distance = 23
        self.last_rotation: int | None = None
        self.last_match_utc: float | None = None

    def reset_match_state(self) -> None:
        self.hit_streak = 0
        self.miss_streak = 0
        self.last_polarity = ""
        self.best_distance = 23
        self.last_rotation = None
        self.last_match_utc = None


class DcsDetector:
    def __init__(
        self,
        code: str,
        sample_rate_hz: int = 8000,
        lowpass_hz: float = 250.0,
        phase_hypotheses: int = DEFAULT_PHASE_HYPOTHESES,
        distance_threshold: int = DEFAULT_DISTANCE_THRESHOLD,
        hits_to_lock: int = DEFAULT_HITS_TO_LOCK,
        misses_to_release: int = DEFAULT_MISSES_TO_RELEASE,
    ) -> None:
        parsed = parse_dcs_code(code)
        self.code = parsed["code"]
        self.configured_code = parsed["display"]
        self.requested_polarity = parsed["requested_polarity"]
        self.sample_rate_hz = int(sample_rate_hz)
        if self.sample_rate_hz <= 0:
            raise DcsError("sample rate must be positive")
        self.samples_per_bit = self.sample_rate_hz / DCS_BIT_RATE
        dt = 1.0 / self.sample_rate_hz
        rc = 1.0 / (2.0 * math.pi * float(lowpass_hz))
        self.lowpass_alpha = dt / (rc + dt)
        self.lowpass_state = 0.0
        self.targets = dcs_targets(self.configured_code)
        self.distance_threshold = max(0, int(distance_threshold))
        self.hits_to_lock = max(1, int(hits_to_lock))
        self.misses_to_release = max(1, int(misses_to_release))
        count = max(1, int(phase_hypotheses))
        self.hypotheses = [
            _BitTimingHypothesis(index / count)
            for index in range(count)
        ]
        self.locked = False
        self.locked_hypothesis: int | None = None
        self.detected_code: str | None = None
        self.detected_polarity: str | None = None
        self.confidence = 0.0
        self.best_distance = 23
        self.last_rotation: int | None = None
        self.last_match_utc: float | None = None
        self.last_evaluation_utc: float | None = None
        self.evaluations = 0
        self.match_windows = 0
        self.lock_events = 0
        self.release_events = 0

    def reset(self) -> None:
        self.lowpass_state = 0.0
        for index, hypothesis in enumerate(self.hypotheses):
            hypothesis.phase = index / len(self.hypotheses)
            hypothesis.accumulator = 0.0
            hypothesis.window = 0
            hypothesis.bits_have = 0
            hypothesis.bits_total = 0
            hypothesis.reset_match_state()
        self.locked = False
        self.locked_hypothesis = None
        self.detected_code = None
        self.detected_polarity = None
        self.confidence = 0.0
        self.best_distance = 23
        self.last_rotation = None
        self.last_match_utc = None
        self.last_evaluation_utc = None

    def _best_target(self, window: int) -> tuple[int, str, int]:
        best_distance = 24
        best_polarity = ""
        best_rotation = 0
        for target in self.targets:
            distance = hamming_distance(window, int(target["word"]))
            if distance < best_distance:
                best_distance = distance
                best_polarity = str(target["polarity"])
                best_rotation = int(target["rotation"])
                if distance == 0:
                    break
        return best_distance, best_polarity, best_rotation

    def _evaluate_hypothesis(
        self,
        index: int,
        hypothesis: _BitTimingHypothesis,
    ) -> None:
        distance, polarity, rotation = self._best_target(hypothesis.window)
        self.evaluations += 1
        self.last_evaluation_utc = time.time()
        hypothesis.best_distance = distance
        hypothesis.last_rotation = rotation

        if distance <= self.distance_threshold:
            if hypothesis.last_polarity == polarity:
                hypothesis.hit_streak += 1
            else:
                hypothesis.hit_streak = 1
                hypothesis.last_polarity = polarity
            hypothesis.miss_streak = 0
            hypothesis.last_match_utc = self.last_evaluation_utc
            self.match_windows += 1

            if hypothesis.hit_streak >= self.hits_to_lock:
                newly_locked = (
                    not self.locked
                    or self.locked_hypothesis != index
                    or self.detected_polarity != polarity
                )
                self.locked = True
                self.locked_hypothesis = index
                self.detected_code = self.code
                self.detected_polarity = polarity
                self.best_distance = distance
                self.last_rotation = rotation
                self.last_match_utc = self.last_evaluation_utc
                self.confidence = max(
                    0.0,
                    min(
                        1.0,
                        1.0
                        - (
                            distance
                            / float(self.distance_threshold + 1)
                        ),
                    ),
                )
                if newly_locked:
                    self.lock_events += 1
        else:
            hypothesis.hit_streak = 0
            hypothesis.miss_streak += 1
            if (
                self.locked
                and self.locked_hypothesis == index
                and hypothesis.miss_streak >= self.misses_to_release
            ):
                self.locked = False
                self.locked_hypothesis = None
                self.detected_code = None
                self.detected_polarity = None
                self.confidence = 0.0
                self.release_events += 1

    def feed(self, frame: bytes) -> dict[str, Any]:
        step = 1.0 / self.samples_per_bit
        for sample in pcm16le_samples(frame):
            self.lowpass_state += self.lowpass_alpha * (
                float(sample) - self.lowpass_state
            )
            for index, hypothesis in enumerate(self.hypotheses):
                hypothesis.accumulator += self.lowpass_state
                hypothesis.phase += step
                if hypothesis.phase < 1.0:
                    continue

                bit = 1 if hypothesis.accumulator >= 0.0 else 0
                hypothesis.window = (
                    (hypothesis.window << 1) | bit
                ) & DCS_CODEWORD_MASK
                hypothesis.accumulator = 0.0
                hypothesis.phase -= 1.0
                hypothesis.bits_total += 1
                hypothesis.bits_have = min(
                    DCS_CODEWORD_BITS,
                    hypothesis.bits_have + 1,
                )
                if hypothesis.bits_have >= DCS_CODEWORD_BITS:
                    self._evaluate_hypothesis(index, hypothesis)

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        selected = (
            self.hypotheses[self.locked_hypothesis]
            if self.locked_hypothesis is not None
            else None
        )
        return {
            "configured_code": self.configured_code,
            "base_code": self.code,
            "requested_polarity": self.requested_polarity,
            "locked": self.locked,
            "detected_code": self.detected_code,
            "detected_polarity": self.detected_polarity,
            "confidence": round(self.confidence, 4),
            "best_distance": self.best_distance,
            "last_rotation": self.last_rotation,
            "locked_hypothesis": self.locked_hypothesis,
            "locked_hit_streak": (
                selected.hit_streak if selected is not None else 0
            ),
            "locked_miss_streak": (
                selected.miss_streak if selected is not None else 0
            ),
            "last_match_utc": self.last_match_utc,
            "last_evaluation_utc": self.last_evaluation_utc,
            "evaluations": self.evaluations,
            "match_windows": self.match_windows,
            "lock_events": self.lock_events,
            "release_events": self.release_events,
            "phase_hypotheses": len(self.hypotheses),
            "distance_threshold": self.distance_threshold,
            "samples_per_bit": round(self.samples_per_bit, 6),
        }


def synthetic_dcs_pcm(
    code: str,
    seconds: float = 1.6,
    sample_rate_hz: int = 8000,
    amplitude: int = 6000,
    inverted: bool = False,
    bit_phase_offset: float = 0.37,
) -> bytes:
    word = dcs_codeword(code)
    if inverted:
        word = (~word) & DCS_CODEWORD_MASK
    bits = [
        (word >> (22 - index)) & 1
        for index in range(DCS_CODEWORD_BITS)
    ]
    values = array.array("h")
    total = int(round(float(seconds) * sample_rate_hz))
    for sample_index in range(total):
        bit_index = int(
            (
                sample_index
                * DCS_BIT_RATE
                / sample_rate_hz
                + float(bit_phase_offset)
            )
        ) % DCS_CODEWORD_BITS
        values.append(amplitude if bits[bit_index] else -amplitude)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def feed_in_frames(
    detector: DcsDetector,
    payload: bytes,
    frame_bytes: int = 320,
) -> dict[str, Any]:
    result = detector.snapshot()
    for offset in range(0, len(payload), frame_bytes):
        result = detector.feed(payload[offset : offset + frame_bytes])
    return result


def self_test() -> int:
    normal_detector = DcsDetector("023")
    normal = feed_in_frames(
        normal_detector,
        synthetic_dcs_pcm("023"),
    )

    inverted_detector = DcsDetector("023")
    inverted = feed_in_frames(
        inverted_detector,
        synthetic_dcs_pcm("023", inverted=True),
    )

    wrong_detector = DcsDetector("023")
    wrong = feed_in_frames(
        wrong_detector,
        synthetic_dcs_pcm("754"),
    )

    release_payload = array.array(
        "h",
        [5000 if index % 2 else -5000 for index in range(8000)],
    )
    if sys.byteorder != "little":
        release_payload.byteswap()
    released = feed_in_frames(normal_detector, release_payload.tobytes())

    phase_results = []
    for phase in (0.0, 0.11, 0.25, 0.49, 0.73, 0.91):
        detector = DcsDetector("023")
        result = feed_in_frames(
            detector,
            synthetic_dcs_pcm(
                "023",
                bit_phase_offset=phase,
            ),
        )
        phase_results.append(
            {
                "phase": phase,
                "locked": result["locked"],
                "polarity": result["detected_polarity"],
            }
        )

    codeword = dcs_codeword("023")
    info = (codeword >> 11) & 0xFFF
    rotations = dcs_targets("023")

    checks = [
        normal["locked"] is True,
        normal["detected_code"] == "023",
        normal["detected_polarity"] == "N",
        inverted["locked"] is True,
        inverted["detected_polarity"] == "I",
        wrong["locked"] is False,
        released["locked"] is False,
        info == 0b000_010_011_100,
        len(rotations) == 46,
        all(item["locked"] for item in phase_results),
    ]

    if not all(checks):
        print(
            json.dumps(
                {
                    "normal": normal,
                    "inverted": inverted,
                    "wrong": wrong,
                    "released": released,
                    "phase_results": phase_results,
                    "codeword": f"{codeword:023b}",
                    "information": f"{info:012b}",
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
                "normal": normal,
                "inverted": inverted,
                "wrong": wrong,
                "released": released,
                "phase_results": phase_results,
                "codeword": f"{codeword:023b}",
                "information": f"{info:012b}",
            },
            indent=2,
        )
    )
    print("PASS: DCS detector self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER DCS decoder"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--code", default="023")
    parser.add_argument("--print-codeword", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.print_codeword:
        parsed = parse_dcs_code(args.code)
        word = dcs_codeword(args.code)
        print(
            json.dumps(
                {
                    "configured_code": parsed["display"],
                    "base_code": parsed["code"],
                    "requested_polarity": parsed["requested_polarity"],
                    "codeword_hex": f"0x{word:06x}",
                    "codeword_bits": f"{word:023b}",
                    "target_count": len(dcs_targets(args.code)),
                },
                indent=2,
            )
        )
        return 0
    parser.error("no action selected")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

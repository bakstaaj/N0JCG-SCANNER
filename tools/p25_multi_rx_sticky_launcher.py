#!/usr/bin/env python3
"""Run OP25 multi_rx with a sticky control-channel timeout policy."""

from __future__ import annotations

import argparse
import importlib
import json
import runpy
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

MARKER = "PI_P25_STICKY_CONTROL_V1"


def _frequency_text(value: Any) -> str:
    try:
        frequency = int(value)
    except (TypeError, ValueError):
        return "-"
    return f"{frequency / 1_000_000:.6f}MHz" if frequency > 0 else "-"


def _emit(payload: dict[str, Any]) -> None:
    record = {
        "event": "sticky_cc_timeout",
        "marker": MARKER,
        "timestamp": time.time(),
        **payload,
    }
    sys.stderr.write(
        "PI_P25_STICKY_CC "
        + json.dumps(record, sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    sys.stderr.flush()


def apply_sticky_control(tk_p25: Any, retries: int) -> None:
    if retries < 2:
        raise ValueError("retries must be at least 2")

    tk_p25.CC_TIMEOUT_RETRIES = retries
    original = tk_p25.p25_system.timeout_cc

    if getattr(original, "_pi_p25_sticky_wrapped", False):
        return

    def timeout_cc(self: Any, msgq_id: Any) -> Any:
        before_retries = int(getattr(self, "cc_retries", 0) or 0)
        before_index = int(getattr(self, "cc_index", -1) or -1)
        cc_list = list(getattr(self, "cc_list", []) or [])
        before_frequency = (
            cc_list[before_index]
            if 0 <= before_index < len(cc_list)
            else None
        )

        result = original(self, msgq_id)

        after_retries = int(getattr(self, "cc_retries", 0) or 0)
        after_index = int(getattr(self, "cc_index", -1) or -1)
        after_frequency = (
            cc_list[after_index]
            if 0 <= after_index < len(cc_list)
            else None
        )
        switched = after_index != before_index

        if after_retries != before_retries or switched:
            _emit(
                {
                    "system": str(getattr(self, "sysname", "")),
                    "receiver": msgq_id,
                    "retry_before": before_retries,
                    "retry_after": after_retries,
                    "retry_threshold": retries,
                    "index_before": before_index,
                    "index_after": after_index,
                    "frequency_before_hz": before_frequency,
                    "frequency_after_hz": after_frequency,
                    "frequency_before": _frequency_text(before_frequency),
                    "frequency_after": _frequency_text(after_frequency),
                    "switched": switched,
                }
            )

        return result

    timeout_cc._pi_p25_sticky_wrapped = True
    timeout_cc._pi_p25_original = original
    tk_p25.p25_system.timeout_cc = timeout_cc


def _self_test() -> int:
    fake_module = SimpleNamespace()
    fake_module.CC_TIMEOUT_RETRIES = 3

    class FakeSystem:
        def __init__(self) -> None:
            self.sysname = "self-test"
            self.cc_retries = 0
            self.cc_index = 0
            self.cc_list = [852_225_000, 853_300_000]

        def timeout_cc(self, msgq_id: int) -> int:
            self.cc_retries += 1
            if self.cc_retries >= fake_module.CC_TIMEOUT_RETRIES:
                self.cc_retries = 0
                self.cc_index = (self.cc_index + 1) % len(self.cc_list)
            return self.cc_list[self.cc_index]

    fake_module.p25_system = FakeSystem
    apply_sticky_control(fake_module, 10)

    system = FakeSystem()
    for _ in range(9):
        system.timeout_cc(0)
    assert system.cc_index == 0
    assert system.cc_retries == 9

    system.timeout_cc(0)
    assert system.cc_index == 1
    assert system.cc_retries == 0
    assert fake_module.CC_TIMEOUT_RETRIES == 10

    print("STICKY_CONTROL_SELF_TEST=PASS")
    print("CC_TIMEOUT_RETRIES=10")
    print("SWITCH_AFTER_CONSECUTIVE_TIMEOUTS=10")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc-timeout-retries", type=int, default=10)
    parser.add_argument("--app", type=Path)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("app_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.app is None:
        raise SystemExit("--app is required")
    if not args.app.is_file():
        raise SystemExit(f"multi_rx app not found: {args.app}")
    if not 4 <= args.cc_timeout_retries <= 30:
        raise SystemExit("--cc-timeout-retries must be 4..30")

    app_dir = str(args.app.parent.resolve())
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    tk_p25 = importlib.import_module("tk_p25")
    apply_sticky_control(tk_p25, args.cc_timeout_retries)

    forwarded = list(args.app_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    sys.stderr.write(
        "PI_P25_STICKY_CC "
        + json.dumps(
            {
                "event": "sticky_cc_enabled",
                "marker": MARKER,
                "timestamp": time.time(),
                "retry_threshold": args.cc_timeout_retries,
                "multi_rx_app": str(args.app),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stderr.flush()

    sys.argv = [str(args.app), *forwarded]
    runpy.run_path(str(args.app), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

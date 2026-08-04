#!/usr/bin/env python3
"""Capture temporary OP25 spectrum/constellation plots for one receiver."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from p25_terminal_diagnostic import post_command


PLOTS = {"spectrum": 1, "constellation": 2}


def plot_files(messages: list[dict[str, Any]], channel: int) -> list[str]:
    prefix = f"plot-{channel}-"
    for message in messages:
        if message.get("json_type") != "rx_update":
            continue
        return [
            str(item)
            for item in message.get("files", [])
            if prefix in str(item)
        ]
    return []


def receiver_state(messages: list[dict[str, Any]], channel: int) -> dict[str, Any]:
    for message in messages:
        if message.get("json_type") == "channel_update":
            state = message.get(str(channel))
            return state if isinstance(state, dict) else {}
    return {}


def safe_name(url: str) -> str:
    name = Path(urlparse(url).path).name
    if not name or name in {".", ".."}:
        raise ValueError(f"unsafe plot URL: {url!r}")
    return name


def capture(
    url: str,
    channel: int,
    output_dir: Path,
    settle_seconds: float,
    target_frequency_hz: int | None = None,
    wait_seconds: float = 60.0,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    toggled: list[int] = []
    try:
        observed_state: dict[str, Any] = {}
        if target_frequency_hz is not None:
            wait_deadline = time.time() + wait_seconds
            while time.time() < wait_deadline:
                messages = post_command(url, "update", channel=channel)
                observed_state = receiver_state(messages, channel)
                if observed_state.get("freq") == target_frequency_hz:
                    break
                time.sleep(0.1)
            if observed_state.get("freq") != target_frequency_hz:
                raise TimeoutError(
                    f"receiver {channel} did not tune to {target_frequency_hz} Hz"
                )
        for plot_id in PLOTS.values():
            post_command(url, "toggle_plot", channel=channel, arg1=plot_id)
            toggled.append(plot_id)
        deadline = time.time() + settle_seconds
        files: list[str] = []
        while time.time() < deadline:
            files = plot_files(post_command(url, "update", channel=channel), channel)
            if len(files) >= len(PLOTS):
                break
            time.sleep(0.25)
        saved = []
        for item in files:
            source = urljoin(url, item)
            target = output_dir / safe_name(source)
            with urlopen(source, timeout=5) as response:
                target.write_bytes(response.read())
            saved.append({"source": source, "file": str(target)})
        report = {
            "utc": time.time(),
            "channel": channel,
            "target_frequency_hz": target_frequency_hz,
            "observed_state": observed_state,
            "plot_ids": PLOTS,
            "reported_files": files,
            "saved_files": saved,
        }
        (output_dir / "plot_snapshot.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        for plot_id in reversed(toggled):
            try:
                post_command(url, "toggle_plot", channel=channel, arg1=plot_id)
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18091/")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--target-frequency-hz", type=int)
    parser.add_argument("--wait-seconds", type=float, default=60.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = capture(
        args.url,
        args.channel,
        args.output_dir,
        args.settle_seconds,
        target_frequency_hz=args.target_frequency_hz,
        wait_seconds=args.wait_seconds,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["saved_files"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

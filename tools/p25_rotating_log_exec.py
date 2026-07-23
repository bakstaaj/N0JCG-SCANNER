#!/usr/bin/env python3
# Run a child while teeing combined stdout/stderr to a bounded rotating log.

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO


class RotatingBinaryLog:
    def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.handle: BinaryIO | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("ab", buffering=0)

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def rotate(self) -> None:
        self.close()
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        try:
            oldest.unlink()
        except FileNotFoundError:
            pass

        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.exists():
                os.replace(source, target)

        if self.path.exists():
            os.replace(
                self.path,
                self.path.with_name(f"{self.path.name}.1"),
            )
        self.handle = self.path.open("ab", buffering=0)

    def write(self, payload: bytes) -> None:
        if not payload:
            return
        size = self.path.stat().st_size if self.path.exists() else 0
        if size > 0 and size + len(payload) > self.max_bytes:
            self.rotate()
        assert self.handle is not None
        self.handle.write(payload)


def marker(event: str, **fields: object) -> bytes:
    payload = {
        "marker": "PI_P25_ROTATING_RUNTIME_LOG_V1",
        "event": event,
        "timestamp": time.time(),
        **fields,
    }
    return (
        "PI_P25_RUNTIME_LOG "
        + json.dumps(payload, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8", errors="replace")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--backups", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("child command missing")
    if args.max_bytes < 1024:
        parser.error("--max-bytes must be at least 1024")
    if not 1 <= args.backups <= 100:
        parser.error("--backups must be 1 through 100")
    return args


def main() -> int:
    args = parse_args()
    log = RotatingBinaryLog(
        Path(args.log).expanduser().resolve(),
        args.max_bytes,
        args.backups,
    )
    child: subprocess.Popen[bytes] | None = None
    forwarded_signal = False

    def emit(payload: bytes) -> None:
        log.write(payload)
        try:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
        except BrokenPipeError:
            pass

    def signal_child(signum: int) -> None:
        if child is None or child.poll() is not None:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(child.pid, signum)
            else:
                child.send_signal(signum)
        except (OSError, ProcessLookupError, ValueError):
            pass

    def forward(signum: int, _frame: object) -> None:
        nonlocal forwarded_signal
        forwarded_signal = True
        signal_child(signum)

    handled_signals = [signal.SIGTERM, signal.SIGINT]
    sighup = getattr(signal, "SIGHUP", None)
    if sighup is not None:
        handled_signals.append(sighup)
    for sig in handled_signals:
        signal.signal(sig, forward)

    emit(
        marker(
            "start",
            logger_pid=os.getpid(),
            log_path=str(log.path),
            max_bytes=args.max_bytes,
            backups=args.backups,
            command=args.command,
        )
    )

    return_code = 127
    try:
        child = subprocess.Popen(
            args.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": os.environ.get(
                    "PYTHONIOENCODING",
                    "utf-8",
                ),
            },
        )
        assert child.stdout is not None

        while True:
            chunk = child.stdout.read(65536)
            if chunk:
                emit(chunk)
                continue
            if child.poll() is not None:
                break
            time.sleep(0.02)

        return_code = child.wait()
    finally:
        if child is not None and child.poll() is None:
            signal_child(signal.SIGTERM)
            try:
                return_code = child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                signal_child(getattr(signal, "SIGKILL", signal.SIGTERM))
                return_code = child.wait()

        emit(
            marker(
                "exit",
                logger_pid=os.getpid(),
                child_pid=(child.pid if child is not None else None),
                return_code=return_code,
                signal_forwarded=forwarded_signal,
            )
        )
        log.close()

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

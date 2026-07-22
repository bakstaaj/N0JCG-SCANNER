# PI-SCANNER analog WAV recording, retention, and playback metadata.

from __future__ import annotations

import argparse
import array
import json
import math
import re
import shutil
import tempfile
import time
import wave
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RECORDINGS_DIR = PROJECT_ROOT / "runtime" / "recordings"
VALID_ROLES = ("analog_2m", "analog_70cm")
PCM_RATE_HZ = 8000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_FILES_PER_ROLE = 500
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_RECORDING_SECONDS = 900.0


class AnalogRecordingError(RuntimeError):
    pass


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-._")
    if not cleaned:
        raise AnalogRecordingError("recording filename is empty after sanitization")
    return cleaned[:180]


def role_recording_dir(
    role: str,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> Path:
    if role not in VALID_ROLES:
        raise AnalogRecordingError(f"unsupported analog recording role: {role}")
    return Path(recordings_dir) / role


def recording_url(role: str, filename: str) -> str:
    return (
        "/api/analog/recordings/file"
        f"?role={quote(role)}&filename={quote(filename)}"
    )


def resolve_recording_file(
    role: str,
    filename: str,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> Path:
    role_dir = role_recording_dir(role, recordings_dir=recordings_dir).resolve()
    candidate_name = safe_filename(filename)
    if candidate_name != filename:
        raise AnalogRecordingError("unsafe recording filename")
    candidate = (role_dir / candidate_name).resolve()
    if candidate.parent != role_dir:
        raise AnalogRecordingError("recording path escaped role directory")
    if candidate.suffix.lower() != ".wav":
        raise AnalogRecordingError("recording file must be WAV")
    if not candidate.is_file():
        raise AnalogRecordingError(f"recording file not found: {candidate_name}")
    return candidate


class WavRecordingSession:
    def __init__(
        self,
        role: str,
        event_id: str,
        recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
        rate_hz: int = PCM_RATE_HZ,
    ) -> None:
        self.role = role
        self.event_id = safe_filename(event_id)
        self.rate_hz = int(rate_hz)
        self.directory = role_recording_dir(role, recordings_dir=recordings_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.filename = safe_filename(self.event_id + ".wav")
        self.path = self.directory / self.filename
        self.writer = wave.open(str(self.path), "wb")
        self.writer.setnchannels(PCM_CHANNELS)
        self.writer.setsampwidth(PCM_SAMPLE_WIDTH)
        self.writer.setframerate(self.rate_hz)
        self.frames_written = 0
        self.closed = False
        self.truncated = False

    def write(self, pcm: bytes) -> None:
        if self.closed or not pcm:
            return
        max_frames = int(MAX_RECORDING_SECONDS * self.rate_hz)
        samples = len(pcm) // PCM_SAMPLE_WIDTH
        if self.frames_written + samples > max_frames:
            allowed_samples = max(0, max_frames - self.frames_written)
            pcm = pcm[: allowed_samples * PCM_SAMPLE_WIDTH]
            self.truncated = True
        if pcm:
            self.writer.writeframesraw(pcm)
            self.frames_written += len(pcm) // PCM_SAMPLE_WIDTH

    def close(self) -> dict[str, Any]:
        if not self.closed:
            self.writer.close()
            self.closed = True
        duration = self.frames_written / float(self.rate_hz)
        size = self.path.stat().st_size if self.path.exists() else 0
        return {
            "recording_filename": self.filename,
            "recording_path": str(self.path),
            "recording_url": recording_url(self.role, self.filename),
            "recording_size_bytes": size,
            "recording_duration_seconds": round(duration, 3),
            "recording_truncated": self.truncated,
        }

    def abort(self) -> None:
        try:
            if not self.closed:
                self.writer.close()
                self.closed = True
        finally:
            self.path.unlink(missing_ok=True)


def list_recordings(
    role: str | None = None,
    limit: int = 200,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> list[dict[str, Any]]:
    roles = [role] if role else list(VALID_ROLES)
    items: list[dict[str, Any]] = []
    for item_role in roles:
        directory = role_recording_dir(item_role, recordings_dir=recordings_dir)
        if not directory.exists():
            continue
        for path in directory.glob("*.wav"):
            try:
                stat = path.stat()
                with wave.open(str(path), "rb") as reader:
                    frames = reader.getnframes()
                    rate = reader.getframerate()
                    channels = reader.getnchannels()
                    width = reader.getsampwidth()
                duration = frames / float(rate or PCM_RATE_HZ)
            except (OSError, wave.Error):
                continue
            items.append(
                {
                    "role": item_role,
                    "filename": path.name,
                    "path": str(path),
                    "url": recording_url(item_role, path.name),
                    "size_bytes": stat.st_size,
                    "modified_utc": stat.st_mtime,
                    "duration_seconds": round(duration, 3),
                    "rate_hz": rate,
                    "channels": channels,
                    "sample_width_bytes": width,
                }
            )
    items.sort(key=lambda item: float(item["modified_utc"]), reverse=True)
    return items[: max(1, min(int(limit), 1000))]


def recordings_payload(
    role: str | None = None,
    limit: int = 200,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> dict[str, Any]:
    items = list_recordings(
        role=role,
        limit=limit,
        recordings_dir=recordings_dir,
    )
    return {
        "ok": True,
        "recordings_dir": str(recordings_dir),
        "recording_count": len(items),
        "total_size_bytes": sum(int(item["size_bytes"]) for item in items),
        "total_duration_seconds": round(
            sum(float(item["duration_seconds"]) for item in items),
            3,
        ),
        "items": items,
        "updated_utc": time.time(),
    }


def delete_recording(
    role: str,
    filename: str,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> dict[str, Any]:
    path = resolve_recording_file(
        role,
        filename,
        recordings_dir=recordings_dir,
    )
    size = path.stat().st_size
    path.unlink()
    return {
        "ok": True,
        "role": role,
        "filename": filename,
        "deleted_size_bytes": size,
        "updated_utc": time.time(),
    }


def clear_recordings(
    role: str | None = None,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> dict[str, Any]:
    roles = [role] if role else list(VALID_ROLES)
    removed_files = 0
    removed_bytes = 0
    for item_role in roles:
        directory = role_recording_dir(item_role, recordings_dir=recordings_dir)
        if not directory.exists():
            continue
        for path in directory.glob("*.wav"):
            try:
                removed_bytes += path.stat().st_size
                path.unlink()
                removed_files += 1
            except OSError:
                continue
    return {
        "ok": True,
        "cleared_roles": roles,
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "updated_utc": time.time(),
    }


def enforce_retention(
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_files_per_role: int = DEFAULT_MAX_FILES_PER_ROLE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    now = time.time()
    cutoff = now - max(1, int(retention_days)) * 86400
    removed_files = 0
    removed_bytes = 0

    for role in VALID_ROLES:
        directory = role_recording_dir(role, recordings_dir=recordings_dir)
        files = sorted(
            directory.glob("*.wav") if directory.exists() else [],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        keep = []
        for index, path in enumerate(files):
            stat = path.stat()
            expired = stat.st_mtime < cutoff
            over_count = index >= max(1, int(max_files_per_role))
            if expired or over_count:
                removed_files += 1
                removed_bytes += stat.st_size
                path.unlink(missing_ok=True)
            else:
                keep.append(path)

    all_files = []
    for role in VALID_ROLES:
        directory = role_recording_dir(role, recordings_dir=recordings_dir)
        all_files.extend(directory.glob("*.wav") if directory.exists() else [])
    all_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    running_bytes = 0
    for path in all_files:
        stat = path.stat()
        if running_bytes + stat.st_size <= int(max_total_bytes):
            running_bytes += stat.st_size
            continue
        removed_files += 1
        removed_bytes += stat.st_size
        path.unlink(missing_ok=True)

    return {
        "ok": True,
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "retention_days": retention_days,
        "max_files_per_role": max_files_per_role,
        "max_total_bytes": max_total_bytes,
    }


def emit_test_recording(
    role: str,
    recordings_dir: Path = DEFAULT_RECORDINGS_DIR,
) -> dict[str, Any]:
    event_id = f"phase7-validation-{role}-{int(time.time())}"
    session = WavRecordingSession(
        role,
        event_id,
        recordings_dir=recordings_dir,
    )
    samples = array.array("h")
    for index in range(PCM_RATE_HZ):
        sample = int(
            10000
            * math.sin(
                2.0 * math.pi * 880.0 * index / PCM_RATE_HZ
            )
        )
        samples.append(sample)
    session.write(samples.tobytes())
    return {"ok": True, "role": role, **session.close()}


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="pi_scanner_recording_") as tmp:
        directory = Path(tmp) / "recordings"
        created = emit_test_recording(
            "analog_2m",
            recordings_dir=directory,
        )
        path = resolve_recording_file(
            "analog_2m",
            created["recording_filename"],
            recordings_dir=directory,
        )
        with wave.open(str(path), "rb") as reader:
            checks = [
                reader.getnchannels() == 1,
                reader.getsampwidth() == 2,
                reader.getframerate() == 8000,
                reader.getnframes() == 8000,
            ]
        payload = recordings_payload(recordings_dir=directory)
        checks.extend(
            [
                payload["recording_count"] == 1,
                payload["total_size_bytes"] > 16000,
            ]
        )
        deleted = delete_recording(
            "analog_2m",
            created["recording_filename"],
            recordings_dir=directory,
        )
        checks.extend([deleted["ok"], not path.exists()])
        try:
            resolve_recording_file(
                "analog_2m",
                "../unsafe.wav",
                recordings_dir=directory,
            )
        except AnalogRecordingError:
            checks.append(True)
        else:
            checks.append(False)
        if not all(checks):
            print(
                json.dumps(
                    {
                        "created": created,
                        "payload": payload,
                        "deleted": deleted,
                        "checks": checks,
                    },
                    indent=2,
                )
            )
            print("FINAL: FAIL")
            return 1
    print("PASS: analog WAV recording self-test")
    print("FINAL: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="PI-SCANNER analog recordings"
    )
    parser.add_argument("--role", choices=VALID_ROLES)
    parser.add_argument("--filename", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--emit-test-recording", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.emit_test_recording:
        if not args.role:
            raise AnalogRecordingError(
                "--emit-test-recording requires --role"
            )
        result = emit_test_recording(args.role)
    elif args.clear:
        result = clear_recordings(role=args.role)
    elif args.delete:
        if not args.role or not args.filename:
            raise AnalogRecordingError(
                "--delete requires --role and --filename"
            )
        result = delete_recording(args.role, args.filename)
    else:
        result = recordings_payload(role=args.role)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

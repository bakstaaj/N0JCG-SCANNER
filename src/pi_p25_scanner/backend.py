#!/usr/bin/env python3
"""Minimal PI P25 Scanner web/API backend."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pi_p25_scanner.config_model import ConfigError
    from pi_p25_scanner.config_store import (
        active_config_metadata,
        ensure_runtime_config,
        load_active_project_config,
        read_active_config_payload,
        validate_config_payload,
        write_runtime_config,
    )
    from pi_p25_scanner.decoder_discovery import discover_op25
    from pi_p25_scanner.op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs
else:
    from .config_model import ConfigError
    from .config_store import (
        active_config_metadata,
        ensure_runtime_config,
        load_active_project_config,
        read_active_config_payload,
        validate_config_payload,
        write_runtime_config,
    )
    from .decoder_discovery import discover_op25
    from .op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"
OP25_OUTPUT_DIR = Path(os.environ.get("P25_SCANNER_OP25_OUTPUT", str(DEFAULT_OUTPUT_DIR)))
LOG_TAIL_LIMIT = 80
MAX_JSON_BODY_BYTES = 512 * 1024


@dataclass
class ScannerStatus:
    ok: bool = True
    scanner_state: str = "stopped"
    decoder_engine: str = "op25"
    config: dict[str, Any] = field(default_factory=active_config_metadata)
    decoder_process: dict[str, Any] = field(
        default_factory=lambda: {
            "running": False,
            "pid": None,
            "command": [],
            "start_enabled": False,
        }
    )
    decoder_capability: dict[str, Any] = field(default_factory=dict)
    receiver_roles: dict[str, Any] = field(
        default_factory=lambda: {
            "p25_control": {"rtl_serial": "", "runtime_index": None},
            "p25_voice": {"rtl_serial": "", "runtime_index": None},
        }
    )
    active_control_frequency_hz: int | None = None
    active_voice_frequency_hz: int | None = None
    active_tgid: int | None = None
    active_talkgroup_label: str = ""
    p25_phase: str = "unknown"
    encrypted: bool = False
    muted: bool = False
    generated_op25_config: dict[str, Any] = field(default_factory=dict)
    last_event: str = "V0.1E backend idle; config API/UI scaffold ready"
    warnings: list[str] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    updated_utc: float = field(default_factory=time.time)


class ScannerManager:
    def __init__(self) -> None:
        self.status = ScannerStatus()
        self.process: subprocess.Popen[str] | None = None
        self.log_lines: deque[str] = deque(maxlen=LOG_TAIL_LIMIT)
        self.lock = threading.RLock()
        self.refresh_capability()
        self.refresh_config_summary()

    def _set_event(self, message: str) -> None:
        self.status.last_event = message
        self.status.updated_utc = time.time()

    def _append_warning(self, message: str) -> None:
        if message not in self.status.warnings:
            self.status.warnings.append(message)

    def _append_log(self, line: str) -> None:
        clean = line.rstrip("\n")
        if not clean:
            return
        with self.lock:
            self.log_lines.append(clean)
            self.status.log_tail = list(self.log_lines)

    def refresh_capability(self) -> dict[str, Any]:
        capability = discover_op25().to_dict()
        with self.lock:
            self.status.decoder_capability = capability
            for warning in capability.get("warnings", []):
                self._append_warning(str(warning))
        return capability

    def refresh_config_summary(self) -> None:
        with self.lock:
            self.status.config = active_config_metadata()
            try:
                config, path = load_active_project_config()
                system = config.first_enabled_system()
                self.status.config["path"] = str(path)
                self.status.receiver_roles = {
                    name: {
                        "rtl_serial": role.rtl_serial,
                        "runtime_index": None,
                        "gain_db": role.gain_db,
                        "ppm": role.ppm,
                    }
                    for name, role in system.receiver_roles.items()
                }
                self.status.active_control_frequency_hz = system.control_channels_hz[0]
                self.status.ok = True
            except ConfigError as exc:
                self.status.ok = False
                self.status.scanner_state = "config_error"
                self._append_warning(str(exc))
                self._set_event(f"Config error: {exc}")

    def generate_config(self) -> dict[str, Any]:
        _config, path = load_active_project_config()
        manifest = generate_op25_configs(path, OP25_OUTPUT_DIR).to_dict()
        with self.lock:
            self.status.generated_op25_config = manifest
            self.status.config = active_config_metadata()
            self._set_event(f"Generated OP25 runtime config at {manifest['output_dir']}")
        return manifest

    def init_local_config(self) -> dict[str, Any]:
        result = ensure_runtime_config(force=False)
        self.refresh_config_summary()
        with self.lock:
            self._set_event(f"Initialized local runtime config at {result['config_path']}")
        return {"ok": True, **result, "status": self.status_payload()}

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ConfigError("config save payload must be an object")
        result = write_runtime_config(payload)
        self.refresh_config_summary()
        with self.lock:
            self._set_event(f"Saved local runtime config at {result['config_path']}")
        return {"ok": True, **result, "status": self.status_payload()}

    def _reader_thread(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self._append_log(line)
        rc = process.poll()
        with self.lock:
            self.status.decoder_process["running"] = False
            self.status.decoder_process["pid"] = None
            if self.status.scanner_state == "running":
                self.status.scanner_state = "decoder_exited"
            self._set_event(f"Decoder process exited rc={rc}")

    def _build_command_from_template(self, manifest: dict[str, Any]) -> list[str]:
        template = os.environ.get("P25_SCANNER_OP25_COMMAND_TEMPLATE", "").strip()
        if not template:
            return []
        first_system = manifest.get("systems", [{}])[0]
        values = {
            "trunk_tsv": manifest.get("trunk_tsv", ""),
            "output_dir": manifest.get("output_dir", ""),
            "control_frequency_hz": first_system.get("control_channels_hz", [""])[0],
            "control_frequency_mhz": first_system.get("control_channels_mhz", [""])[0],
        }
        rendered = template.format(**values)
        return shlex.split(rendered)

    def start(self) -> tuple[dict[str, Any], HTTPStatus]:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.status.scanner_state = "running"
                self.status.decoder_process["running"] = True
                self.status.decoder_process["pid"] = self.process.pid
                self._set_event("Scanner already running")
                return asdict(self.status), HTTPStatus.ACCEPTED

        self.refresh_config_summary()
        manifest = self.generate_config()
        capability = self.refresh_capability()
        command = self._build_command_from_template(manifest)

        with self.lock:
            self.status.decoder_process["start_enabled"] = bool(command)
            self.status.decoder_process["command"] = command

        if not capability.get("installed"):
            with self.lock:
                self.status.scanner_state = "decoder_missing"
                self._set_event("Start requested; OP25 not found, generated config only")
            return asdict(self.status), HTTPStatus.ACCEPTED

        if not command:
            with self.lock:
                self.status.scanner_state = "decoder_config_generated"
                self._set_event("Start requested; OP25 found but live launch is disabled until command template validation")
            return asdict(self.status), HTTPStatus.ACCEPTED

        try:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            with self.lock:
                self.status.ok = False
                self.status.scanner_state = "decoder_start_failed"
                self._append_warning(str(exc))
                self._set_event(f"Decoder start failed: {exc}")
            return asdict(self.status), HTTPStatus.INTERNAL_SERVER_ERROR

        with self.lock:
            self.process = process
            self.status.ok = True
            self.status.scanner_state = "running"
            self.status.decoder_process["running"] = True
            self.status.decoder_process["pid"] = process.pid
            self._set_event("Decoder process started")
        threading.Thread(target=self._reader_thread, args=(process,), daemon=True).start()
        return asdict(self.status), HTTPStatus.ACCEPTED

    def stop(self) -> tuple[dict[str, Any], HTTPStatus]:
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            with self.lock:
                self.process = None
                self.status.decoder_process["running"] = False
                self.status.decoder_process["pid"] = None
                self.status.scanner_state = "stopped"
                self._set_event("Decoder process stopped")
        else:
            with self.lock:
                self.process = None
                self.status.decoder_process["running"] = False
                self.status.decoder_process["pid"] = None
                self.status.scanner_state = "stopped"
                self._set_event("Stop requested; no decoder process was running")
        return asdict(self.status), HTTPStatus.ACCEPTED

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            self.status.config = active_config_metadata()
            if self.process is not None and self.process.poll() is None:
                self.status.decoder_process["running"] = True
                self.status.decoder_process["pid"] = self.process.pid
            else:
                self.status.decoder_process["running"] = False
                self.status.decoder_process["pid"] = None
            self.status.log_tail = list(self.log_lines)
            self.status.updated_utc = time.time()
            return asdict(self.status)


MANAGER = ScannerManager()


def load_config_payload() -> dict[str, Any]:
    try:
        payload, path = read_active_config_payload()
        validation = validate_config_payload(payload)
        return {
            "ok": True,
            "config_path": str(path),
            "metadata": active_config_metadata(),
            "config": payload,
            "validation": validation,
        }
    except ConfigError as exc:
        return {"ok": False, "error": str(exc), "metadata": active_config_metadata(), "config": None}


class Handler(SimpleHTTPRequestHandler):
    server_version = "PiP25Scanner/0.1E"

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise ConfigError("JSON request body too large")
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"request JSON invalid: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("request JSON body must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        if self.path == "/api/status":
            self._send_json(MANAGER.status_payload())
            return
        if self.path == "/api/config":
            self._send_json(load_config_payload())
            return
        if self.path == "/api/decoder/capability":
            self._send_json(MANAGER.refresh_capability())
            return
        if self.path == "/api/op25/generated-config":
            self._send_json(MANAGER.status.generated_op25_config or {"ok": False, "error": "not generated yet"})
            return
        if self.path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server method name
        if self.path == "/api/scanner/start":
            payload, status = MANAGER.start()
            self._send_json(payload, status)
            return
        if self.path == "/api/scanner/stop":
            payload, status = MANAGER.stop()
            self._send_json(payload, status)
            return
        if self.path == "/api/decoder/generate-config":
            try:
                manifest = MANAGER.generate_config()
                self._send_json({"ok": True, **manifest}, HTTPStatus.ACCEPTED)
            except ConfigError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/config/init-local":
            try:
                self._send_json(MANAGER.init_local_config(), HTTPStatus.ACCEPTED)
            except ConfigError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/config/save":
            try:
                request = self._read_json()
                payload = request.get("config", request)
                self._send_json(MANAGER.save_config(payload), HTTPStatus.ACCEPTED)
            except ConfigError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def translate_path(self, path: str) -> str:
        rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
        return str((WEB_ROOT / rel).resolve())

    def guess_type(self, path: str) -> str:
        guessed, _ = mimetypes.guess_type(path)
        return guessed or "application/octet-stream"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PI P25 Scanner backend")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PI P25 Scanner backend listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopping PI P25 Scanner backend", flush=True)
    finally:
        MANAGER.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

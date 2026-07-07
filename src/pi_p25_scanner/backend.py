#!/usr/bin/env python3
"""Minimal PI P25 Scanner web/API backend."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import urllib.error
import urllib.request
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
    from pi_p25_scanner.backend_launch import (
        LaunchConfigError,
        build_validated_op25_command,
        validated_command_marker_metadata,
    )
    from pi_p25_scanner.decoder_discovery import discover_op25
    from pi_p25_scanner.op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs
    from pi_p25_scanner.runtime_status import RuntimeStatusParser, RuntimeStatusUpdate
    from pi_p25_scanner.runtime_activity import RuntimeActivityTracker
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
    from .backend_launch import (
        LaunchConfigError,
        build_validated_op25_command,
        validated_command_marker_metadata,
    )
    from .decoder_discovery import discover_op25
    from .op25_config import DEFAULT_OUTPUT_DIR, generate_op25_configs
    from .runtime_status import RuntimeStatusParser, RuntimeStatusUpdate
    from .runtime_activity import RuntimeActivityTracker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"
OP25_OUTPUT_DIR = Path(os.environ.get("P25_SCANNER_OP25_OUTPUT", str(DEFAULT_OUTPUT_DIR)))
LOG_TAIL_LIMIT = 80
MAX_JSON_BODY_BYTES = 512 * 1024
OP25_HTTP_PORT_RE = re.compile(r'http:(?:\[[^\]]+\]|[^:\s]+):(?P<port>\d{1,5})')
OP25_PROXY_MAX_BYTES = 2 * 1024 * 1024
OP25_AUDIO_UDP_HOST = "127.0.0.1"
OP25_AUDIO_UDP_PORT = int(os.environ.get("P25_SCANNER_AUDIO_UDP_PORT", "23456"))


def iter_status_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_status_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_status_strings(item)


def op25_http_ports_from_value(value: Any) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for text_value in iter_status_strings(value):
        for match in OP25_HTTP_PORT_RE.finditer(text_value):
            try:
                port = int(match.group('port'))
            except ValueError:
                continue
            if 0 < port < 65536 and port not in seen:
                seen.add(port)
                ports.append(port)
    return ports


def unique_ports(*groups: list[int]) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for group in groups:
        for port in group:
            if 0 < int(port) < 65536 and int(port) not in seen:
                seen.add(int(port))
                ports.append(int(port))
    return ports


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
            "cwd": "",
            "command_source": "none",
            "validated_marker": {},
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
    last_active_tgid: int | None = None
    last_active_talkgroup_label: str = ""
    last_active_voice_frequency_hz: int | None = None
    last_active_updated_utc: float | None = None
    talkgroup_catalog: dict[str, Any] = field(default_factory=dict)
    p25_phase: str = "unknown"
    encrypted: bool = False
    muted: bool = False
    generated_op25_config: dict[str, Any] = field(default_factory=dict)
    runtime_status: dict[str, Any] = field(default_factory=dict)
    activity_summary: dict[str, Any] = field(default_factory=dict)
    last_event: str = "V0.2H backend idle; runtime activity summary ready"
    warnings: list[str] = field(default_factory=list)
    log_tail: list[str] = field(default_factory=list)
    updated_utc: float = field(default_factory=time.time)


class ScannerManager:
    def __init__(self) -> None:
        self.status = ScannerStatus()
        self.process: subprocess.Popen[str] | None = None
        self.log_lines: deque[str] = deque(maxlen=LOG_TAIL_LIMIT)
        self.runtime_parser = RuntimeStatusParser()
        self.activity_tracker = RuntimeActivityTracker()
        self.talkgroup_labels: dict[int, str] = {}
        self.lock = threading.RLock()
        self.refresh_capability()
        self.refresh_config_summary()

    def _set_event(self, message: str) -> None:
        self.status.last_event = message
        self.status.updated_utc = time.time()

    def _append_warning(self, message: str) -> None:
        if message not in self.status.warnings:
            self.status.warnings.append(message)

    def _talkgroup_label_for_tgid(self, tgid: int | None) -> str:
        if tgid is None:
            return ""
        try:
            return self.talkgroup_labels.get(int(tgid), "")
        except (TypeError, ValueError):
            return ""

    def _apply_runtime_status_update(self, update: RuntimeStatusUpdate) -> None:
        if not update.has_update:
            return
        if update.control_frequency_hz is not None:
            self.status.active_control_frequency_hz = update.control_frequency_hz
        if update.voice_frequency_hz is not None:
            self.status.active_voice_frequency_hz = update.voice_frequency_hz
            self.status.last_active_voice_frequency_hz = update.voice_frequency_hz
        if update.tgid is not None:
            self.status.active_tgid = update.tgid
            label = update.talkgroup_label or self._talkgroup_label_for_tgid(update.tgid)
            if label:
                update.talkgroup_label = label
                self.status.active_talkgroup_label = label
            self.status.last_active_tgid = update.tgid
            self.status.last_active_talkgroup_label = label or self.status.active_talkgroup_label
            if update.voice_frequency_hz is not None:
                self.status.last_active_voice_frequency_hz = update.voice_frequency_hz
            self.status.last_active_updated_utc = time.time()
        elif update.talkgroup_label:
            self.status.active_talkgroup_label = update.talkgroup_label
            self.status.last_active_talkgroup_label = update.talkgroup_label
            self.status.last_active_updated_utc = time.time()
        if update.p25_phase:
            self.status.p25_phase = update.p25_phase
        if update.encrypted is not None:
            self.status.encrypted = update.encrypted
        if update.muted is not None:
            self.status.muted = update.muted
        self.status.runtime_status = update.to_status_dict()
        self.status.activity_summary = self.activity_tracker.record(update)

    def _append_log(self, line: str) -> None:
        clean = line.rstrip("\n")
        if not clean:
            return
        update = self.runtime_parser.parse_line(clean)
        with self.lock:
            self.log_lines.append(clean)
            self.status.log_tail = list(self.log_lines)
            self._apply_runtime_status_update(update)

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
                self.talkgroup_labels = {int(tg.tgid): str(tg.label or tg.tgid) for tg in system.enabled_talkgroups}
                self.status.talkgroup_catalog = {
                    "count": len(self.talkgroup_labels),
                    "labels": {str(tgid): label for tgid, label in sorted(self.talkgroup_labels.items())},
                }
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
                self.talkgroup_labels = {}
                self.status.talkgroup_catalog = {"count": 0, "labels": {}}
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

    def _with_browser_audio_udp(self, command: list[str]) -> list[str]:
        """Return OP25 command with raw browser-audio UDP output enabled.

        The raw audio bridge runs independently as a service. This method only
        appends OP25 UDP output flags; it does not manage audio bridge process
        state and it does not add any filtered/encrypted audio gates.
        """
        updated = list(command)
        if "-w" not in updated:
            updated.append("-w")
        if "-W" not in updated:
            updated.extend(["-W", OP25_AUDIO_UDP_HOST])
        if "-u" not in updated:
            updated.extend(["-u", str(OP25_AUDIO_UDP_PORT)])
        return updated

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
        command: list[str] = []
        command_cwd = str(PROJECT_ROOT)
        command_env: dict[str, str] | None = None
        command_meta: dict[str, Any] = validated_command_marker_metadata(PROJECT_ROOT)

        try:
            validated = build_validated_op25_command(PROJECT_ROOT)
        except LaunchConfigError as exc:
            with self.lock:
                self.status.ok = False
                self.status.scanner_state = "decoder_command_invalid"
                self.status.decoder_process["start_enabled"] = False
                self.status.decoder_process["command"] = []
                self.status.decoder_process["cwd"] = ""
                self.status.decoder_process["command_source"] = "validated_marker"
                self.status.decoder_process["validated_marker"] = command_meta
                self._append_warning(str(exc))
                self._set_event(f"Validated OP25 command marker invalid: {exc}")
            return asdict(self.status), HTTPStatus.BAD_REQUEST

        if validated is not None:
            command = validated.command
            command_cwd = validated.cwd
            command_env = validated.env
            command_meta = validated.to_status_dict()
        else:
            template_command = self._build_command_from_template(manifest)
            if template_command:
                command = template_command
                command_meta = {
                    "source": "environment_template",
                    "path": "",
                    "exists": True,
                    "validated": False,
                }

        if command:
            command = self._with_browser_audio_udp(command)

        with self.lock:
            self.status.decoder_process["start_enabled"] = bool(command)
            self.status.decoder_process["command"] = command
            self.status.decoder_process["cwd"] = command_cwd if command else ""
            self.status.decoder_process["command_source"] = command_meta.get("source", "none")
            self.status.decoder_process["validated_marker"] = command_meta

        if not capability.get("installed"):
            with self.lock:
                self.status.scanner_state = "decoder_missing"
                self._set_event("Start requested; OP25 not found, generated config only")
            return asdict(self.status), HTTPStatus.ACCEPTED

        if not command:
            with self.lock:
                self.status.scanner_state = "decoder_config_generated"
                self._set_event(
                    "Start requested; live launch disabled until runtime/settings/op25_validated_rx_command.env exists"
                )
            return asdict(self.status), HTTPStatus.ACCEPTED

        with self.lock:
            self.status.activity_summary = self.activity_tracker.reset()
            self._set_event("Runtime activity counters reset for scanner start")

        try:
            process = subprocess.Popen(
                command,
                cwd=command_cwd,
                env=command_env,
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
            self.status.decoder_process["command"] = command
            self.status.decoder_process["cwd"] = command_cwd
            self.status.decoder_process["command_source"] = command_meta.get("source", "none")
            self.status.decoder_process["validated_marker"] = command_meta
            self._set_event("Decoder process started from validated OP25 command marker")
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

    def op25_http_interface(self) -> dict[str, Any]:
        with self.lock:
            self.status.decoder_process["validated_marker"] = validated_command_marker_metadata(PROJECT_ROOT)
            snapshot = asdict(self.status)
        process = snapshot.get("decoder_process") or {}
        marker = process.get("validated_marker") or {}
        command_ports = op25_http_ports_from_value(process.get("command") or [])
        marker_ports = op25_http_ports_from_value(marker)
        ports = unique_ports(command_ports, marker_ports)
        port = ports[0] if ports else None
        tcp_reachable = False
        tcp_error = ""
        if port is not None:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
                    tcp_reachable = True
            except OSError as exc:
                tcp_error = str(exc)
        return {
            "ok": True,
            "enabled": port is not None,
            "running": bool(process.get("running")),
            "port": port,
            "ports": ports,
            "local_url": f"http://127.0.0.1:{port}/" if port is not None else "",
            "proxy_url": "/op25/" if port is not None else "",
            "tcp_reachable": tcp_reachable,
            "tcp_error": tcp_error,
            "sources": {"command": command_ports, "validated_marker": marker_ports},
        }

    def op25_proxy_fetch(self, rel_path: str) -> tuple[int, str, bytes]:
        interface = self.op25_http_interface()
        port = interface.get("port")
        if port is None:
            return 503, "text/plain; charset=utf-8", b"OP25 HTTP interface port is not known\n"
        clean_path = rel_path.lstrip("/")
        target_url = f"http://127.0.0.1:{int(port)}/{clean_path}"
        try:
            with urllib.request.urlopen(target_url, timeout=2.0) as response:
                body = response.read(OP25_PROXY_MAX_BYTES)
                content_type = response.headers.get("content-type", "application/octet-stream")
                return int(response.status), content_type, body
        except urllib.error.HTTPError as exc:
            body = exc.read(OP25_PROXY_MAX_BYTES)
            content_type = exc.headers.get("content-type", "text/plain; charset=utf-8")
            return int(exc.code), content_type, body
        except Exception as exc:
            message = f"OP25 proxy error: {type(exc).__name__}: {exc}\n".encode("utf-8")
            return 502, "text/plain; charset=utf-8", message

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            self.status.config = active_config_metadata()
            if self.process is not None and self.process.poll() is None:
                self.status.decoder_process["running"] = True
                self.status.decoder_process["pid"] = self.process.pid
            else:
                self.status.decoder_process["running"] = False
                self.status.decoder_process["pid"] = None
            self.status.decoder_process["validated_marker"] = validated_command_marker_metadata(PROJECT_ROOT)
            self.status.log_tail = list(self.log_lines)
            self.status.activity_summary = self.activity_tracker.snapshot()
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
    server_version = "PiP25Scanner/0.3A"

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, status: int = 200, content_type: str = "application/octet-stream") -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type or "application/octet-stream")
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

    def _proxy_op25(self) -> None:
        if self.path == "/op25":
            rel_path = ""
        else:
            rel_path = self.path[len("/op25/"):]
        status, content_type, data = MANAGER.op25_proxy_fetch(rel_path)
        self._send_bytes(data, status, content_type)

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        if self.path == "/api/op25/http-interface":
            self._send_json(MANAGER.op25_http_interface())
            return
        if self.path == "/op25" or self.path.startswith("/op25/"):
            self._proxy_op25()
            return
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
    parser.add_argument("--port", type=int, default=8070)
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

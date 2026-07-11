#!/usr/bin/env python3
"""Minimal PI P25 Scanner web/API backend with stable named-config support."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
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
        delete_named_config as store_delete_named_config,
        ensure_runtime_config,
        list_named_configs,
        load_active_project_config,
        load_named_config as store_load_named_config,
        read_active_config_payload,
        save_named_config as store_save_named_config,
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
    try:
        from pi_p25_scanner.radioreference_import import (
            RadioReferenceError,
            import_trunked_system,
            radioreference_status,
            save_credentials as save_radioreference_credentials,
            test_login as test_radioreference_login,
        )
    except Exception:
        RadioReferenceError = ConfigError  # type: ignore[assignment]
        import_trunked_system = None  # type: ignore[assignment]
        radioreference_status = None  # type: ignore[assignment]
        save_radioreference_credentials = None  # type: ignore[assignment]
        test_radioreference_login = None  # type: ignore[assignment]
else:
    from .config_model import ConfigError
    from .config_store import (
        active_config_metadata,
        delete_named_config as store_delete_named_config,
        ensure_runtime_config,
        list_named_configs,
        load_active_project_config,
        load_named_config as store_load_named_config,
        read_active_config_payload,
        save_named_config as store_save_named_config,
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
    try:
        from .radioreference_import import (
            RadioReferenceError,
            import_trunked_system,
            radioreference_status,
            save_credentials as save_radioreference_credentials,
            test_login as test_radioreference_login,
        )
    except Exception:
        RadioReferenceError = ConfigError  # type: ignore[assignment]
        import_trunked_system = None  # type: ignore[assignment]
        radioreference_status = None  # type: ignore[assignment]
        save_radioreference_credentials = None  # type: ignore[assignment]
        test_radioreference_login = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"
OP25_OUTPUT_DIR = Path(os.environ.get("P25_SCANNER_OP25_OUTPUT", str(DEFAULT_OUTPUT_DIR)))
LOG_TAIL_LIMIT = 80
MAX_JSON_BODY_BYTES = 512 * 1024
OP25_HTTP_PORT_RE = re.compile(r"http:(?:\[[^\]]+\]|[^:\s]+):(?P<port>\d{1,5})")
OP25_AUDIO_UDP_HOST = "127.0.0.1"
OP25_AUDIO_UDP_PORT = int(os.environ.get("P25_SCANNER_AUDIO_UDP_PORT", "23456"))
AUDIO_BRIDGE_PORT = int(os.environ.get("P25_SCANNER_AUDIO_BRIDGE_PORT", "8072"))


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
                port = int(match.group("port"))
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
            port_int = int(port)
            if 0 < port_int < 65536 and port_int not in seen:
                seen.add(port_int)
                ports.append(port_int)
    return ports


def _safe_error_payload(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": exc.__class__.__name__}


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
    last_event: str = "backend idle"
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

    # LAUNCH_READINESS_REFRESH_V0_4G14
    def _refresh_launch_readiness_locked(self) -> None:
        """Refresh scanner Start-button readiness from the validated OP25 marker.

        The dashboard polls /api/status and disables Start unless
        decoder_process.start_enabled is true. After the V0.4G13 hard reset,
        readiness was only computed during Start, so the UI stayed at
        "Not Launch Ready" even when runtime/settings/op25_validated_rx_command.env
        was present and valid.
        """

        command: list[str] = []
        command_cwd = ""
        command_meta: dict[str, Any] = validated_command_marker_metadata(PROJECT_ROOT)
        try:
            validated = build_validated_op25_command(PROJECT_ROOT)
        except LaunchConfigError as exc:
            self.status.decoder_process["start_enabled"] = False
            self.status.decoder_process["command"] = []
            self.status.decoder_process["cwd"] = ""
            self.status.decoder_process["command_source"] = "validated_marker"
            self.status.decoder_process["validated_marker"] = command_meta
            self.status.decoder_process["launch_ready_error"] = str(exc)
            self._append_warning(str(exc))
            return

        if validated is not None:
            command = list(validated.command)
            command_cwd = str(validated.cwd)
            command_meta = validated.to_status_dict()
        else:
            self.status.decoder_process["launch_ready_error"] = "validated OP25 command marker not found"

        if command:
            command = self._with_browser_audio_udp(command)

        op25_installed = bool(self.status.decoder_capability.get("installed", True))
        ready = bool(command) and op25_installed
        self.status.decoder_process["start_enabled"] = ready
        self.status.decoder_process["command"] = command if command else []
        self.status.decoder_process["cwd"] = command_cwd if command else ""
        self.status.decoder_process["command_source"] = command_meta.get("source", "none")
        self.status.decoder_process["validated_marker"] = command_meta
        if ready:
            self.status.decoder_process.pop("launch_ready_error", None)
        elif not op25_installed:
            self.status.decoder_process["launch_ready_error"] = "OP25 not discovered"

    def status_payload(self) -> dict[str, Any]:
        with self.lock:
            self._refresh_process_state_locked()
            if self.process is None or self.process.poll() is not None:
                self._refresh_launch_readiness_locked()
            self.status.config = active_config_metadata()
            return asdict(self.status)

    def _set_event(self, message: str) -> None:
        self.status.last_event = message
        self.status.updated_utc = time.time()

    def _append_warning(self, message: str) -> None:
        if message and message not in self.status.warnings:
            self.status.warnings.append(message)

    def _refresh_process_state_locked(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.status.decoder_process["running"] = True
            self.status.decoder_process["pid"] = self.process.pid
            if self.status.scanner_state not in ("running", "starting"):
                self.status.scanner_state = "running"
        elif self.process is not None:
            self.status.decoder_process["running"] = False
            self.status.decoder_process["pid"] = None
            if self.status.scanner_state == "running":
                self.status.scanner_state = "decoder_exited"
        else:
            self.status.decoder_process["running"] = False
            self.status.decoder_process["pid"] = None

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
        try:
            capability = discover_op25().to_dict()
        except Exception as exc:
            capability = {"installed": False, "warnings": [f"OP25 discovery failed: {exc}"]}
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
                self.status.active_control_frequency_hz = system.control_channels_hz[0] if system.control_channels_hz else None
                self.status.ok = True
                if self.status.scanner_state == "config_error":
                    self.status.scanner_state = "stopped"
            except ConfigError as exc:
                self.talkgroup_labels = {}
                self.status.talkgroup_catalog = {"count": 0, "labels": {}}
                self.status.ok = False
                self.status.scanner_state = "config_error"
                self._append_warning(str(exc))
                self._set_event(f"Config error: {exc}")

    def config_payload(self) -> dict[str, Any]:
        payload, path = read_active_config_payload()
        validation = validate_config_payload(payload)
        return {"ok": True, "path": str(path), "config": payload, "validation": validation}

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

    def named_configs_payload(self) -> dict[str, Any]:
        payload = list_named_configs(include_invalid=True)
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            payload.setdefault("active_config", active_config_metadata())
            return payload
        return {"ok": True, "configs": payload, "active_config": active_config_metadata()}

    def save_named_config(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ConfigError("named config save payload must be an object")
        name = str(request.get("name") or request.get("id") or request.get("config_id") or "").strip()
        payload = request.get("config")
        if payload is None:
            payload, _path = read_active_config_payload()
        result = store_save_named_config(name, payload)
        self.refresh_config_summary()
        with self.lock:
            self._set_event(f"Saved named config: {result.get('name', name)}")
        return {"ok": True, **result, "named_configs": self.named_configs_payload(), "status": self.status_payload()}

    def load_named_config(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ConfigError("named config load payload must be an object")
        config_id = str(request.get("name") or request.get("id") or request.get("config_id") or request.get("slug") or "").strip()
        result = store_load_named_config(config_id, apply_to_runtime=bool(request.get("apply", True)))
        self.refresh_config_summary()
        manifest = None
        if bool(request.get("apply", True)):
            manifest = self.generate_config()
        with self.lock:
            self._set_event(f"Loaded named config: {result.get('name', config_id)}")
        return {
            "ok": True,
            **result,
            "generated_op25_config": manifest,
            "named_configs": self.named_configs_payload(),
            "status": self.status_payload(),
        }

    def delete_named_config(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ConfigError("named config delete payload must be an object")
        config_id = str(request.get("name") or request.get("id") or request.get("config_id") or request.get("slug") or "").strip()
        result = store_delete_named_config(config_id)
        self.refresh_config_summary()
        with self.lock:
            self._set_event(f"Deleted named config: {result.get('id', config_id)}")
        return {"ok": True, **result, "named_configs": self.named_configs_payload(), "status": self.status_payload()}

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
        return shlex.split(template.format(**values))

    def _with_browser_audio_udp(self, command: list[str]) -> list[str]:
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
                return self.status_payload(), HTTPStatus.ACCEPTED

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
            return self.status_payload(), HTTPStatus.BAD_REQUEST

        if validated is not None:
            command = validated.command
            command_cwd = validated.cwd
            command_env = validated.env
            command_meta = validated.to_status_dict()
        else:
            template_command = self._build_command_from_template(manifest)
            if template_command:
                command = template_command
                command_meta = {"source": "environment_template", "path": "", "exists": True, "validated": False}

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
            return self.status_payload(), HTTPStatus.ACCEPTED

        if not command:
            with self.lock:
                self.status.scanner_state = "decoder_config_generated"
                self._set_event("Start requested; live launch disabled until runtime/settings/op25_validated_rx_command.env exists")
            return self.status_payload(), HTTPStatus.ACCEPTED

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
                self.status.scanner_state = "decoder_launch_failed"
                self._append_warning(str(exc))
                self._set_event(f"Decoder launch failed: {exc}")
            return self.status_payload(), HTTPStatus.INTERNAL_SERVER_ERROR

        with self.lock:
            self.process = process
            self.status.ok = True
            self.status.scanner_state = "running"
            self.status.decoder_process["running"] = True
            self.status.decoder_process["pid"] = process.pid
            self._set_event(f"Scanner started pid={process.pid}")
        threading.Thread(target=self._reader_thread, args=(process,), daemon=True).start()
        return self.status_payload(), HTTPStatus.ACCEPTED

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
            self.status.scanner_state = "stopped"
            self.status.decoder_process["running"] = False
            self.status.decoder_process["pid"] = None
            self._set_event("Scanner stopped")
        return self.status_payload(), HTTPStatus.ACCEPTED

    def audio_status(self, request_host: str = "") -> dict[str, Any]:
        host = (request_host or "").split(":", 1)[0].strip() or socket.gethostname()
        if host in ("127.0.0.1", "localhost", "0.0.0.0"):
            host = socket.gethostname()
        return {
            "ok": True,
            "mode": "raw_browser_audio_bridge",
            "bridge_port": AUDIO_BRIDGE_PORT,
            "udp_host": OP25_AUDIO_UDP_HOST,
            "udp_port": OP25_AUDIO_UDP_PORT,
            "stream_url": f"http://{host}:{AUDIO_BRIDGE_PORT}/audio.wav",
            "test_tone_url": f"http://{host}:{AUDIO_BRIDGE_PORT}/test-tone.wav",
            "managed_by_backend": False,
        }


MANAGER = ScannerManager()


class Handler(SimpleHTTPRequestHandler):
    server_version = "PI-P25-Scanner/0.4G13"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_JSON_BODY_BYTES:
            raise ConfigError("request body too large")
        if length <= 0:
            return {}
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON request body: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("request JSON must be an object")
        return payload

    def _handle_exception(self, exc: Exception, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json(_safe_error_payload(exc), status)

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                self._send_json(MANAGER.status_payload())
                return
            if path == "/api/config":
                self._send_json(MANAGER.config_payload())
                return
            if path == "/api/config/named":
                self._send_json(MANAGER.named_configs_payload())
                return
            if path == "/api/audio/status":
                self._send_json(MANAGER.audio_status(self.headers.get("Host", "")))
                return
            if path == "/api/radioreference/status":
                if radioreference_status is None:
                    self._send_json({"ok": False, "available": False, "error": "RadioReference importer unavailable"})
                else:
                    self._send_json(radioreference_status())
                return
            if path == "/api/decoder/capability":
                self._send_json(MANAGER.refresh_capability())
                return
            if path == "/api/op25/generated-config":
                self._send_json(MANAGER.status.generated_op25_config or {"ok": False, "error": "not generated yet"})
                return
            if path in ("/", "/index.html"):
                self.path = "/index.html"
            return super().do_GET()
        except Exception as exc:
            self._handle_exception(exc, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802 - http.server method name
        try:
            path = self.path.split("?", 1)[0]
            if path == "/api/scanner/start":
                payload, status = MANAGER.start()
                self._send_json(payload, status)
                return
            if path == "/api/scanner/stop":
                payload, status = MANAGER.stop()
                self._send_json(payload, status)
                return
            if path in ("/api/audio/start", "/api/audio/stop"):
                self._send_json(MANAGER.audio_status(self.headers.get("Host", "")), HTTPStatus.ACCEPTED)
                return
            if path == "/api/decoder/generate-config":
                manifest = MANAGER.generate_config()
                self._send_json({"ok": True, **manifest}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/config/init-local":
                self._send_json(MANAGER.init_local_config(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/config/save":
                request = self._read_json()
                payload = request.get("config", request)
                self._send_json(MANAGER.save_config(payload), HTTPStatus.ACCEPTED)
                return
            if path in ("/api/config/named/save", "/api/config/save-named"):
                self._send_json(MANAGER.save_named_config(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if path in ("/api/config/named/load", "/api/config/load-named"):
                self._send_json(MANAGER.load_named_config(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if path in ("/api/config/named/delete", "/api/config/delete-named"):
                self._send_json(MANAGER.delete_named_config(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/radioreference/save-credentials":
                if save_radioreference_credentials is None:
                    raise ConfigError("RadioReference importer unavailable")
                self._send_json(save_radioreference_credentials(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if path == "/api/radioreference/test-login":
                if test_radioreference_login is None:
                    raise ConfigError("RadioReference importer unavailable")
                self._send_json(test_radioreference_login(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/radioreference/import":
                if import_trunked_system is None:
                    raise ConfigError("RadioReference importer unavailable")
                request = self._read_json()
                result = import_trunked_system(request)
                if result.get("ok") and isinstance(result.get("config"), dict):
                    saved = MANAGER.save_config(result["config"])
                    manifest = MANAGER.generate_config()
                    result["saved"] = saved
                    result["generated_op25_config"] = manifest
                    result["status"] = MANAGER.status_payload()
                self._send_json(result, HTTPStatus.ACCEPTED)
                return
            self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except ConfigError as exc:
            self._handle_exception(exc, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._handle_exception(exc, HTTPStatus.INTERNAL_SERVER_ERROR)

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

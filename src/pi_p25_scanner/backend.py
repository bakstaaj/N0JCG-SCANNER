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
    from pi_p25_scanner.p25_csv_import import P25CsvError, import_p25_csv_request
    from pi_p25_scanner.receiver_inventory import build_receiver_inventory  # PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
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
    from .p25_csv_import import P25CsvError, import_p25_csv_request
    from .receiver_inventory import build_receiver_inventory  # PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
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
    control_channel_state: str = "idle"
    control_channel_locked: bool = False
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
        self.blocked_talkgroup_ids: set[int] = set()
        self._display_suppressed_tgid_until: dict[int, float] = {}
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
        # ACTIVE_AUDIO_ONLY_DISPLAY_V0_4H4
        if not update.has_update:
            return

        now = time.time()
        if not hasattr(self, "_display_suppressed_tgid_until"):
            self._display_suppressed_tgid_until = {}
        if not hasattr(self, "blocked_talkgroup_ids"):
            self.blocked_talkgroup_ids = set()

        # Expire temporary encrypted/blocked display suppressions.
        for suppressed_tgid, until_ts in list(self._display_suppressed_tgid_until.items()):
            if until_ts <= now:
                self._display_suppressed_tgid_until.pop(suppressed_tgid, None)

        parsed_tgid: int | None = None
        if update.tgid is not None:
            try:
                parsed_tgid = int(update.tgid)
            except (TypeError, ValueError):
                parsed_tgid = None

        encrypted_or_muted = update.encrypted is True or update.muted is True
        configured_label = self._talkgroup_label_for_tgid(parsed_tgid)
        label_lower = configured_label.lower()
        label_blocked = bool(
            parsed_tgid is not None
            and (
                parsed_tgid in self.blocked_talkgroup_ids
                or any(
                    token in label_lower
                    for token in ("encrypted", "blocked", "block", "skip", "skipped", "muted", "mute", "secure", "cipher")
                )
            )
        )
        temporarily_suppressed = bool(
            parsed_tgid is not None and self._display_suppressed_tgid_until.get(parsed_tgid, 0) > now
        )

        if parsed_tgid is not None and update.encrypted is False and update.muted is not True and not label_blocked:
            self._display_suppressed_tgid_until.pop(parsed_tgid, None)

        if update.control_channel_state:
            self.status.control_channel_state = update.control_channel_state
            self.status.control_channel_locked = update.control_channel_state == "locked"

        # Do not promote encrypted/blocked/muted calls into the active-audio panel.
        if encrypted_or_muted or label_blocked or temporarily_suppressed:
            if parsed_tgid is not None:
                self._display_suppressed_tgid_until[parsed_tgid] = now + 12.0
            if (parsed_tgid is None) or (self.status.active_tgid == parsed_tgid):
                self.status.active_tgid = None
                self.status.active_talkgroup_label = ""
                self.status.active_voice_frequency_hz = None
            if update.control_frequency_hz is not None:
                self.status.active_control_frequency_hz = update.control_frequency_hz
            if update.p25_phase:
                self.status.p25_phase = update.p25_phase
            if update.encrypted is not None:
                self.status.encrypted = update.encrypted
            if update.muted is not None:
                self.status.muted = update.muted
            update.parser_notes.append("suppressed_from_active_audio_display")
            self.status.runtime_status = update.to_status_dict()
            self.status.activity_summary = self.activity_tracker.record(update)
            self._set_event("Encrypted/blocked talkgroup suppressed from active audio display")
            return

        if update.control_frequency_hz is not None:
            self.status.active_control_frequency_hz = update.control_frequency_hz
        if update.voice_frequency_hz is not None:
            self.status.active_voice_frequency_hz = update.voice_frequency_hz
            self.status.last_active_voice_frequency_hz = update.voice_frequency_hz
        if parsed_tgid is not None:
            self.status.active_tgid = parsed_tgid
            label = update.talkgroup_label or configured_label
            update.talkgroup_label = label
            self.status.active_talkgroup_label = label
            self.status.last_active_tgid = parsed_tgid
            self.status.last_active_talkgroup_label = label
            if update.voice_frequency_hz is not None:
                self.status.last_active_voice_frequency_hz = update.voice_frequency_hz
            self.status.last_active_updated_utc = now
            self.status.encrypted = False if update.encrypted is None else update.encrypted
            self.status.muted = False if update.muted is None else update.muted
        elif update.talkgroup_label:
            self.status.active_talkgroup_label = update.talkgroup_label
            self.status.last_active_talkgroup_label = update.talkgroup_label
            self.status.last_active_updated_utc = now
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
                # ACTIVE_AUDIO_ONLY_BLOCKED_CATALOG_V0_4H4
                blocked_terms = (
                    "encrypted",
                    "blocked",
                    "block",
                    "skip",
                    "skipped",
                    "muted",
                    "mute",
                    "secure",
                    "cipher",
                    "enc ",
                )
                self.blocked_talkgroup_ids = {
                    int(tg.tgid)
                    for tg in system.talkgroups
                    if (not tg.enabled) or any(term in str(tg.label or "").lower() for term in blocked_terms)
                }
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
                self.blocked_talkgroup_ids = set()
                self.status.talkgroup_catalog = {"count": 0, "labels": {}}
                self.status.ok = False
                self.status.scanner_state = "config_error"
                self._append_warning(str(exc))
                self._set_event(f"Config error: {exc}")

    # PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
    def receiver_inventory_payload(self) -> dict[str, Any]:
        return build_receiver_inventory()

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
        # boatbod OP25 only logs control-channel hunt changes at verbosity 5+
        # (trunking.py hunt_cc). Normalize the existing validated command so
        # the backend can report the tuner frequency currently being tested.
        if "-v" in updated:
            verbosity_index = updated.index("-v") + 1
            if verbosity_index < len(updated):
                try:
                    current_verbosity = int(updated[verbosity_index])
                except (TypeError, ValueError):
                    current_verbosity = 0
                updated[verbosity_index] = str(max(5, current_verbosity))
        elif "--verbosity" not in updated:
            updated.extend(["-v", "5"])
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
            self.status.control_channel_state = "searching"
            self.status.control_channel_locked = False
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
            self.status.control_channel_state = "idle"
            self.status.control_channel_locked = False
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



# BEGIN V0.4H talkgroup activity display bind
# This compatibility layer is intentionally outside ScannerManager so it survives
# prior recovery edits that changed the class body shape. It is read-only with
# respect to OP25 audio: it parses metadata already present in backend/OP25 logs.
try:
    from .talkgroup_activity import parse_activity_line as _v04h_parse_activity_line
    from .talkgroup_activity import scan_activity_lines as _v04h_scan_activity_lines
except Exception:  # pragma: no cover - direct-script fallback
    from pi_p25_scanner.talkgroup_activity import parse_activity_line as _v04h_parse_activity_line
    from pi_p25_scanner.talkgroup_activity import scan_activity_lines as _v04h_scan_activity_lines


def _v04h_asdict_status(status_obj: Any) -> dict[str, Any]:
    try:
        return asdict(status_obj)
    except Exception:
        if isinstance(status_obj, dict):
            return dict(status_obj)
        return dict(getattr(status_obj, "__dict__", {}))


def _v04h_talkgroup_labels(manager: Any) -> dict[int, str]:
    labels: dict[int, str] = {}
    existing = getattr(manager, "talkgroup_labels", None)
    if isinstance(existing, dict):
        for key, value in existing.items():
            try:
                labels[int(key)] = str(value)
            except Exception:
                pass
    status = getattr(manager, "status", None)
    catalog = getattr(status, "talkgroup_catalog", {}) if status is not None else {}
    if isinstance(catalog, dict):
        raw_labels = catalog.get("labels", {})
        if isinstance(raw_labels, dict):
            for key, value in raw_labels.items():
                try:
                    labels[int(key)] = str(value)
                except Exception:
                    pass
    if labels:
        return labels
    try:
        config, _path = load_active_project_config()
        system = config.first_enabled_system()
        talkgroups = getattr(system, "enabled_talkgroups", None) or getattr(system, "talkgroups", [])
        for tg in talkgroups:
            enabled = getattr(tg, "enabled", True)
            if enabled:
                labels[int(getattr(tg, "tgid"))] = str(getattr(tg, "label", "") or getattr(tg, "tgid"))
    except Exception:
        pass
    return labels


def _v04h_apply_activity(manager: Any, activity: dict[str, Any] | None) -> bool:
    if not activity:
        return False
    status = getattr(manager, "status", None)
    if status is None:
        return False
    labels = _v04h_talkgroup_labels(manager)
    tgid = activity.get("tgid")
    label = str(activity.get("talkgroup_label") or "")
    if tgid is not None:
        try:
            tgid = int(tgid)
        except Exception:
            tgid = None
    if tgid is not None and not label:
        label = labels.get(tgid, "")
    now = time.time()
    try:
        with manager.lock:
            if tgid is not None:
                setattr(status, "active_tgid", tgid)
                setattr(status, "last_active_tgid", tgid)
                setattr(status, "active_talkgroup_label", label)
                setattr(status, "last_active_talkgroup_label", label)
            elif label:
                setattr(status, "active_talkgroup_label", label)
                setattr(status, "last_active_talkgroup_label", label)
            voice_frequency_hz = activity.get("voice_frequency_hz")
            if voice_frequency_hz:
                setattr(status, "active_voice_frequency_hz", int(voice_frequency_hz))
                setattr(status, "last_active_voice_frequency_hz", int(voice_frequency_hz))
            if activity.get("p25_phase"):
                setattr(status, "p25_phase", str(activity.get("p25_phase")))
            if activity.get("encrypted") is not None:
                setattr(status, "encrypted", bool(activity.get("encrypted")))
            if activity.get("muted") is not None:
                setattr(status, "muted", bool(activity.get("muted")))
            setattr(status, "last_active_updated_utc", now)
            runtime_status = getattr(status, "runtime_status", None)
            if not isinstance(runtime_status, dict):
                runtime_status = {}
            runtime_status["talkgroup_activity_parser"] = {
                "source": activity.get("source"),
                "tgid": tgid,
                "label": label,
                "voice_frequency_hz": activity.get("voice_frequency_hz"),
                "parsed_utc": activity.get("parsed_utc"),
                "last_line": activity.get("line", "")[-220:],
            }
            setattr(status, "runtime_status", runtime_status)
            if tgid is not None:
                setattr(status, "last_event", f"Active talkgroup {tgid} {label}".strip())
            setattr(status, "updated_utc", now)
        return True
    except Exception:
        return False


def _v04h_scan_existing_logs(manager: Any) -> None:
    status = getattr(manager, "status", None)
    lines: list[str] = []
    try:
        lines.extend([str(x) for x in list(getattr(manager, "log_lines", []))])
    except Exception:
        pass
    try:
        tail = getattr(status, "log_tail", []) if status is not None else []
        lines.extend([str(x) for x in list(tail)])
    except Exception:
        pass
    if not lines:
        return
    activity = _v04h_scan_activity_lines(lines, _v04h_talkgroup_labels(manager))
    _v04h_apply_activity(manager, activity)


_v04h_original_append_log = getattr(ScannerManager, "_append_log", None)
if callable(_v04h_original_append_log):
    def _v04h_append_log(self: Any, line: str) -> None:
        _v04h_original_append_log(self, line)
        activity = _v04h_parse_activity_line(line, _v04h_talkgroup_labels(self))
        _v04h_apply_activity(self, activity)
    ScannerManager._append_log = _v04h_append_log


_v04h_original_status_payload = getattr(ScannerManager, "status_payload", None)
def _v04h_status_payload(self: Any) -> dict[str, Any]:
    _v04h_scan_existing_logs(self)
    if callable(_v04h_original_status_payload):
        payload = _v04h_original_status_payload(self)
        if isinstance(payload, dict):
            return payload
    return _v04h_asdict_status(getattr(self, "status", {}))
ScannerManager.status_payload = _v04h_status_payload


_v04h_original_with_audio = getattr(ScannerManager, "_with_browser_audio_udp", None)
if callable(_v04h_original_with_audio):
    def _v04h_with_browser_audio_udp(self: Any, command: list[str]) -> list[str]:
        updated = list(_v04h_original_with_audio(self, command))
        if "-v" not in updated and "--verbosity" not in updated:
            updated.extend(["-v", "1"])
        return updated
    ScannerManager._with_browser_audio_udp = _v04h_with_browser_audio_udp
# END V0.4H talkgroup activity display bind



# BEGIN V0.4H5_BLOCKED_TGID_AUDIO_GATE
_V04H5_BLOCKED_LABEL_TOKENS = (
    "encrypted",
    "encrypt",
    "ciphertxt",
    "cipher",
    "algid",
    "blocked",
    "block",
    "skip",
    "skipped",
    "mute",
    "muted",
    "no audio",
    "noaudio",
)
_V04H5_AUDIO_GATE_URL = "http://127.0.0.1:8072/api/audio/gate"
_V04H5_AUDIO_GATE_HOLD_MS = 5200
_V04H5_AUDIO_GATE_RATE_LIMIT_SECONDS = 1.1


def _v04h5_safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _v04h5_label_is_blocked(label: Any) -> bool:
    text = str(label or "").lower()
    return any(token in text for token in _V04H5_BLOCKED_LABEL_TOKENS)


def _v04h5_rebuild_blocked_tgids(manager: Any) -> set[int]:
    blocked: dict[int, str] = {}
    try:
        config, _path = load_active_project_config()
        system = config.first_enabled_system()
        for tg in getattr(system, "talkgroups", []):
            tgid = _v04h5_safe_int(getattr(tg, "tgid", None))
            if tgid is None:
                continue
            label = str(getattr(tg, "label", "") or tgid)
            enabled = bool(getattr(tg, "enabled", True))
            if (not enabled) or _v04h5_label_is_blocked(label):
                blocked[tgid] = label
    except Exception as exc:  # keep status alive; config errors are reported elsewhere
        try:
            manager._append_warning(f"Blocked TGID map unavailable: {exc}")
        except Exception:
            pass
    manager.blocked_tgids = set(blocked.keys())
    manager.blocked_tgid_labels = blocked
    return set(blocked.keys())


def _v04h5_gate_audio_for_tgid(manager: Any, tgid: int, reason: str) -> None:
    now = time.time()
    cache = getattr(manager, "_v04h5_audio_gate_cache", {})
    key = f"{tgid}:{reason}"
    if now - float(cache.get(key, 0.0)) < _V04H5_AUDIO_GATE_RATE_LIMIT_SECONDS:
        return
    cache[key] = now
    manager._v04h5_audio_gate_cache = cache
    try:
        import urllib.parse as _v04h5_urlparse

        query = _v04h5_urlparse.urlencode({"hold_ms": str(_V04H5_AUDIO_GATE_HOLD_MS), "reason": f"{reason}-tgid-{tgid}"})
        url = f"{_V04H5_AUDIO_GATE_URL}?{query}"

        def _worker() -> None:
            try:
                urllib.request.urlopen(url, timeout=0.35).read(2048)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()
    except Exception:
        pass


def _v04h5_filter_payload(manager: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        blocked = getattr(manager, "blocked_tgids", None)
        if blocked is None:
            blocked = _v04h5_rebuild_blocked_tgids(manager)
        active_tgid = _v04h5_safe_int(payload.get("active_tgid"))
        active_label = payload.get("active_talkgroup_label", "")
        encrypted_or_muted = bool(payload.get("encrypted")) or bool(payload.get("muted"))
        blocked_active = active_tgid is not None and active_tgid in blocked
        blocked_label = _v04h5_label_is_blocked(active_label)
        if blocked_active or blocked_label or encrypted_or_muted:
            payload["suppressed_active_tgid"] = active_tgid
            payload["suppressed_active_talkgroup_label"] = active_label
            payload["active_tgid"] = None
            payload["active_talkgroup_label"] = ""
            payload["active_voice_frequency_hz"] = None
        payload["blocked_talkgroups"] = {
            "count": len(getattr(manager, "blocked_tgid_labels", {}) or {}),
            "labels": {str(k): v for k, v in sorted((getattr(manager, "blocked_tgid_labels", {}) or {}).items())},
            "audio_gate_hold_ms": _V04H5_AUDIO_GATE_HOLD_MS,
        }
    except Exception:
        pass
    return payload


if hasattr(ScannerManager, "refresh_config_summary"):
    _v04h5_original_refresh_config_summary = ScannerManager.refresh_config_summary

    def _v04h5_refresh_config_summary(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = _v04h5_original_refresh_config_summary(self, *args, **kwargs)
        _v04h5_rebuild_blocked_tgids(self)
        return result

    ScannerManager.refresh_config_summary = _v04h5_refresh_config_summary

if hasattr(ScannerManager, "_apply_runtime_status_update"):
    _v04h5_original_apply_runtime_status_update = ScannerManager._apply_runtime_status_update

    def _v04h5_apply_runtime_status_update(self: Any, update: Any) -> None:
        tgid = _v04h5_safe_int(getattr(update, "tgid", None))
        label = getattr(update, "talkgroup_label", "")
        encrypted = getattr(update, "encrypted", None) is True
        muted = getattr(update, "muted", None) is True
        blocked = getattr(self, "blocked_tgids", None)
        if blocked is None:
            blocked = _v04h5_rebuild_blocked_tgids(self)
        is_blocked_tgid = tgid is not None and tgid in blocked
        is_blocked_label = _v04h5_label_is_blocked(label)
        if tgid is not None and (is_blocked_tgid or is_blocked_label or encrypted or muted):
            reason = "blocked" if (is_blocked_tgid or is_blocked_label) else "encrypted"
            _v04h5_gate_audio_for_tgid(self, tgid, reason)
            with self.lock:
                if _v04h5_safe_int(getattr(self.status, "active_tgid", None)) == tgid or reason in ("blocked", "encrypted"):
                    self.status.active_tgid = None
                    self.status.active_talkgroup_label = ""
                    self.status.active_voice_frequency_hz = None
                self.status.encrypted = bool(encrypted or is_blocked_tgid or is_blocked_label)
                self.status.muted = True
                try:
                    self.status.runtime_status = update.to_status_dict()
                except Exception:
                    pass
                self.status.last_event = f"Suppressed {reason} TGID {tgid} from active audio display and gated browser audio"
                self.status.updated_utc = time.time()
            return
        return _v04h5_original_apply_runtime_status_update(self, update)

    ScannerManager._apply_runtime_status_update = _v04h5_apply_runtime_status_update

_v04h5_original_status_payload = getattr(ScannerManager, "status_payload", None)

def _v04h5_status_payload(self: Any) -> dict[str, Any]:
    if _v04h5_original_status_payload is not None:
        payload = _v04h5_original_status_payload(self)
    else:
        payload = asdict(self.status)
    if isinstance(payload, dict):
        return _v04h5_filter_payload(self, payload)
    return payload

ScannerManager.status_payload = _v04h5_status_payload

_v04h5_original_activity_payload = getattr(ScannerManager, "activity_payload", None)

def _v04h5_activity_payload(self: Any) -> dict[str, Any]:
    if _v04h5_original_activity_payload is not None:
        payload = _v04h5_original_activity_payload(self)
    else:
        payload = self.status_payload()
    if isinstance(payload, dict):
        return _v04h5_filter_payload(self, payload)
    return payload

ScannerManager.activity_payload = _v04h5_activity_payload
# END V0.4H5_BLOCKED_TGID_AUDIO_GATE


# ANALOG_DASHBOARD_STATUS_V1
_ANALOG_DASHBOARD_ROOT = Path(os.environ.get("PI_SCANNER_ANALOG_ROOT", "/home/pi/PI-SCANNER"))
_ANALOG_DASHBOARD_ROLES = {
    "analog_2m": {"label": "VHF", "status_file": "analog_2m.json", "audio_port": 8073, "expected_serial": "00000144"},
    "analog_70cm": {"label": "UHF", "status_file": "analog_70cm.json", "audio_port": 8074, "expected_serial": "00000440"},
}


def _analog_dashboard_status_payload(host_header: str = "") -> dict[str, Any]:
    hostname = str(host_header or "").split(":", 1)[0].strip() or "127.0.0.1"
    status_dir = _ANALOG_DASHBOARD_ROOT / "runtime" / "status"
    now = time.time()
    roles: dict[str, Any] = {}
    all_ok = True
    for role, metadata in _ANALOG_DASHBOARD_ROLES.items():
        path = status_dir / metadata["status_file"]
        payload: dict[str, Any] = {}
        error = ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("status root is not an object")
        except Exception as exc:
            error = str(exc)
            all_ok = False
        age_seconds = None
        try:
            age_seconds = max(0.0, now - path.stat().st_mtime)
        except OSError:
            pass
        state = str(payload.get("state") or ("offline" if error else "unknown"))
        serial = str(payload.get("rtl_serial") or "")
        fresh = age_seconds is not None and age_seconds <= 15.0
        role_ok = not error and fresh and serial == metadata["expected_serial"] and state not in {"error", "stopped", "offline"}
        all_ok = all_ok and role_ok
        roles[role] = {
            "ok": role_ok,
            "role": role,
            "label": metadata["label"],
            "state": state,
            "rtl_serial": serial or metadata["expected_serial"],
            "status_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "channel_count": payload.get("channel_count"),
            "channel_tunes": payload.get("channel_tunes"),
            "scan_cycles": payload.get("scan_cycles"),
            "lock_count": payload.get("lock_count"),
            "frames_received": payload.get("frames_received"),
            "bytes_received": payload.get("bytes_received"),
            "frames_forwarded": payload.get("frames_forwarded"),
            "current_channel": payload.get("current_channel"),
            "last_lock": payload.get("last_lock"),
            "rms": payload.get("rms"),
            "baseline_rms": payload.get("baseline_rms"),
            "threshold_rms": payload.get("threshold_rms"),
            "rf_input_sample_rate_hz": payload.get("rf_input_sample_rate_hz"),
            "audio_sample_rate_hz": payload.get("audio_sample_rate_hz"),
            "audio_url": f"http://{hostname}:{metadata['audio_port']}/audio.wav",
            "error": error or None,
        }
    return {"ok": all_ok, "analog_root": str(_ANALOG_DASHBOARD_ROOT), "updated_epoch": now, "roles": roles}

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
            # PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
            if path == "/api/receivers/inventory":
                self._send_json(MANAGER.receiver_inventory_payload())
                return
            # ANALOG_CSV_CHANNEL_IMPORT_V1
            # ANALOG_DASHBOARD_STATUS_V1
            if path == "/api/analog/status":
                self._send_json(_analog_dashboard_status_payload(self.headers.get("Host", "")))
                return
            if path == "/api/analog/channels":
                from pi_p25_scanner.analog_channels import channels_payload
                self._send_json(channels_payload())
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
            # ANALOG_CSV_CHANNEL_IMPORT_V1
            if path == "/api/analog/channels/import":
                from pi_p25_scanner.analog_channels import AnalogChannelError, import_csv_request
                try:
                    result = import_csv_request(self._read_json())
                except AnalogChannelError as exc:
                    raise ConfigError(str(exc)) from exc
                self._send_json(result, HTTPStatus.ACCEPTED)
                return
            if path == "/api/p25/csv/import":
                from pi_p25_scanner.analog_channels import P25CsvError, import_p25_csv_request
                try:
                    result = import_p25_csv_request(self._read_json())
                except P25CsvError as exc:
                    raise ConfigError(str(exc)) from exc
                self._send_json(result, HTTPStatus.ACCEPTED)
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



# BEGIN V0_4H2_RUNTIME_ACTIVITY_ENDPOINT
# Lightweight active-talkgroup endpoint.  This is intentionally installed as a
# runtime wrapper so it survives backend.py route-layout changes from earlier
# recovery patches.
def _v0_4h2_json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _v0_4h2_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_v0_4h2_json_safe(v) for v in value]
    return str(value)


def _v0_4h2_activity_payload():
    status = getattr(MANAGER, "status", None)
    if status is None:
        return {"ok": False, "error": "scanner status unavailable"}

    def g(name, default=None):
        return getattr(status, name, default)

    active_tgid = g("active_tgid")
    active_label = g("active_talkgroup_label", "") or ""
    last_tgid = g("last_active_tgid")
    last_label = g("last_active_talkgroup_label", "") or ""
    payload = {
        "ok": True,
        "source": "v0_4h2_fast_activity",
        "scanner_state": g("scanner_state", "unknown"),
        "updated_utc": g("updated_utc"),
        "active_control_frequency_hz": g("active_control_frequency_hz"),
        "active_voice_frequency_hz": g("active_voice_frequency_hz"),
        "active_tgid": active_tgid,
        "active_talkgroup_label": active_label,
        "last_active_tgid": last_tgid,
        "last_active_talkgroup_label": last_label,
        "last_active_voice_frequency_hz": g("last_active_voice_frequency_hz"),
        "last_active_updated_utc": g("last_active_updated_utc"),
        "display_tgid": active_tgid if active_tgid is not None else last_tgid,
        "display_talkgroup_label": active_label or last_label,
        "display_voice_frequency_hz": g("active_voice_frequency_hz") or g("last_active_voice_frequency_hz"),
        "p25_phase": g("p25_phase", "unknown"),
        "encrypted": g("encrypted", False),
        "muted": g("muted", False),
        "runtime_status": g("runtime_status", {}),
        "activity_summary": g("activity_summary", {}),
    }
    return _v0_4h2_json_safe(payload)


if hasattr(Handler, "do_GET") and not getattr(Handler, "_v0_4h2_activity_wrapped", False):
    _v0_4h2_original_do_GET = Handler.do_GET

    def _v0_4h2_do_GET(self):
        route = self.path.split("?", 1)[0].split("#", 1)[0]
        if route == "/api/activity":
            try:
                self._send_json(_v0_4h2_activity_payload())
            except Exception as exc:  # keep endpoint failure JSON-safe
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        return _v0_4h2_original_do_GET(self)

    Handler.do_GET = _v0_4h2_do_GET
    Handler._v0_4h2_activity_wrapped = True


def _v0_4h2_unbuffered_env(env):
    merged = os.environ.copy()
    if isinstance(env, dict):
        merged.update(env)
    merged["PYTHONUNBUFFERED"] = "1"
    return merged
# V0_4H3_DECODER_EXIT_RECOVERY: OP25 launch env restored to validated command_env
# END V0_4H2_RUNTIME_ACTIVITY_ENDPOINT

# BEGIN V0.4D3D RadioReference picker endpoint wrapper
def _rr_d3d_endpoint_payload(kind: str, request: dict[str, Any]) -> dict[str, Any]:
    from pi_p25_scanner import radioreference_import as rr_import  # noqa: E402
    if kind == "systems":
        return rr_import.discover_systems(request)
    if kind == "sites":
        return rr_import.discover_sites(request)
    raise RadioReferenceError(f"unknown RadioReference picker endpoint: {kind}")


_RR_D3D_ORIGINAL_DO_POST = Handler.do_POST


def _rr_d3d_do_POST(self: Handler) -> None:  # noqa: N802
    try:
        if self.path == "/api/radioreference/systems":
            self._send_json(_rr_d3d_endpoint_payload("systems", self._read_json()), HTTPStatus.ACCEPTED)
            return
        if self.path == "/api/radioreference/sites":
            self._send_json(_rr_d3d_endpoint_payload("sites", self._read_json()), HTTPStatus.ACCEPTED)
            return
    except (ConfigError, RadioReferenceError) as exc:
        self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.BAD_REQUEST)
        return
    except Exception as exc:
        self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    return _RR_D3D_ORIGINAL_DO_POST(self)


Handler.do_POST = _rr_d3d_do_POST
# END V0.4D3D RadioReference picker endpoint wrapper


# BEGIN V0.4D3E backend RadioReference picker endpoint wrapper
def _v04d3e_query_payload(path: str) -> dict[str, Any]:
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    payload: dict[str, Any] = {}
    for key, values in query.items():
        if values:
            payload[key] = values[-1]
    return payload


def _v04d3e_rr_picker_dispatch(handler: Handler, endpoint: str, payload: dict[str, Any]) -> bool:
    try:
        from pi_p25_scanner.radioreference_import import rr_picker_find_sites, rr_picker_find_systems
        if endpoint == "/api/radioreference/systems":
            handler._send_json(rr_picker_find_systems(payload), HTTPStatus.OK)
            return True
        if endpoint == "/api/radioreference/sites":
            handler._send_json(rr_picker_find_sites(payload), HTTPStatus.OK)
            return True
    except RadioReferenceError as exc:
        handler._send_json({"ok": False, "error": str(exc), "endpoint": endpoint}, HTTPStatus.BAD_REQUEST)
        return True
    except Exception as exc:
        handler._send_json({"ok": False, "error": str(exc), "endpoint": endpoint}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return True
    return False


_v04d3e_original_do_get = Handler.do_GET
_v04d3e_original_do_post = Handler.do_POST


def _v04d3e_do_get(self: Handler) -> None:  # noqa: N802
    from urllib.parse import urlparse
    endpoint = urlparse(self.path).path
    if endpoint in ("/api/radioreference/systems", "/api/radioreference/sites"):
        if _v04d3e_rr_picker_dispatch(self, endpoint, _v04d3e_query_payload(self.path)):
            return
    return _v04d3e_original_do_get(self)


def _v04d3e_do_post(self: Handler) -> None:  # noqa: N802
    from urllib.parse import urlparse
    endpoint = urlparse(self.path).path
    if endpoint in ("/api/radioreference/systems", "/api/radioreference/sites"):
        try:
            payload = self._read_json()
        except Exception:
            payload = {}
        if _v04d3e_rr_picker_dispatch(self, endpoint, payload):
            return
    return _v04d3e_original_do_post(self)


Handler.do_GET = _v04d3e_do_get
Handler.do_POST = _v04d3e_do_post
# END V0.4D3E backend RadioReference picker endpoint wrapper


# BEGIN V0.4D3J radioreference explicit SOAP picker runtime routes
def _install_v0_4d3j_radioreference_picker_routes() -> None:
    """Install robust RadioReference picker endpoints without fragile route edits."""
    import urllib.parse as _v04d3j_urlparse

    try:
        from pi_p25_scanner.radioreference_picker_runtime import (
            radioreference_picker_sites as _v04d3j_rr_sites,
            radioreference_picker_systems as _v04d3j_rr_systems,
        )
    except Exception as exc:  # pragma: no cover - visible through endpoint response
        _import_error = exc
        _v04d3j_rr_sites = None
        _v04d3j_rr_systems = None
    else:
        _import_error = None

    def _query_payload(handler: Handler) -> dict[str, Any]:
        parsed = _v04d3j_urlparse.urlparse(handler.path)
        values = _v04d3j_urlparse.parse_qs(parsed.query)
        payload: dict[str, Any] = {}
        for key, raw_values in values.items():
            if raw_values:
                payload[key] = raw_values[-1]
        return payload

    original_get = Handler.do_GET
    original_post = Handler.do_POST

    def do_GET(self: Handler) -> None:  # noqa: N802
        parsed = _v04d3j_urlparse.urlparse(self.path)
        if parsed.path == "/api/radioreference/systems":
            if _import_error is not None or _v04d3j_rr_systems is None:
                self._send_json({"ok": False, "error": f"RadioReference picker import failed: {_import_error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            try:
                self._send_json(_v04d3j_rr_systems(_query_payload(self)), HTTPStatus.ACCEPTED)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "endpoint": parsed.path}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/radioreference/sites":
            if _import_error is not None or _v04d3j_rr_sites is None:
                self._send_json({"ok": False, "error": f"RadioReference picker import failed: {_import_error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            try:
                self._send_json(_v04d3j_rr_sites(_query_payload(self)), HTTPStatus.ACCEPTED)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "endpoint": parsed.path}, HTTPStatus.BAD_REQUEST)
            return
        return original_get(self)

    def do_POST(self: Handler) -> None:  # noqa: N802
        parsed = _v04d3j_urlparse.urlparse(self.path)
        if parsed.path == "/api/radioreference/systems":
            if _import_error is not None or _v04d3j_rr_systems is None:
                self._send_json({"ok": False, "error": f"RadioReference picker import failed: {_import_error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            try:
                self._send_json(_v04d3j_rr_systems(self._read_json()), HTTPStatus.ACCEPTED)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "endpoint": parsed.path}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/radioreference/sites":
            if _import_error is not None or _v04d3j_rr_sites is None:
                self._send_json({"ok": False, "error": f"RadioReference picker import failed: {_import_error}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            try:
                self._send_json(_v04d3j_rr_sites(self._read_json()), HTTPStatus.ACCEPTED)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc), "endpoint": parsed.path}, HTTPStatus.BAD_REQUEST)
            return
        return original_post(self)

    Handler.do_GET = do_GET
    Handler.do_POST = do_POST


_install_v0_4d3j_radioreference_picker_routes()
# END V0.4D3J radioreference explicit SOAP picker runtime routes

# BEGIN V0.4D3K forced RadioReference picker runtime bind
try:
    from pi_p25_scanner.radioreference_picker_d3k import (  # noqa: E402
        PARSER_MARKER as _RR_D3K_PARSER_MARKER,
        discover_sites as _rr_d3k_discover_sites,
        discover_systems as _rr_d3k_discover_systems,
    )

    _RR_D3K_ORIGINAL_DO_POST = Handler.do_POST

    def _rr_d3k_do_POST(self) -> None:  # noqa: N802
        endpoint = self.path.split("?", 1)[0]
        try:
            if endpoint == "/api/radioreference/systems":
                self._send_json(_rr_d3k_discover_systems(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if endpoint == "/api/radioreference/sites":
                self._send_json(_rr_d3k_discover_sites(self._read_json()), HTTPStatus.ACCEPTED)
                return
        except (ConfigError, RadioReferenceError) as exc:
            self._send_json(
                {"ok": False, "error": str(exc), "endpoint": endpoint, "picker_parser": _RR_D3K_PARSER_MARKER},
                HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "endpoint": endpoint,
                    "picker_parser": _RR_D3K_PARSER_MARKER,
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        return _RR_D3K_ORIGINAL_DO_POST(self)

    Handler.do_POST = _rr_d3k_do_POST
except Exception as _rr_d3k_bind_error:
    # Keep the backend bootable. /api/status will still work; deploy/probe checks
    # py_compile/import so this should only fire on unexpected runtime conditions.
    pass
# END V0.4D3K forced RadioReference picker runtime bind


# BEGIN V0.5A1 PPM calibration route
try:
    from pi_p25_scanner.ppm_calibration import (  # noqa: E402
        PpmCalibrationError as _PpmCalibrationError,
        calibrate_ppm as _calibrate_ppm,
        last_ppm_calibration_report as _last_ppm_calibration_report,
    )
except Exception as _ppm_calibration_import_error:  # pragma: no cover - staged upgrade guard
    class _PpmCalibrationError(Exception):
        pass

    def _last_ppm_calibration_report() -> dict[str, Any]:
        return {"ok": False, "calibrated": False, "error": f"PPM calibration module is not installed: {_ppm_calibration_import_error}"}

    def _calibrate_ppm(_request: dict[str, Any]) -> dict[str, Any]:
        raise _PpmCalibrationError(f"PPM calibration module is not installed: {_ppm_calibration_import_error}")


_PPM_V0_5A1_ORIG_DO_GET = Handler.do_GET
_PPM_V0_5A1_ORIG_DO_POST = Handler.do_POST


def _ppm_v0_5a1_do_get(self: Handler) -> None:  # noqa: N802
    if self.path.split("?", 1)[0] == "/api/calibration/ppm/status":
        self._send_json(_last_ppm_calibration_report())
        return
    return _PPM_V0_5A1_ORIG_DO_GET(self)


def _ppm_v0_5a1_do_post(self: Handler) -> None:  # noqa: N802
    if self.path.split("?", 1)[0] == "/api/calibration/ppm/run":
        try:
            request = self._read_json()
            try:
                MANAGER.stop()
            except Exception:
                pass
            report = _calibrate_ppm(request)
            try:
                MANAGER.refresh_config_summary()
                MANAGER.generate_config()
            except Exception as exc:
                report.setdefault("warnings", []).append(f"post-calibration refresh/generate warning: {exc}")
            self._send_json(report, HTTPStatus.ACCEPTED)
        except (ConfigError, _PpmCalibrationError) as exc:
            self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    return _PPM_V0_5A1_ORIG_DO_POST(self)


Handler.do_GET = _ppm_v0_5a1_do_get
Handler.do_POST = _ppm_v0_5a1_do_post
# END V0.5A1 PPM calibration route

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




# BEGIN RR_PICKER_RUNTIME_ROUTES_V0_4D3B
def _install_radioreference_picker_routes_v0_4d3b() -> None:
    try:
        from pi_p25_scanner.radioreference_import import (  # type: ignore
            discover_radioreference_sites,
            discover_radioreference_systems,
        )
    except Exception as exc:  # pragma: no cover - optional staged upgrade
        route_import_error = exc
        discover_radioreference_sites = None  # type: ignore[assignment]
        discover_radioreference_systems = None  # type: ignore[assignment]
    else:
        route_import_error = None

    original_do_post = Handler.do_POST

    def do_post_with_rr_picker(self: Handler) -> None:  # noqa: ANN001
        try:
            if self.path == "/api/radioreference/systems":
                if discover_radioreference_systems is None:
                    raise RadioReferenceError(f"RadioReference picker is not available: {route_import_error}")
                self._send_json(discover_radioreference_systems(self._read_json()), HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/radioreference/sites":
                if discover_radioreference_sites is None:
                    raise RadioReferenceError(f"RadioReference picker is not available: {route_import_error}")
                self._send_json(discover_radioreference_sites(self._read_json()), HTTPStatus.ACCEPTED)
                return
            return original_do_post(self)
        except (ConfigError, RadioReferenceError) as exc:
            self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc), "endpoint": self.path}, HTTPStatus.INTERNAL_SERVER_ERROR)

    Handler.do_POST = do_post_with_rr_picker


_install_radioreference_picker_routes_v0_4d3b()
# END RR_PICKER_RUNTIME_ROUTES_V0_4D3B



# BEGIN V0.4D3G RadioReference picker endpoint wrapper
# This wrapper owns the picker endpoints regardless of prior staged route patches.
def _rr_d3g_send_json(handler, payload, status=HTTPStatus.ACCEPTED):
    handler._send_json(payload, status)


def _rr_d3g_handler_do_post(self):
    if self.path in ('/api/radioreference/systems', '/api/radioreference/discover-systems'):
        try:
            from pi_p25_scanner.radioreference_import import rr_d3g_discover_systems
            _rr_d3g_send_json(self, rr_d3g_discover_systems(self._read_json()), HTTPStatus.ACCEPTED)
        except (ConfigError, RadioReferenceError) as exc:
            _rr_d3g_send_json(self, {'ok': False, 'error': str(exc), 'endpoint': self.path}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            _rr_d3g_send_json(self, {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'endpoint': self.path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    if self.path in ('/api/radioreference/sites', '/api/radioreference/discover-sites'):
        try:
            from pi_p25_scanner.radioreference_import import rr_d3g_discover_sites
            _rr_d3g_send_json(self, rr_d3g_discover_sites(self._read_json()), HTTPStatus.ACCEPTED)
        except (ConfigError, RadioReferenceError) as exc:
            _rr_d3g_send_json(self, {'ok': False, 'error': str(exc), 'endpoint': self.path}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            _rr_d3g_send_json(self, {'ok': False, 'error': f'{type(exc).__name__}: {exc}', 'endpoint': self.path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    return _RR_D3G_ORIGINAL_HANDLER_DO_POST(self)


try:
    _RR_D3G_ORIGINAL_HANDLER_DO_POST
except NameError:
    _RR_D3G_ORIGINAL_HANDLER_DO_POST = Handler.do_POST
Handler.do_POST = _rr_d3g_handler_do_post
# END V0.4D3G RadioReference picker endpoint wrapper


# BEGIN V0.4D3L final forced RadioReference picker route override
_ORIGINAL_HANDLER_DO_POST_BEFORE_V0_4D3L = Handler.do_POST

def _handler_do_post_v0_4d3l(self) -> None:
    path = self.path.split("?", 1)[0]
    try:
        if path == "/api/radioreference/systems":
            from pi_p25_scanner.radioreference_picker_forced_v0_4d3l import find_systems
            self._send_json(find_systems(self._read_json()), HTTPStatus.ACCEPTED)
            return
        if path == "/api/radioreference/sites":
            from pi_p25_scanner.radioreference_picker_forced_v0_4d3l import find_sites
            self._send_json(find_sites(self._read_json()), HTTPStatus.ACCEPTED)
            return
    except (ConfigError, RadioReferenceError) as exc:
        self._send_json({"ok": False, "picker_parser": "forced-explicit-soap-v0.4d3l", "error": str(exc), "endpoint": path}, HTTPStatus.BAD_REQUEST)
        return
    except Exception as exc:
        self._send_json({"ok": False, "picker_parser": "forced-explicit-soap-v0.4d3l", "error": f"{type(exc).__name__}: {exc}", "endpoint": path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    return _ORIGINAL_HANDLER_DO_POST_BEFORE_V0_4D3L(self)

Handler.do_POST = _handler_do_post_v0_4d3l
# END V0.4D3L final forced RadioReference picker route override

# BEGIN V0.4D3M final RadioReference US country picker route override
_ORIGINAL_HANDLER_DO_POST_BEFORE_V0_4D3M = Handler.do_POST

def _handler_do_post_v0_4d3m(self) -> None:
    path = self.path.split("?", 1)[0]
    try:
        if path == "/api/radioreference/systems":
            from pi_p25_scanner.radioreference_picker_forced_v0_4d3m import find_systems
            self._send_json(find_systems(self._read_json()), HTTPStatus.ACCEPTED)
            return
        if path == "/api/radioreference/sites":
            from pi_p25_scanner.radioreference_picker_forced_v0_4d3m import find_sites
            self._send_json(find_sites(self._read_json()), HTTPStatus.ACCEPTED)
            return
    except (ConfigError, RadioReferenceError) as exc:
        self._send_json({"ok": False, "picker_parser": "us-country-explicit-soap-v0.4d3m", "error": str(exc), "endpoint": path}, HTTPStatus.BAD_REQUEST)
        return
    except Exception as exc:
        self._send_json({"ok": False, "picker_parser": "us-country-explicit-soap-v0.4d3m", "error": f"{type(exc).__name__}: {exc}", "endpoint": path}, HTTPStatus.INTERNAL_SERVER_ERROR)
        return
    return _ORIGINAL_HANDLER_DO_POST_BEFORE_V0_4D3M(self)

Handler.do_POST = _handler_do_post_v0_4d3m
# END V0.4D3M final RadioReference US country picker route override

# BEGIN V0.5AC PRE-MAIN RR SITE ENRICHMENT
_RR_V05AC_BASE_ENDPOINT_PAYLOAD = _rr_d3d_endpoint_payload


def _rr_d3d_endpoint_payload(kind, payload):
    result = _RR_V05AC_BASE_ENDPOINT_PAYLOAD(kind, payload)
    if kind != "sites" or not isinstance(result, dict):
        return result

    sites = list(result.get("sites") or [])
    for site in sites:
        if not isinstance(site, dict):
            continue

        site_id = site.get("site_id") or site.get("siteId")
        try:
            site_id_int = int(site_id) if site_id is not None else None
        except (TypeError, ValueError):
            site_id_int = None

        freqs = set()
        for value in site.get("control_channels_hz") or []:
            try:
                freqs.add(int(value))
            except (TypeError, ValueError):
                pass

        is_tenderfoot = site_id_int == 12917

        if is_tenderfoot:
            site["site_id"] = 12917
            site["name"] = "Tenderfoot II"
            site["site_description"] = "Tenderfoot II"
            site["location"] = "Teller, CO"
            site["county_id"] = 300
            site["rfss"] = 6
            site["site_number"] = 17
            site["label"] = "Tenderfoot II (RFSS 6, Site 017, RR ID 12917)"

    sites.sort(
        key=lambda site: (
            0 if isinstance(site, dict) and int(site.get("site_id") or 0) == 12917 else 1,
            str(site.get("label") or site.get("name") or "").lower()
            if isinstance(site, dict)
            else "",
        )
    )

    result["sites"] = sites
    result["site_count"] = len(sites)
    result["returned_site_count"] = len(sites)
    result["truncated"] = False
    result["site_limit"] = None
    result["dispatcher_version"] = "v0.5ac-pre-main"
    return result
# END V0.5AC PRE-MAIN RR SITE ENRICHMENT

# BEGIN V0.5AD FINAL PRE-MAIN RR DISPATCHER
_RR_V05AD_BASE_ENDPOINT_PAYLOAD = _rr_d3d_endpoint_payload


def _rr_d3d_endpoint_payload(kind, payload):
    result = _RR_V05AD_BASE_ENDPOINT_PAYLOAD(kind, payload)
    if kind != "sites" or not isinstance(result, dict):
        return result

    sites = list(result.get("sites") or [])
    for site in sites:
        if not isinstance(site, dict):
            continue
        try:
            site_id = int(site.get("site_id") or site.get("siteId") or 0)
        except (TypeError, ValueError):
            site_id = 0

        freqs=set()
        for value in site.get("control_channels_hz") or []:
            try:
                freqs.add(int(value))
            except (TypeError, ValueError):
                pass

        if site_id == 12917:
            site.update({
                "site_id": 12917,
                "name": "Tenderfoot II",
                "site_description": "Tenderfoot II",
                "location": "Teller, CO",
                "county_id": 300,
                "rfss": 6,
                "site_number": 17,
                "label": "Tenderfoot II (RFSS 6, Site 017, RR ID 12917)",
            })

    def sort_key(site):
        if not isinstance(site, dict):
            return (2, "")
        try:
            sid=int(site.get("site_id") or 0)
        except (TypeError, ValueError):
            sid=0
        return (
            0 if sid == 12917 else 1,
            str(site.get("label") or site.get("name") or "").lower(),
        )

    sites.sort(key=sort_key)
    result["sites"] = sites
    result["site_count"] = len(sites)
    result["returned_site_count"] = len(sites)
    result["truncated"] = False
    result["site_limit"] = None
    result["dispatcher_version"] = "v0.5ad-final-pre-main"
    return result
# END V0.5AD FINAL PRE-MAIN RR DISPATCHER

# BEGIN V0.5AG EXACT TENDERFOOT SITE ROUTE ENRICHMENT
_HANDLER_V05AG_BASE_DO_POST = Handler.do_POST


def _v05ag_enrich_rr_sites(result):
    if not isinstance(result, dict):
        return result

    sites = list(result.get("sites") or [])
    for site in sites:
        if not isinstance(site, dict):
            continue

        try:
            site_id = int(site.get("site_id") or site.get("siteId") or 0)
        except (TypeError, ValueError):
            site_id = 0

        freqs = set()
        for value in site.get("control_channels_hz") or []:
            try:
                freqs.add(int(value))
            except (TypeError, ValueError):
                pass

        if site_id == 12917:
            site.update({
                "site_id": 12917,
                "name": "Tenderfoot II",
                "site_description": "Tenderfoot II",
                "location": "Teller, CO",
                "county_id": 300,
                "rfss": 6,
                "site_number": 17,
                "label": "Tenderfoot II (RFSS 6, Site 017, RR ID 12917)",
            })

    def sort_key(site):
        if not isinstance(site, dict):
            return (2, "")
        try:
            sid = int(site.get("site_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        return (
            0 if sid == 12917 else 1,
            str(site.get("label") or site.get("name") or "").lower(),
        )

    sites.sort(key=sort_key)
    result["sites"] = sites
    result["site_count"] = len(sites)
    result["returned_site_count"] = len(sites)
    result["truncated"] = False
    result["site_limit"] = None
    result["route_version"] = "control-markers-only-v0.5"
    return result


def _handler_do_post_v0_5ag(self):
    path = self.path.split("?", 1)[0]
    if path == "/api/radioreference/sites":
        try:
            from pi_p25_scanner.radioreference_picker_forced_v0_4d3m import find_sites
            result = _v05ag_enrich_rr_sites(find_sites(self._read_json()))
            self._send_json(result, HTTPStatus.ACCEPTED)
            return
        except (ConfigError, RadioReferenceError) as exc:
            self._send_json(
                {
                    "ok": False,
                    "route_version": "control-markers-only-v0.5",
                    "error": str(exc),
                    "endpoint": path,
                },
                HTTPStatus.BAD_REQUEST,
            )
            return
        except Exception as exc:
            self._send_json(
                {
                    "ok": False,
                    "route_version": "control-markers-only-v0.5",
                    "error": f"{type(exc).__name__}: {exc}",
                    "endpoint": path,
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

    return _HANDLER_V05AG_BASE_DO_POST(self)


Handler.do_POST = _handler_do_post_v0_5ag
# END V0.5AG EXACT TENDERFOOT SITE ROUTE ENRICHMENT

if __name__ == "__main__":
    raise SystemExit(main())

# BEGIN V0.5Y RR SITES DISPATCHER OVERRIDE
_RR_V05Y_BASE_ENDPOINT_PAYLOAD = _rr_d3d_endpoint_payload


def _rr_d3d_endpoint_payload(kind, payload):
    if kind == "sites":
        from pi_p25_scanner import radioreference_import as _rr_runtime
        return _rr_runtime.discover_radioreference_sites(payload)
    return _RR_V05Y_BASE_ENDPOINT_PAYLOAD(kind, payload)
# END V0.5Y RR SITES DISPATCHER OVERRIDE

# BEGIN V0.5AA LIVE PACKAGE RR SITES DISPATCHER
_RR_V05AA_BASE_ENDPOINT_PAYLOAD = _rr_d3d_endpoint_payload


def _rr_d3d_endpoint_payload(kind, payload):
    if kind == "sites":
        import importlib
        rr_runtime = importlib.import_module("pi_p25_scanner.radioreference_import")
        result = rr_runtime.discover_radioreference_sites(payload)
        result["dispatcher_version"] = "v0.5aa-live-package"
        result["runtime_module"] = rr_runtime.__name__
        result["runtime_module_file"] = str(getattr(rr_runtime, "__file__", ""))
        return result
    return _RR_V05AA_BASE_ENDPOINT_PAYLOAD(kind, payload)
# END V0.5AA LIVE PACKAGE RR SITES DISPATCHER

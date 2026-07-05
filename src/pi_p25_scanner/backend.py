#!/usr/bin/env python3
"""Minimal PI P25 Scanner web/API stub.

This is a V0.1A placeholder backend. It proves the project layout and API
shape before OP25 process control is added.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"
CONFIG_PATH = PROJECT_ROOT / "config" / "p25_systems.example.json"


@dataclass
class ScannerStatus:
    ok: bool = True
    scanner_state: str = "stopped"
    decoder_engine: str = "not_configured"
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
    last_event: str = "V0.1A backend stub running; decoder wrapper not started"
    warnings: list[str] = field(default_factory=list)


STATUS = ScannerStatus()


def load_example_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "systems": []}
    except json.JSONDecodeError as exc:
        return {"schema_version": 1, "systems": [], "error": str(exc)}


class Handler(SimpleHTTPRequestHandler):
    server_version = "PiP25Scanner/0.1A"

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server method name
        if self.path == "/api/status":
            self._send_json(asdict(STATUS))
            return
        if self.path == "/api/config":
            self._send_json(load_example_config())
            return
        if self.path in ("/", "/index.html"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server method name
        if self.path == "/api/scanner/start":
            STATUS.scanner_state = "not_implemented"
            STATUS.last_event = "Start requested; decoder wrapper will be added in V0.1B"
            self._send_json(asdict(STATUS), HTTPStatus.ACCEPTED)
            return
        if self.path == "/api/scanner/stop":
            STATUS.scanner_state = "stopped"
            STATUS.last_event = "Stop requested; backend stub is stopped"
            self._send_json(asdict(STATUS), HTTPStatus.ACCEPTED)
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def translate_path(self, path: str) -> str:
        rel = path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "index.html"
        return str((WEB_ROOT / rel).resolve())

    def guess_type(self, path: str) -> str:
        guessed, _ = mimetypes.guess_type(path)
        return guessed or "application/octet-stream"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PI P25 Scanner backend stub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"PI P25 Scanner backend stub listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopping PI P25 Scanner backend stub", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

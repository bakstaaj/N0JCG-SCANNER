#!/usr/bin/env bash
set -u

python3 - "$@" <<'PY_HTTP_RUNTIME'
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_DIR = Path('.p25_op25_http_runtime_probe_reports')
DEFAULT_BACKEND_URL = os.environ.get('P25_SCANNER_BACKEND_URL', 'http://127.0.0.1:8070')
DEFAULT_PATHS = ['/', '/status', '/status.json', '/api/status', '/terminal', '/trunk', '/metadata', '/metadata.json', '/json']
VALIDATED_MARKER_CANDIDATES = [
    Path('runtime/settings/op25_validated_rx_command.env'),
    Path('runtime/settings/op25_validated_command.env'),
    Path('runtime/settings/op25_command_candidate.json'),
]
HTTP_PORT_RE = re.compile(r'http:(?:\[[^\]]+\]|[^:\s]+):(?P<port>\d{1,5})')


def utc_stamp() -> str:
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())


def run_command(args: list[str], timeout: float = 3.0) -> tuple[int, str]:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return 127, f'{type(exc).__name__}: {exc}'
    return result.returncode, (result.stdout + result.stderr).strip()


def http_request(method: str, url: str, timeout: float = 1.0) -> tuple[int, str, str, str]:
    request = urllib.request.Request(url=url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(8192).decode('utf-8', errors='replace')
            content_type = response.headers.get('content-type', '')
            return int(response.status), content_type, body, ''
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode('utf-8', errors='replace')
        return int(exc.code), exc.headers.get('content-type', ''), body, ''
    except Exception as exc:
        return 0, '', '', f'{type(exc).__name__}: {exc}'


def get_json_detailed(url: str, timeout: float = 1.0) -> tuple[dict[str, Any] | None, str]:
    status, _content_type, body, error = http_request('GET', url, timeout=timeout)
    if error:
        return None, error
    if status <= 0:
        return None, f'HTTP status {status}'
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f'JSONDecodeError: {exc}'
    if not isinstance(value, dict):
        return None, f'JSON value was {type(value).__name__}, not object'
    return value, ''


def post_json_detailed(url: str, timeout: float = 4.0) -> tuple[dict[str, Any] | None, str]:
    status, _content_type, body, error = http_request('POST', url, timeout=timeout)
    if error:
        return None, error
    if status <= 0:
        return None, f'HTTP status {status}'
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f'JSONDecodeError: {exc}'
    if not isinstance(value, dict):
        return None, f'JSON value was {type(value).__name__}, not object'
    return value, ''


def boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', 'yes', '1', 'on'}:
            return True
        if lowered in {'false', 'no', '0', 'off'}:
            return False
    return None


def status_running(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    if str(status.get('scanner_state') or '').lower() == 'running':
        return True
    process = status.get('decoder_process')
    if isinstance(process, dict):
        return boolish(process.get('running')) is True
    return False


def iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_strings(item)


def ports_from_text(text: str) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for match in HTTP_PORT_RE.finditer(text):
        try:
            port = int(match.group('port'))
        except ValueError:
            continue
        if 0 < port < 65536 and port not in seen:
            seen.add(port)
            ports.append(port)
    return ports


def ports_from_obj(value: Any) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for text in iter_strings(value):
        for port in ports_from_text(text):
            if port not in seen:
                seen.add(port)
                ports.append(port)
    return ports


def read_marker_text() -> str:
    chunks: list[str] = []
    for path in VALIDATED_MARKER_CANDIDATES:
        if not path.exists():
            continue
        try:
            chunks.append(f'## {path}\n{path.read_text(encoding="utf-8", errors="replace")}')
        except OSError as exc:
            chunks.append(f'## {path}\nREAD_ERROR: {exc}')
    return '\n'.join(chunks)


def unique_ports(source_lists: list[list[int]], extra: list[int] | None = None) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()
    for source in source_lists + ([extra] if extra else []):
        for port in source:
            if 0 < int(port) < 65536 and int(port) not in seen:
                seen.add(int(port))
                ports.append(int(port))
    return ports


def parse_cli_ports(text: str) -> list[int]:
    ports: list[int] = []
    for raw in text.split(','):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 < value < 65536 and value not in ports:
            ports.append(value)
    return ports


def listener_snapshot() -> dict[str, Any]:
    command = shutil.which('ss')
    args = [command, '-ltnp'] if command else []
    if not command:
        command = shutil.which('netstat')
        args = [command, '-ltnp'] if command else []
    if not args:
        return {'command': '', 'rc': 127, 'text': 'no ss or netstat available', 'ports': []}
    rc, text = run_command(args, timeout=3.0)
    ports: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r':(?P<port>\d{1,5})(?:\s|$)', text):
        try:
            port = int(match.group('port'))
        except ValueError:
            continue
        if 0 < port < 65536 and port not in seen:
            seen.add(port)
            ports.append(port)
    return {'command': ' '.join(args), 'rc': rc, 'text': text, 'ports': ports}


def socket_connects(port: int, timeout: float = 0.25) -> tuple[bool, str]:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True, ''
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def probe_http_ports(ports: list[int], paths: list[str], timeout: float = 0.5) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for port in ports:
        for path in paths:
            url = f'http://127.0.0.1:{port}{path}'
            status, content_type, body, error = http_request('GET', url, timeout=timeout)
            if status > 0 or error:
                results.append(
                    {
                        'port': port,
                        'path': path,
                        'url': url,
                        'status': status,
                        'content_type': content_type,
                        'error': error,
                        'sample': body.replace('\r', ' ').replace('\n', ' ')[:240],
                    }
                )
    return results


def state_name(sample: dict[str, Any] | None) -> str:
    if not isinstance(sample, dict):
        return ''
    return str(sample.get('scanner_state') or '')


def summarize_status(samples: list[dict[str, Any]]) -> dict[str, Any]:
    states: Counter[str] = Counter()
    running = 0
    commands: list[Any] = []
    warnings: set[str] = set()
    for sample in samples:
        states[state_name(sample) or 'unknown'] += 1
        if status_running(sample):
            running += 1
        process = sample.get('decoder_process')
        if isinstance(process, dict):
            command = process.get('command')
            if command and command not in commands:
                commands.append(command)
        for warning in sample.get('warnings') or []:
            warnings.add(str(warning))
    return {
        'snapshot_count': len(samples),
        'running_snapshots': running,
        'states': dict(states),
        'decoder_commands': commands,
        'warnings': sorted(warnings),
    }


def make_report(summary: dict[str, Any]) -> tuple[str, int, int, int]:
    passes = 0
    warns = 0
    fails = 0
    lines: list[str] = []
    lines.append('# PI-P25-SCANNER OP25 HTTP Runtime Probe')
    lines.append('')

    def add(level: str, text: str) -> None:
        nonlocal passes, warns, fails
        lines.append(f'- {level}: {text}')
        if level == 'PASS':
            passes += 1
        elif level == 'WARN':
            warns += 1
        elif level == 'FAIL':
            fails += 1

    status_summary = summary.get('status_summary') or {}
    start = summary.get('start') or {}
    targets = summary.get('target_ports') or []
    listener_hits = summary.get('listener_hits') or {}
    socket_hits = summary.get('socket_connect_hits') or {}
    http_successes = summary.get('http_successes') or []
    status_errors = summary.get('status_errors') or []

    if summary.get('initial_status_captured'):
        add('PASS', 'backend initial /api/status captured')
    else:
        add('FAIL', 'backend initial /api/status was not captured')
    if start.get('requested'):
        if start.get('response_seen'):
            add('PASS', f"scanner start response seen: {start.get('scanner_state') or 'unknown'}")
        else:
            add('FAIL', f"scanner start response missing: {start.get('error') or 'no error detail'}")
    else:
        add('PASS', 'scanner was already running or --no-start was used')
    if status_summary.get('running_snapshots', 0) > 0:
        add('PASS', f"running status snapshots observed: {status_summary.get('running_snapshots')}")
    else:
        add('FAIL', 'no running status snapshots observed during short runtime probe')
    if status_errors:
        add('WARN', f"backend status poll errors observed: {len(status_errors)}")
    else:
        add('PASS', 'no backend status poll errors observed')
    if targets:
        add('PASS', f'OP25 HTTP target ports detected: {targets}')
    else:
        add('WARN', 'no OP25 HTTP target ports detected from status/start/marker')
    for port in targets:
        if listener_hits.get(str(port), 0) > 0:
            add('PASS', f'port {port} appeared in TCP listener snapshots')
        else:
            add('FAIL', f'port {port} never appeared as a TCP listener')
        if socket_hits.get(str(port), 0) > 0:
            add('PASS', f'port {port} accepted localhost TCP connections')
        else:
            add('WARN', f'port {port} did not accept localhost TCP connections')
    if http_successes:
        add('PASS', f'HTTP responses observed from OP25 target ports: {len(http_successes)}')
    elif targets:
        add('WARN', 'no HTTP responses observed from OP25 target ports')
    else:
        add('WARN', 'HTTP probing skipped because no target ports were detected')

    lines.append('')
    lines.append('## Target Port Sources')
    lines.append('```json')
    lines.append(json.dumps(summary.get('port_sources') or {}, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('## Status Summary')
    lines.append('```json')
    lines.append(json.dumps(status_summary, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('## Runtime Diagnostics')
    lines.append('```json')
    lines.append(json.dumps(summary.get('runtime_diagnostics') or {}, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('## Backend Status Poll Errors')
    if status_errors:
        lines.append('```text')
        lines.extend(str(error) for error in status_errors[:30])
        lines.append('```')
    else:
        lines.append('- none')
    lines.append('')
    lines.append('## Listener Snapshots')
    for snap in (summary.get('listener_snapshots') or [])[:6]:
        lines.append(f"### t+{snap.get('elapsed_sec')}s")
        lines.append('```text')
        text = str(snap.get('text') or 'none')
        lines.extend(text.splitlines()[:30] or ['none'])
        lines.append('```')
    lines.append('')
    lines.append('## HTTP Probe Results')
    probe_results = summary.get('http_probe_results') or []
    if probe_results:
        lines.append('```json')
        lines.append(json.dumps(probe_results[:80], indent=2, sort_keys=True))
        lines.append('```')
    else:
        lines.append('- none')
    lines.append('')
    lines.append('## Backend Warnings')
    warnings = status_summary.get('warnings') or []
    if warnings:
        for warning in warnings:
            lines.append(f'- {warning}')
    else:
        lines.append('- none')
    lines.append('')
    lines.append(f'SUMMARY: PASS={passes} WARN={warns} FAIL={fails}')
    lines.append('FINAL: PASS' if fails == 0 else 'FINAL: FAIL')
    return '\n'.join(lines) + '\n', passes, warns, fails


def write_outputs(summary: dict[str, Any], prefix: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    report, passes, warns, fails = make_report(summary)
    summary['result_counts'] = {'pass': passes, 'warn': warns, 'fail': fails}
    report_path = REPORT_DIR / f'{prefix}_{stamp}.md'
    summary_path = REPORT_DIR / f'{prefix}_{stamp}.json'
    report_path.write_text(report, encoding='utf-8', newline='\n')
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    print(report, end='')
    print(f'Report: {report_path}')
    print(f'Summary JSON: {summary_path}')
    print(f'SUMMARY: PASS={passes} WARN={warns} FAIL={fails}')
    print('FINAL: PASS' if fails == 0 else 'FINAL: FAIL')
    return report_path, summary_path


class _SelfTestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'ok': True, 'path': self.path}).encode('utf-8')
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def run_self_test() -> int:
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _SelfTestHandler)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sample = {
            'scanner_state': 'running',
            'decoder_process': {'running': True, 'command': ['rx.py', '-l', f'http:127.0.0.1:{port}']},
            'warnings': [],
        }
        listener = listener_snapshot()
        open_ok, _error = socket_connects(port)
        http_results = probe_http_ports([port], ['/status'], timeout=1.0)
        successes = [result for result in http_results if int(result.get('status') or 0) > 0]
        summary = {
            'initial_status_captured': True,
            'start': {'requested': True, 'response_seen': True, 'scanner_state': 'running'},
            'target_ports': [port],
            'port_sources': {'self_test': [port]},
            'status_summary': summarize_status([sample]),
            'status_errors': [],
            'listener_hits': {str(port): 1},
            'socket_connect_hits': {str(port): 1 if open_ok else 0},
            'http_successes': successes,
            'http_probe_results': http_results,
            'listener_snapshots': [{'elapsed_sec': 0, 'text': listener.get('text'), 'ports': listener.get('ports')}],
            'runtime_diagnostics': {'self_test_port': port, 'self_test_socket_open': open_ok},
        }
        report_path, _summary_path = write_outputs(summary, 'op25_http_runtime_selftest')
        report_text = report_path.read_text(encoding='utf-8')
        return 0 if 'FINAL: PASS' in report_text else 1
    finally:
        server.shutdown()
        server.server_close()


def run_live(args: argparse.Namespace) -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    status_samples: list[dict[str, Any]] = []
    status_errors: list[str] = []
    listener_snapshots: list[dict[str, Any]] = []
    socket_hits: Counter[str] = Counter()
    listener_hits: Counter[str] = Counter()
    http_probe_results: list[dict[str, Any]] = []

    initial_status, initial_error = get_json_detailed(f'{args.backend_url}/api/status', timeout=args.status_timeout)
    if initial_status is not None:
        status_samples.append(initial_status)
    else:
        status_errors.append(f'initial /api/status: {initial_error}')

    start_response: dict[str, Any] | None = None
    start_error = ''
    started_by_probe = False
    if initial_status is not None and not status_running(initial_status) and not args.no_start:
        start_response, start_error = post_json_detailed(f'{args.backend_url}/api/scanner/start', timeout=4.0)
        if start_response is not None:
            status_samples.append(start_response)
            started_by_probe = True
        else:
            status_errors.append(f'POST /api/scanner/start: {start_error}')
    elif initial_status is not None:
        start_response = None

    marker_text = read_marker_text()
    start_ports = ports_from_obj(start_response)
    status_ports = ports_from_obj(status_samples)
    marker_ports = ports_from_text(marker_text)
    cli_ports = parse_cli_ports(args.ports)
    target_ports = unique_ports([start_ports, status_ports, marker_ports], cli_ports)
    paths = [path.strip() for path in args.paths.split(',') if path.strip()]

    elapsed = 0
    end_time = time.time() + args.seconds
    while time.time() < end_time:
        status, error = get_json_detailed(f'{args.backend_url}/api/status', timeout=args.status_timeout)
        if status is not None:
            status_samples.append(status)
        elif error:
            status_errors.append(f't+{elapsed}s /api/status: {error}')
        listener = listener_snapshot()
        listener_snapshots.append({'elapsed_sec': elapsed, 'text': listener.get('text'), 'ports': listener.get('ports')})
        listener_ports = set(int(port) for port in listener.get('ports') or [])
        for port in target_ports:
            if int(port) in listener_ports:
                listener_hits[str(port)] += 1
            ok, _connect_error = socket_connects(int(port), timeout=0.25)
            if ok:
                socket_hits[str(port)] += 1
        http_probe_results.extend(probe_http_ports(target_ports, paths, timeout=args.http_timeout))
        time.sleep(args.interval)
        elapsed = int(round(args.seconds - max(0.0, end_time - time.time())))

    final_status, final_error = get_json_detailed(f'{args.backend_url}/api/status', timeout=args.status_timeout)
    if final_status is not None:
        status_samples.append(final_status)
    elif final_error:
        status_errors.append(f'final /api/status: {final_error}')

    stop_response = None
    stop_error = ''
    if started_by_probe and not args.no_stop:
        stop_response, stop_error = post_json_detailed(f'{args.backend_url}/api/scanner/stop', timeout=4.0)
        if stop_response is not None:
            status_samples.append(stop_response)
        else:
            status_errors.append(f'POST /api/scanner/stop: {stop_error}')

    status_ports = ports_from_obj(status_samples)
    target_ports = unique_ports([start_ports, status_ports, marker_ports], cli_ports)
    successes = [result for result in http_probe_results if int(result.get('status') or 0) > 0]
    summary = {
        'backend_url': args.backend_url,
        'initial_status_captured': initial_status is not None,
        'start': {
            'requested': initial_status is not None and not status_running(initial_status) and not args.no_start,
            'response_seen': start_response is not None,
            'scanner_state': state_name(start_response),
            'error': start_error,
        },
        'stop': {
            'requested': started_by_probe and not args.no_stop,
            'response_seen': stop_response is not None,
            'scanner_state': state_name(stop_response),
            'error': stop_error,
        },
        'target_ports': target_ports,
        'port_sources': {
            'start_response': start_ports,
            'status_samples': status_ports,
            'validated_marker': marker_ports,
            'cli_ports': cli_ports,
        },
        'status_summary': summarize_status(status_samples),
        'status_errors': status_errors,
        'listener_hits': dict(listener_hits),
        'socket_connect_hits': dict(socket_hits),
        'http_successes': successes,
        'http_probe_results': http_probe_results,
        'listener_snapshots': listener_snapshots,
        'runtime_diagnostics': {
            'seconds': args.seconds,
            'interval': args.interval,
            'status_timeout': args.status_timeout,
            'http_timeout': args.http_timeout,
            'samples_collected': len(status_samples),
            'status_error_count': len(status_errors),
            'started_by_probe': started_by_probe,
            'final_status_captured': final_status is not None,
        },
    }
    report_path, _summary_path = write_outputs(summary, 'op25_http_runtime_probe')
    return 0 if 'FINAL: PASS' in report_path.read_text(encoding='utf-8') else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Short OP25 HTTP runtime listener and backend status diagnostic')
    parser.add_argument('--backend-url', default=DEFAULT_BACKEND_URL)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--interval', type=int, default=1)
    parser.add_argument('--status-timeout', type=float, default=1.0)
    parser.add_argument('--http-timeout', type=float, default=0.5)
    parser.add_argument('--ports', default='')
    parser.add_argument('--paths', default=','.join(DEFAULT_PATHS))
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--no-stop', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if not args.yes:
        print('FAIL: live OP25 HTTP runtime probe requires --yes')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1
    if args.seconds <= 0 or args.interval <= 0:
        print('FAIL: --seconds and --interval must be positive integers')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1
    return run_live(args)


if __name__ == '__main__':
    raise SystemExit(main())
PY_HTTP_RUNTIME

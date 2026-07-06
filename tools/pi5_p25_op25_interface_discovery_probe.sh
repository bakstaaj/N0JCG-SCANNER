#!/usr/bin/env bash
set -u

python3 - "$@" <<'PY_DISCOVERY'
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

REPORT_DIR = Path('.p25_op25_interface_discovery_reports')
DEFAULT_BACKEND_URL = os.environ.get('P25_SCANNER_BACKEND_URL', 'http://127.0.0.1:8070')
DEFAULT_PORTS = [8080, 8081, 8082, 8000, 8888, 9000, 5000]
HTTP_PATHS = ['/', '/status', '/status.json', '/api/status', '/trunk', '/trunk.tsv', '/metadata', '/metadata.json', '/json']
SOURCE_TOKENS = [
    'http',
    'terminal',
    'json',
    'websocket',
    'tgid',
    'talkgroup',
    'frequency',
    'voice',
    'grant',
    'trunk',
    'metadata',
    'plot',
    'zmq',
]
FAILURE_STATES = {'decoder_command_invalid', 'decoder_start_failed', 'decoder_missing', 'config_error'}


def utc_stamp() -> str:
    return time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())


def http_request(method: str, url: str, timeout: float = 2.0) -> tuple[int, str, str]:
    request = urllib.request.Request(url=url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(5120).decode('utf-8', errors='replace')
            content_type = response.headers.get('content-type', '')
            return int(response.status), content_type, body
    except urllib.error.HTTPError as exc:
        body = exc.read(2048).decode('utf-8', errors='replace')
        return int(exc.code), exc.headers.get('content-type', ''), body
    except Exception as exc:
        return 0, '', f'{type(exc).__name__}: {exc}'


def get_json(url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    status, _content_type, body = http_request('GET', url, timeout=timeout)
    if status <= 0:
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def post_json(url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    status, _content_type, body = http_request('POST', url, timeout=timeout)
    if status <= 0:
        return None
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def run_command(args: list[str], timeout: float = 3.0) -> str:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        return f'{type(exc).__name__}: {exc}'
    return (result.stdout + result.stderr).strip()


def parse_ports(text: str) -> list[int]:
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
    process = status.get('decoder_process')
    if isinstance(process, dict):
        return boolish(process.get('running')) is True
    return False


def command_paths_from_status(status: dict[str, Any] | None) -> list[Path]:
    paths: list[Path] = []
    if not isinstance(status, dict):
        return paths
    process = status.get('decoder_process')
    if not isinstance(process, dict):
        return paths
    command = process.get('command')
    if isinstance(command, list):
        for item in command:
            if not isinstance(item, str):
                continue
            if item.endswith('.py') or '/op25/' in item.replace('\\', '/'):
                path = Path(item).expanduser()
                paths.append(path)
    marker = process.get('validated_marker')
    if isinstance(marker, dict):
        for value in marker.values():
            if isinstance(value, str) and ('rx.py' in value or 'multi_rx.py' in value or '/op25/' in value):
                for token in value.replace('"', ' ').replace("'", ' ').split():
                    if token.endswith('.py') or '/op25/' in token.replace('\\', '/'):
                        paths.append(Path(token).expanduser())
    return paths


def op25_candidate_dirs(status: dict[str, Any] | None) -> list[Path]:
    dirs: list[Path] = []
    for path in command_paths_from_status(status):
        if path.name in {'rx.py', 'multi_rx.py', 'terminal.py'}:
            dirs.append(path.parent)
        elif path.is_dir():
            dirs.append(path)
    home = Path.home()
    dirs.extend(
        [
            home / 'op25' / 'op25' / 'gr-op25_repeater' / 'apps',
            Path('/usr/src/op25/op25/gr-op25_repeater/apps'),
            Path('/opt/op25/op25/gr-op25_repeater/apps'),
            Path('/usr/local/share/op25/op25/gr-op25_repeater/apps'),
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        normalized = str(path.expanduser()).replace('\\', '/')
        if normalized not in seen:
            seen.add(normalized)
            unique.append(path.expanduser())
    return unique


def scan_source_paths(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    for base in paths:
        if not base.exists():
            continue
        candidates: list[Path]
        if base.is_file():
            candidates = [base]
        else:
            candidates = sorted(base.glob('*.py'))
        for file_path in candidates:
            normalized = str(file_path.resolve()) if file_path.exists() else str(file_path)
            if normalized in seen_files:
                continue
            seen_files.add(normalized)
            try:
                text = file_path.read_text(encoding='utf-8', errors='replace')
            except OSError as exc:
                files.append({'path': str(file_path), 'readable': False, 'error': str(exc)})
                continue
            lower = text.lower()
            file_tokens = [token for token in SOURCE_TOKENS if token in lower]
            files.append({'path': str(file_path), 'readable': True, 'tokens': file_tokens})
            for line_no, line in enumerate(text.splitlines(), start=1):
                line_lower = line.lower()
                matched = [token for token in SOURCE_TOKENS if token in line_lower]
                if matched:
                    hits.append(
                        {
                            'path': str(file_path),
                            'line': line_no,
                            'tokens': matched,
                            'sample': line.strip()[:220],
                        }
                    )
                    if len(hits) >= 120:
                        return files, hits
    return files, hits


def probe_listeners() -> str:
    ss = shutil.which('ss')
    if ss:
        return run_command([ss, '-ltnp'], timeout=3.0)
    netstat = shutil.which('netstat')
    if netstat:
        return run_command([netstat, '-ltnp'], timeout=3.0)
    return 'no ss or netstat command available'


def probe_http_interfaces(ports: list[int]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for port in ports:
        for path in HTTP_PATHS:
            url = f'http://127.0.0.1:{port}{path}'
            status, content_type, body = http_request('GET', url, timeout=0.75)
            if status > 0:
                results.append(
                    {
                        'url': url,
                        'status': status,
                        'content_type': content_type,
                        'sample': body.replace('\r', ' ').replace('\n', ' ')[:240],
                    }
                )
    return results


def collect_status_samples(backend_url: str, seconds: int, interval: int) -> tuple[list[dict[str, Any]], bool]:
    samples: list[dict[str, Any]] = []
    end_time = time.time() + seconds
    while time.time() < end_time:
        status = get_json(f'{backend_url}/api/status', timeout=2.0)
        if status is not None:
            samples.append(status)
        time.sleep(interval)
    final = get_json(f'{backend_url}/api/status', timeout=2.0)
    if final is not None:
        samples.append(final)
    return samples, any(status_running(sample) for sample in samples)


def summarize_status(samples: list[dict[str, Any]]) -> dict[str, Any]:
    states: Counter[str] = Counter()
    running = 0
    warnings: set[str] = set()
    control_freqs: Counter[str] = Counter()
    voice_freqs: Counter[str] = Counter()
    active_tgids: Counter[str] = Counter()
    decoder_commands: list[Any] = []
    for sample in samples:
        state = str(sample.get('scanner_state') or 'unknown')
        states[state] += 1
        if status_running(sample):
            running += 1
        for warning in sample.get('warnings') or []:
            warnings.add(str(warning))
        control = sample.get('active_control_frequency_hz')
        if control:
            control_freqs[str(control)] += 1
        voice = sample.get('active_voice_frequency_hz')
        if voice:
            voice_freqs[str(voice)] += 1
        tgid = sample.get('active_tgid')
        if tgid:
            active_tgids[str(tgid)] += 1
        process = sample.get('decoder_process')
        if isinstance(process, dict) and process.get('command') and process.get('command') not in decoder_commands:
            decoder_commands.append(process.get('command'))
    return {
        'snapshot_count': len(samples),
        'running_snapshots': running,
        'states': dict(states),
        'warnings': sorted(warnings),
        'control_frequencies': dict(control_freqs),
        'voice_frequencies': dict(voice_freqs),
        'active_tgids': dict(active_tgids),
        'decoder_commands': decoder_commands,
    }


def make_report(summary: dict[str, Any]) -> tuple[str, int, int, int]:
    passes = 0
    warns = 0
    fails = 0
    lines: list[str] = []
    lines.append('# PI-P25-SCANNER OP25 Interface Discovery')
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
    if status_summary.get('snapshot_count', 0) > 0:
        add('PASS', f"backend status snapshots captured: {status_summary.get('snapshot_count')}")
    else:
        add('FAIL', 'backend status snapshots were not captured')
    backend_probe = summary.get('backend_probe') or {}
    if status_summary.get('running_snapshots', 0) > 0:
        add('PASS', f"decoder running snapshots observed: {status_summary.get('running_snapshots')}")
    elif backend_probe.get('long_collection_skipped'):
        add('FAIL', 'fail-fast preflight did not observe decoder running; long collection skipped')
    else:
        add('WARN', 'decoder running snapshots were not observed')
    if status_summary.get('control_frequencies'):
        add('PASS', f"status control frequency fields observed: {status_summary.get('control_frequencies')}")
    else:
        add('WARN', 'status control frequency field not observed')
    if status_summary.get('voice_frequencies'):
        add('PASS', f"status voice frequency fields observed: {status_summary.get('voice_frequencies')}")
    else:
        add('WARN', 'status voice frequency field not observed')
    if status_summary.get('active_tgids'):
        add('PASS', f"status active TGID fields observed: {status_summary.get('active_tgids')}")
    else:
        add('WARN', 'status active TGID field not observed')
    source_files = summary.get('source_files') or []
    source_hits = summary.get('source_hits') or []
    if source_files:
        add('PASS', f'OP25 app/source files inspected: {len(source_files)}')
    else:
        add('WARN', 'no OP25 app/source files were found for inspection')
    if source_hits:
        add('PASS', f'OP25 source metadata/interface token hits found: {len(source_hits)}')
    else:
        add('WARN', 'no OP25 source metadata/interface token hits found')
    http_results = summary.get('http_results') or []
    if http_results:
        add('PASS', f'HTTP/interface endpoint responses observed: {len(http_results)}')
    else:
        add('WARN', 'no OP25 HTTP/interface endpoint responses observed on probed ports')
    lines.append('')
    lines.append('## Backend Status Summary')
    lines.append('```json')
    lines.append(json.dumps(status_summary, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('## Backend Probe Diagnostics')
    lines.append('```json')
    lines.append(json.dumps(summary.get('backend_probe') or {}, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('## Listening TCP Sockets')
    lines.append('```text')
    listener_text = str(summary.get('listeners') or 'none')
    lines.extend(listener_text.splitlines()[:80] or ['none'])
    lines.append('```')
    lines.append('')
    lines.append('## HTTP / Interface Probe Results')
    if http_results:
        for result in http_results[:50]:
            lines.append(f"- {result.get('status')} {result.get('url')} {result.get('content_type')}: {result.get('sample')}")
    else:
        lines.append('- none')
    lines.append('')
    lines.append('## OP25 Source Files Inspected')
    if source_files:
        for file_info in source_files[:80]:
            tokens = ', '.join(file_info.get('tokens') or [])
            readable = 'yes' if file_info.get('readable') else 'no'
            lines.append(f"- {file_info.get('path')} readable={readable} tokens={tokens or '-'}")
    else:
        lines.append('- none')
    lines.append('')
    lines.append('## OP25 Metadata/Interface Source Hits')
    if source_hits:
        lines.append('```text')
        for hit in source_hits[:80]:
            tokens = ','.join(hit.get('tokens') or [])
            lines.append(f"{hit.get('path')}:{hit.get('line')} [{tokens}] {hit.get('sample')}")
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


def run_self_test(keep: bool) -> int:
    temp_root = Path(tempfile.mkdtemp(prefix='pi-p25-interface-selftest-'))
    try:
        app_dir = temp_root / 'op25' / 'op25' / 'gr-op25_repeater' / 'apps'
        app_dir.mkdir(parents=True)
        (app_dir / 'rx.py').write_text(
            "import json\n"
            "def terminal_http_status():\n"
            "    return json.dumps({'tgid': 3105, 'frequency': 853275000, 'voice': True, 'grant': True})\n",
            encoding='utf-8',
            newline='\n',
        )
        files, hits = scan_source_paths([app_dir])
        status_samples = [
            {
                'scanner_state': 'running',
                'decoder_process': {'running': True, 'command': [str(app_dir / 'rx.py')]},
                'active_control_frequency_hz': 852750000,
                'warnings': [],
            }
        ]
        status_summary = summarize_status(status_samples)
        summary = {
            'status_summary': status_summary,
            'backend_probe': {'long_collection_skipped': False},
            'listeners': 'LISTEN 0 4096 127.0.0.1:8080',
            'http_results': [{'url': 'http://127.0.0.1:8080/status', 'status': 200, 'content_type': 'application/json', 'sample': '{}'}],
            'source_files': files,
            'source_hits': hits,
        }
        report, _passes, _warns, fails = make_report(summary)
        report_path = REPORT_DIR / f'interface_discovery_selftest_{utc_stamp()}.md'
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding='utf-8', newline='\n')
        required = [
            files,
            hits,
            status_summary.get('running_snapshots') == 1,
            'FINAL: PASS' in report,
            report_path.exists(),
        ]
        if fails != 0 or not all(required):
            print(report)
            print('SUMMARY: PASS=0 WARN=0 FAIL=1')
            print('FINAL: FAIL')
            return 1
        print('PASS: OP25 interface discovery self-test fixture passed')
        print(f'PASS: self-test report path: {report_path}')
        print('SUMMARY: PASS=2 WARN=0 FAIL=0')
        print('FINAL: PASS')
        return 0
    finally:
        if keep:
            print(f'WARN: keeping self-test directory: {temp_root}')
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description='Discover OP25 metadata/interface sources and localhost endpoints')
    parser.add_argument('--backend-url', default=DEFAULT_BACKEND_URL)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--interval', type=int, default=2)
    parser.add_argument('--preflight-seconds', type=int, default=20)
    parser.add_argument('--preflight-interval', type=int, default=1)
    parser.add_argument('--force-collect', action='store_true')
    parser.add_argument('--ports', default=','.join(str(port) for port in DEFAULT_PORTS))
    parser.add_argument('--no-start', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--keep-self-test', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        return run_self_test(args.keep_self_test)
    if not args.yes:
        print('FAIL: live interface discovery requires --yes')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1
    if args.seconds <= 0 or args.interval <= 0:
        print('FAIL: --seconds and --interval must be positive integers')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1
    if args.preflight_seconds <= 0 or args.preflight_interval <= 0:
        print('FAIL: --preflight-seconds and --preflight-interval must be positive integers')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    started_by_probe = False
    start_response: dict[str, Any] | None = None
    stop_response: dict[str, Any] | None = None
    initial_status = get_json(f'{args.backend_url}/api/status', timeout=2.0)
    if initial_status is None:
        print(f'FAIL: backend not reachable: {args.backend_url}')
        print('SUMMARY: PASS=0 WARN=0 FAIL=1')
        print('FINAL: FAIL')
        return 1

    status_samples: list[dict[str, Any]] = [initial_status]
    if not status_running(initial_status) and not args.no_start:
        start_response = post_json(f'{args.backend_url}/api/scanner/start', timeout=4.0)
        if start_response is not None:
            started_by_probe = True
            time.sleep(2)

    preflight_samples: list[dict[str, Any]] = []
    preflight_running_seen = status_running(initial_status)
    collection_skipped = False
    if not preflight_running_seen:
        preflight_samples, preflight_running_seen = collect_status_samples(
            args.backend_url, args.preflight_seconds, args.preflight_interval
        )
        status_samples.extend(preflight_samples)

    if not preflight_running_seen and not args.force_collect:
        collection_skipped = True
        final_status = get_json(f'{args.backend_url}/api/status', timeout=2.0)
        if final_status is not None:
            status_samples.append(final_status)
    else:
        collected_samples, _running_seen = collect_status_samples(args.backend_url, args.seconds, args.interval)
        status_samples.extend(collected_samples)
        final_status = get_json(f'{args.backend_url}/api/status', timeout=2.0)
        if final_status is not None:
            status_samples.append(final_status)
    if started_by_probe:
        stop_response = post_json(f'{args.backend_url}/api/scanner/stop', timeout=4.0)
        stopped_status = get_json(f'{args.backend_url}/api/status', timeout=2.0)
        if stopped_status is not None:
            status_samples.append(stopped_status)

    best_status = final_status or (status_samples[-1] if status_samples else initial_status)
    source_dirs = op25_candidate_dirs(best_status)
    source_files, source_hits = scan_source_paths(source_dirs)
    ports = parse_ports(args.ports)
    summary = {
        'status_summary': summarize_status(status_samples),
        'backend_probe': {
            'backend_url': args.backend_url,
            'initial_status_captured': initial_status is not None,
            'start_requested': not args.no_start and not status_running(initial_status),
            'start_response_seen': start_response is not None,
            'stop_response_seen': stop_response is not None,
            'status_samples_total': len(status_samples),
            'preflight_seconds': args.preflight_seconds,
            'preflight_interval': args.preflight_interval,
            'preflight_samples': len(preflight_samples),
            'preflight_running_seen': preflight_running_seen,
            'long_collection_skipped': collection_skipped,
            'long_collection_seconds': 0 if collection_skipped else args.seconds,
            'force_collect': args.force_collect,
        },
        'op25_candidate_dirs': [str(path) for path in source_dirs],
        'listeners': probe_listeners(),
        'http_results': probe_http_interfaces(ports),
        'source_files': source_files,
        'source_hits': source_hits,
    }
    report, passes, warns, fails = make_report(summary)
    report_path = REPORT_DIR / f'interface_discovery_report_{stamp}.md'
    summary_path = REPORT_DIR / f'interface_discovery_summary_{stamp}.json'
    snapshot_path = REPORT_DIR / f'interface_discovery_status_snapshots_{stamp}.jsonl'
    report_path.write_text(report, encoding='utf-8', newline='\n')
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8', newline='\n')
    with snapshot_path.open('w', encoding='utf-8', newline='\n') as handle:
        for sample in status_samples:
            handle.write(json.dumps(sample, sort_keys=True, separators=(',', ':')) + '\n')
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(report, end='')
    print(f'Report: {report_path}')
    print(f'Summary JSON: {summary_path}')
    print(f'Snapshot JSONL: {snapshot_path}')
    print(f'SUMMARY: PASS={passes} WARN={warns} FAIL={fails}')
    print('FINAL: PASS' if fails == 0 else 'FINAL: FAIL')
    return 0 if fails == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
PY_DISCOVERY

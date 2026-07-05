#!/usr/bin/env bash
# Normalize tracked/project text files to LF/no trailing spaces and refresh the Git index.
# Run from the PI-P25-SCANNER repository root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_lf_policy_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/normalize_text_policy_${STAMP}.txt"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

mkdir -p "$REPORT_DIR"
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER text policy normalizer ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -f ".gitattributes" && -d "tools" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root after .gitattributes exists"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if git config core.autocrlf false && git config core.eol lf && git config core.safecrlf warn; then
  pass "repo-local Git line-ending policy set"
else
  fail "failed to set repo-local Git line-ending policy"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

python3 - <<'PY'
from __future__ import annotations
import subprocess
from pathlib import Path

text_exts = {
    '.sh', '.py', '.md', '.json', '.js', '.css', '.html', '.txt', '.yml', '.yaml',
    '.service', '.tsv', '.csv', '.gitignore', '.gitattributes'
}
explicit_names = {'.gitignore', '.gitattributes'}

tracked = subprocess.check_output(['git', 'ls-files'], text=True, encoding='utf-8', errors='replace').splitlines()
changed = 0
for name in tracked:
    path = Path(name)
    if not path.is_file():
        continue
    if path.name not in explicit_names and path.suffix.lower() not in text_exts:
        continue
    data = path.read_bytes()
    if b'\0' in data:
        continue
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        continue
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    normalized = '\n'.join(line.rstrip(' \t') for line in normalized.split('\n'))
    if normalized and not normalized.endswith('\n'):
        normalized += '\n'
    if normalized != text:
        path.write_text(normalized, encoding='utf-8')
        changed += 1
print(f'NORMALIZED_TRACKED_TEXT_FILES={changed}')
PY
pass "tracked text files normalized"

if git add --renormalize .; then
  pass "Git index renormalized from .gitattributes"
else
  fail "git add --renormalize failed"
fi

if git --no-pager diff --check -- . ':!runtime' ':!.p25_*_reports' ':!.p25_*_backups' > "$REPORT_DIR/worktree_whitespace_${STAMP}.txt" 2>&1; then
  pass "working tree whitespace check passed"
else
  fail "working tree whitespace check failed; see $REPORT_DIR/worktree_whitespace_${STAMP}.txt"
fi

if git --no-pager diff --cached --check -- . ':!runtime' ':!.p25_*_reports' ':!.p25_*_backups' > "$REPORT_DIR/staged_whitespace_${STAMP}.txt" 2>&1; then
  pass "staged whitespace check passed"
else
  fail "staged whitespace check failed; see $REPORT_DIR/staged_whitespace_${STAMP}.txt"
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1

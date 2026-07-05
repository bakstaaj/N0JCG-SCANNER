#!/usr/bin/env bash
# Validate the PI-P25-SCANNER repository from the repo root.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

printf '=== PI-P25-SCANNER repo validation ===\n'

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "web" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run this script from the PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n'
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  pass "python3 available"
else
  fail "python3 missing"
fi

if command -v git >/dev/null 2>&1; then
  pass "git available"
else
  fail "git missing"
fi

for required in README.md DEV_GUARDRAILS.md docs/ARCHITECTURE.md docs/MILESTONES.md config/p25_systems.example.json web/index.html web/app.css web/app.js src/pi_p25_scanner/backend.py; do
  if [[ -f "$required" ]]; then
    pass "required file exists: $required"
  else
    fail "missing required file: $required"
  fi
done

for script in tools/*.sh; do
  if [[ -f "$script" ]]; then
    if bash -n "$script"; then
      pass "bash syntax valid: $script"
    else
      fail "bash syntax invalid: $script"
    fi
  fi
done

if command -v python3 >/dev/null 2>&1; then
  while IFS= read -r pyfile; do
    [[ -z "$pyfile" ]] && continue
    if python3 -m py_compile "$pyfile"; then
      pass "python compile valid: $pyfile"
    else
      fail "python compile failed: $pyfile"
    fi
  done < <(find src -type f -name '*.py' -print | sort)

  while IFS= read -r jsonfile; do
    [[ -z "$jsonfile" ]] && continue
    if python3 -m json.tool "$jsonfile" >/dev/null; then
      pass "json valid: $jsonfile"
    else
      fail "json invalid: $jsonfile"
    fi
  done < <(find config -type f -name '*.json' -print | sort)
fi

if command -v node >/dev/null 2>&1; then
  if node --check web/app.js; then
    pass "node syntax valid: web/app.js"
  else
    fail "node syntax invalid: web/app.js"
  fi
else
  warn "node not installed; skipped web/app.js syntax validation"
fi

if command -v git >/dev/null 2>&1; then
  if git diff --check -- . ':!runtime' ':!.p25_*_reports' ':!.p25_*_backups'; then
    pass "working tree whitespace check passed"
  else
    fail "working tree whitespace check failed"
  fi
fi

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n'
  exit 0
fi
printf 'FINAL: FAIL\n'
exit 1

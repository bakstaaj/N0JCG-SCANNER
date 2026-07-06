#!/usr/bin/env bash
# Diagnostic-only encrypted/flag-gated audio runner.
# Use this only when intentionally testing encrypted-burst suppression. The normal
# clear-audio baseline is tools/msys2_run_pi_browser_audio_clear_test.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x ./tools/msys2_run_pi_browser_audio_live_test.sh ]]; then
  echo "FAIL: missing executable ./tools/msys2_run_pi_browser_audio_live_test.sh" >&2
  exit 1
fi

exec ./tools/msys2_run_pi_browser_audio_live_test.sh "$@"

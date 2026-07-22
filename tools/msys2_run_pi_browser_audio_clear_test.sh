#!/usr/bin/env bash
# Clear-audio baseline runner.
# This intentionally delegates to the V0.3M raw bypass path because hardware testing
# showed that the project-side encrypted/flag gates over-filtered clear traffic.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x ./tools/msys2_run_pi_browser_audio_bypass_test.sh ]]; then
  echo "FAIL: missing executable ./tools/msys2_run_pi_browser_audio_bypass_test.sh" >&2
  echo "Run the V0.3M raw browser-audio bypass patch first." >&2
  exit 1
fi

exec ./tools/msys2_run_pi_browser_audio_bypass_test.sh "$@"

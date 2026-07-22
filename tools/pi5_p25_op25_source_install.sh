#!/usr/bin/env bash
# Guarded OP25 source installer/helper for PI-P25-SCANNER.
# Default mode is dry-run. Full upstream install requires --run-upstream-install --yes.

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0
REPORT_DIR=".p25_op25_source_install_reports"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_FILE="$REPORT_DIR/op25_source_install_${STAMP}.txt"
MODE="dry-run"
YES="false"
SOURCE_DIR="${OP25_SOURCE_DIR:-$HOME/op25}"
REPO_URL="${OP25_REPO_URL:-https://github.com/boatbod/op25.git}"
BRANCH="${OP25_REPO_BRANCH:-master}"

pass() { printf 'PASS: %s\n' "$*" | tee -a "$REPORT_FILE"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*" | tee -a "$REPORT_FILE"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT_FILE"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }
usage() {
  cat <<'EOF'
Usage: ./tools/pi5_p25_op25_source_install.sh [mode] [options]

Modes:
  --dry-run                 Show planned actions only. Default.
  --clone-only              Clone or inspect OP25 source only. Requires --yes.
  --run-upstream-install    Run OP25 upstream ./install.sh -f. Requires --yes.

Options:
  --yes                     Required for any mode that changes the Pi.
  --source-dir PATH         OP25 source directory. Default: $HOME/op25.
  --repo-url URL            Git repository URL. Default: boatbod/op25.
  --branch NAME             Git branch. Default: master.
  -h, --help                Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --clone-only) MODE="clone-only"; shift ;;
    --run-upstream-install) MODE="run-upstream-install"; shift ;;
    --yes) YES="true"; shift ;;
    --source-dir) SOURCE_DIR="${2:-}"; shift 2 ;;
    --repo-url) REPO_URL="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1"; usage; exit 1 ;;
  esac
done

mkdir -p "$REPORT_DIR" runtime/settings
: > "$REPORT_FILE"
printf '=== PI-P25-SCANNER guarded OP25 source install helper ===\n' | tee -a "$REPORT_FILE"

if [[ -f "DEV_GUARDRAILS.md" && -d "tools" && -d "src/pi_p25_scanner" ]]; then
  pass "running from repository root"
else
  fail "run from PI-P25-SCANNER repository root"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]]; then
  pass "Linux host detected"
else
  fail "this helper is intended for the Raspberry Pi Linux runtime"
fi

for cmd in git python3; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    fail "missing required command: $cmd"
  fi
done

for cmd in sudo apt-get make cmake; do
  if command -v "$cmd" >/dev/null 2>&1; then
    pass "command available: $cmd"
  else
    warn "command not currently available: $cmd"
  fi
done

printf 'Mode: %s\n' "$MODE" | tee -a "$REPORT_FILE"
printf 'Source dir: %s\n' "$SOURCE_DIR" | tee -a "$REPORT_FILE"
printf 'Repo URL: %s\n' "$REPO_URL" | tee -a "$REPORT_FILE"
printf 'Branch: %s\n' "$BRANCH" | tee -a "$REPORT_FILE"

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
  printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
  exit 1
fi

if [[ "$MODE" != "dry-run" && "$YES" != "true" ]]; then
  fail "mode $MODE changes the Pi and requires --yes"
fi

if [[ "$MODE" == "dry-run" ]]; then
  pass "dry-run selected; no source clone, apt install, build, or OP25 launch will run"
  {
    printf '\nPlanned clone-only command:\n'
    printf './tools/pi5_p25_op25_source_install.sh --clone-only --yes --source-dir %q --repo-url %q --branch %q\n' "$SOURCE_DIR" "$REPO_URL" "$BRANCH"
    printf '\nFull upstream install/build remains gated behind:\n'
    printf './tools/pi5_p25_op25_source_install.sh --run-upstream-install --yes --source-dir %q\n' "$SOURCE_DIR"
  } | tee -a "$REPORT_FILE"
elif [[ "$MODE" == "clone-only" ]]; then
  parent="$(dirname "$SOURCE_DIR")"
  mkdir -p "$parent"
  if [[ -d "$SOURCE_DIR/.git" ]]; then
    pass "existing OP25 git source directory found: $SOURCE_DIR"
    if git -C "$SOURCE_DIR" remote get-url origin >> "$REPORT_FILE" 2>&1; then
      pass "existing OP25 origin recorded"
    else
      warn "could not read existing OP25 origin"
    fi
    if git -C "$SOURCE_DIR" rev-parse --short HEAD >> "$REPORT_FILE" 2>&1; then
      pass "existing OP25 commit recorded"
    else
      warn "could not read existing OP25 commit"
    fi
  elif [[ -e "$SOURCE_DIR" ]]; then
    fail "source path exists but is not a git repo: $SOURCE_DIR"
  else
    if git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$SOURCE_DIR" >> "$REPORT_FILE" 2>&1; then
      pass "cloned OP25 source to $SOURCE_DIR"
    else
      fail "OP25 source clone failed; see $REPORT_FILE"
    fi
  fi
elif [[ "$MODE" == "run-upstream-install" ]]; then
  if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    fail "OP25 source directory missing; run --clone-only --yes first: $SOURCE_DIR"
  elif [[ ! -x "$SOURCE_DIR/install.sh" && ! -f "$SOURCE_DIR/install.sh" ]]; then
    fail "OP25 install.sh missing: $SOURCE_DIR/install.sh"
  else
    warn "running upstream OP25 install/build; this may install packages, build code, and require sudo"
    if (cd "$SOURCE_DIR" && sh ./install.sh -f) >> "$REPORT_FILE" 2>&1; then
      pass "upstream OP25 install.sh completed"
    else
      fail "upstream OP25 install.sh failed; see $REPORT_FILE"
    fi
  fi
else
  fail "unsupported mode: $MODE"
fi

if [[ -d "$SOURCE_DIR" ]]; then
  for rel in install.sh op25/gr-op25_repeater/apps/rx.py op25/gr-op25_repeater/apps/multi_rx.py; do
    if [[ -f "$SOURCE_DIR/$rel" ]]; then
      pass "OP25 source file exists: $rel"
    else
      warn "OP25 source file missing: $rel"
    fi
  done
fi

cat > runtime/settings/op25_source_path.env <<EOF
OP25_SOURCE_DIR=$SOURCE_DIR
OP25_REPO_URL=$REPO_URL
OP25_REPO_BRANCH=$BRANCH
OP25_SOURCE_HELPER_MODE=$MODE
EOF
pass "wrote source path marker: runtime/settings/op25_source_path.env"

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT" | tee -a "$REPORT_FILE"
printf 'Report: %s\n' "$REPORT_FILE" | tee -a "$REPORT_FILE"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n' | tee -a "$REPORT_FILE"
  exit 0
fi
printf 'FINAL: FAIL\n' | tee -a "$REPORT_FILE"
exit 1

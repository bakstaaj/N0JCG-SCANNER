#!/usr/bin/env bash
# Validate that repo shell scripts do not contain pager-prone Git output commands.
#
# Allowed:
#   - git --no-pager diff --check ...        (PASS/FAIL whitespace validation)
#   - GIT_PAGER=cat git diff --check ...
#   - git diff --name-only --exit-code ...   (PASS/FAIL cleanliness validation)
#
# Disallowed:
#   - raw git diff/log/show intended for visual inspection
#   - any command that may open a pager and stop at a ':' prompt

set -Eeuo pipefail

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

pass() { printf 'PASS: %s\n' "$*"; PASS_COUNT=$((PASS_COUNT + 1)); return 0; }
warn() { printf 'WARN: %s\n' "$*"; WARN_COUNT=$((WARN_COUNT + 1)); return 0; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL_COUNT=$((FAIL_COUNT + 1)); return 0; }

printf '=== no-blocking Git output validation ===\n'

if [[ -f DEV_GUARDRAILS.md && -d tools ]]; then
  pass "running from repository root"
else
  fail "run this script from the scanner repository root"
  printf 'FINAL: FAIL\n'
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  fail "git missing"
  printf 'FINAL: FAIL\n'
  exit 1
fi

mapfile -t SCRIPT_FILES < <(git ls-files 'tools/*.sh' | sort)
if [[ "${#SCRIPT_FILES[@]}" -gt 0 ]]; then
  pass "tracked shell scripts found: ${#SCRIPT_FILES[@]}"
else
  warn "no tracked shell scripts found"
fi

is_allowed_git_line() {
  local line="$1"
  # Strip comments-only lines.
  [[ "$line" =~ ^[[:space:]]*# ]] && return 0

  # The validator contains examples and regex snippets; avoid self-matching quoted
  # pattern/example text that is not an executable command.
  [[ "$line" == *"pager-prone Git output commands"* ]] && return 0
  [[ "$line" == *"Allowed:"* ]] && return 0
  [[ "$line" == *"Disallowed:"* ]] && return 0
  [[ "$line" == *"local pattern"* ]] && return 0

  # Safe PASS/FAIL-oriented diff checks.
  if [[ "$line" == *"git --no-pager diff --check"* ]]; then
    return 0
  fi
  if [[ "$line" == *"GIT_PAGER=cat git diff --check"* ]]; then
    return 0
  fi
  if [[ "$line" == *"git --no-pager diff --name-only --exit-code"* ]]; then
    return 0
  fi
  if [[ "$line" == *"GIT_PAGER=cat git diff --name-only --exit-code"* ]]; then
    return 0
  fi

  return 1
}

check_file() {
  local file="$1"
  local lineno=0
  local line=""
  local bad=0

  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))

    # Remove leading whitespace for command-position checks.
    local trimmed="${line#"${line%%[![:space:]]*}"}"

    # Flag executable-looking pager-prone Git commands only. This avoids matching
    # harmless examples, regex strings, and explanatory text.
    if [[ "$trimmed" == git\ diff* || "$trimmed" == git\ log* || "$trimmed" == git\ show* ||
          "$trimmed" == GIT_PAGER=cat\ git\ log* || "$trimmed" == GIT_PAGER=cat\ git\ show* ||
          "$trimmed" == GIT_PAGER=cat\ git\ diff* ]]; then
      if ! is_allowed_git_line "$line"; then
        fail "pager-prone Git command in $file:$lineno"
        bad=$((bad + 1))
      fi
    fi
  done < "$file"

  if [[ "$bad" -eq 0 ]]; then
    pass "no pager-prone Git commands: $file"
  fi
}

for script in "${SCRIPT_FILES[@]}"; do
  check_file "$script"
done

printf 'SUMMARY: PASS=%s WARN=%s FAIL=%s\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"
if [[ "$FAIL_COUNT" -eq 0 ]]; then
  printf 'FINAL: PASS\n'
  exit 0
fi
printf 'FINAL: FAIL\n'
exit 1

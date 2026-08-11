#!/usr/bin/env bash
# Common local .env helpers for scanner MSYS2-to-Pi workflows.
# This file is meant to be sourced by MSYS2 helper scripts.

p25_env_file() {
  printf '%s' "${P25_ENV_FILE:-.env}"
}

p25_load_dotenv() {
  local env_file
  env_file="$(p25_env_file)"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
  fi
}

p25_dotenv_set() {
  local key="$1"
  local value="$2"
  local env_file tmp quoted
  env_file="$(p25_env_file)"
  if ! [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "FAIL: invalid .env key: $key" >&2
    return 2
  fi
  mkdir -p "$(dirname "$env_file")"
  touch "$env_file"
  chmod 600 "$env_file" 2>/dev/null || true
  tmp="${env_file}.tmp.$$"
  printf -v quoted '%q' "$value"
  awk -v key="$key" -v line="$key=$quoted" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!done) {
        print line
        done = 1
      }
      next
    }
    { print }
    END {
      if (!done) {
        print line
      }
    }
  ' "$env_file" > "$tmp"
  mv "$tmp" "$env_file"
  chmod 600 "$env_file" 2>/dev/null || true
}

p25_require_pi_password() {
  local provided_password="${1:-}"
  local pi_user="${2:-pi}"
  local pi_host="${3:-PI-SDR}"
  local prompted=0
  if [[ -n "$provided_password" ]]; then
    PI_PASSWORD="$provided_password"
  elif [[ -n "${PI_PASSWORD:-}" ]]; then
    PI_PASSWORD="$PI_PASSWORD"
  elif [[ -n "${SSHPASS:-}" ]]; then
    PI_PASSWORD="$SSHPASS"
  else
    read -r -s -p "Pi password for ${pi_user}@${pi_host}: " PI_PASSWORD
    echo
    prompted=1
  fi
  if [[ -z "${PI_PASSWORD:-}" ]]; then
    echo "FAIL: empty Pi password" >&2
    return 1
  fi
  export PI_PASSWORD
  if [[ "$prompted" -eq 1 || ! -f "$(p25_env_file)" ]]; then
    p25_dotenv_set PI_USER "$pi_user"
    p25_dotenv_set PI_HOST "$pi_host"
    p25_dotenv_set PI_PASSWORD "$PI_PASSWORD"
    echo "PASS: saved Pi SSH password to $(p25_env_file)"
    echo "WARN: $(p25_env_file) is local plaintext; keep it ignored and do not upload it"
  fi
}

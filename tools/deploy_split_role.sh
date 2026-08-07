#!/usr/bin/env bash
set -Eeuo pipefail

export PATH=/ucrt64/bin:/usr/bin:/bin

usage() {
  printf 'Usage: %s --role roc|radio [--dry-run | --deploy --yes] [--restart]\n' "$0"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

role=""
mode="dry-run"
confirmed=0
restart=0

while (($#)); do
  case "$1" in
    --role) role="${2:-}"; shift 2 ;;
    --dry-run) mode="dry-run"; shift ;;
    --deploy) mode="deploy"; shift ;;
    --yes) confirmed=1; shift ;;
    --restart) restart=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; fail "unknown argument: $1" ;;
  esac
done

[[ "$role" == "roc" || "$role" == "radio" ]] || fail "--role must be roc or radio"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

set -a
[[ -f .env ]] && . ./.env
set +a

if [[ "$role" == "roc" ]]; then
  target_user="${ROC_USER:-pi}"
  target_host="${ROC_HOST:-192.168.68.114}"
  target_repo="${ROC_REPO:-/home/$target_user/sdrdev/N0JCG-ROC}"
  target_password="${ROC_PASSWORD:-}"
  manifest="deploy/roc-files.txt"
else
  target_user="${RADIO_USER:-${PI_USER:-pi}}"
  target_host="${RADIO_HOST:-${PI_HOST:-192.168.68.137}}"
  target_repo="${RADIO_REPO:-${PI_REPO:-/home/pi/PI-SCANNER}}"
  target_password="${RADIO_PASSWORD:-${PI_PASSWORD:-}}"
  manifest="deploy/radio-pi-files.txt"
fi

[[ "$target_user" =~ ^[A-Za-z0-9_-]+$ ]] || fail "unsafe target user"
[[ "$target_host" =~ ^[A-Za-z0-9.:-]+$ ]] || fail "unsafe target host"
[[ "$target_repo" =~ ^/home/[A-Za-z0-9_-]+/[A-Za-z0-9._/-]+$ ]] || fail "target repo must be an explicit path under /home"
[[ -f "$manifest" ]] || fail "missing manifest: $manifest"

mapfile -t entries < <(grep -Ev '^[[:space:]]*(#|$)' "$manifest")
((${#entries[@]} > 0)) || fail "empty manifest: $manifest"
for entry in "${entries[@]}"; do
  [[ "$entry" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "unsafe manifest entry: $entry"
  [[ -e "$entry" ]] || fail "manifest entry does not exist: $entry"
done

printf 'ROLE=%s\nHOST=%s@%s\nREPO=%s\nMANIFEST=%s\n' \
  "$role" "$target_user" "$target_host" "$target_repo" "$manifest"
printf 'FILES=%s\n' "$(find "${entries[@]}" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' | wc -l | tr -d ' ')"

if [[ "$mode" == "dry-run" ]]; then
  printf 'FINAL=PASS (dry run; no remote changes)\n'
  exit 0
fi

((confirmed == 1)) || fail "deployment requires --deploy --yes"
[[ -n "$target_password" ]] || fail "set ${role^^}_PASSWORD in .env"
export SSHPASS="$target_password"
target="$target_user@$target_host"
ssh_options=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

temp_dir="$(mktemp -d)"
[[ "$temp_dir" == /tmp/* ]] || fail "unexpected temporary directory: $temp_dir"
trap 'rm -rf -- "$temp_dir"' EXIT
bundle="$temp_dir/N0JCG-SCANNER-$role.tar.gz"
checksums="$temp_dir/N0JCG-SCANNER-$role.sha256"
stage_dir="$temp_dir/stage"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
remote_bundle="/tmp/N0JCG-SCANNER-$role-$stamp.tar.gz"
remote_checksums="/tmp/N0JCG-SCANNER-$role-$stamp.sha256"
backup="$target_repo/runtime/patch_backups/${role}_deployment_$stamp"

mkdir -p "$stage_dir"
if [[ "$role" == "roc" ]]; then
  mkdir -p "$stage_dir/web/pi-scanner"
  cp -a web/. "$stage_dir/web/pi-scanner/"
  package_entries=(web/pi-scanner)
else
  cp -a "${entries[@]}" "$stage_dir/"
  package_entries=("${entries[@]}")
fi

(
  cd "$stage_dir"
  tar -czf "$bundle" --exclude='*/__pycache__' --exclude='*.pyc' --exclude='*.pyo' "${package_entries[@]}"
  while IFS= read -r file; do
    hash="$(sha256sum "$file" | cut -d' ' -f1)"
    printf '%s  %s\n' "$hash" "$file"
  done < <(find "${package_entries[@]}" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' | sort) > "$checksums"
)

sshpass -e ssh "${ssh_options[@]}" "$target" true
sshpass -e scp "${ssh_options[@]}" "$bundle" "$target:$remote_bundle"
sshpass -e scp "${ssh_options[@]}" "$checksums" "$target:$remote_checksums"

sshpass -e ssh "${ssh_options[@]}" "$target" sh -s -- \
  "$target_repo" "$backup" "$remote_bundle" "$remote_checksums" "$role" <<'REMOTE_SCRIPT'
set -eu
repo="$1"
backup="$2"
bundle="$3"
checksums="$4"
role="$5"

mkdir -p "$repo" "$backup"
if [ "$role" = roc ]; then
  entries="web/pi-scanner"
else
  entries="config src systemd tools requirements.txt"
fi
for entry in $entries; do
  if [ -e "$repo/$entry" ]; then
    mkdir -p "$backup/$(dirname "$entry")"
    cp -a "$repo/$entry" "$backup/$entry"
  fi
done
tar -xzf "$bundle" -C "$repo"
cd "$repo"
sha256sum -c "$checksums"
rm -f "$bundle" "$checksums"
REMOTE_SCRIPT

if ((restart == 1)); then
  if [[ "$role" == "roc" ]]; then
    printf '%s\n' "$target_password" | sshpass -e ssh "${ssh_options[@]}" "$target" sudo -S -p sudo: -- systemctl restart n0jcg-roc.service
    sshpass -e ssh "${ssh_options[@]}" "$target" systemctl is-active n0jcg-roc.service
  else
    printf '%s\n' "$target_password" | sshpass -e ssh "${ssh_options[@]}" "$target" sudo -S -p sudo: -- systemctl restart pi-p25-scanner.service pi-p25-audio-pool.service pi-p25-raw-audio-bridge.service
    printf '%s\n' "$target_password" | sshpass -e ssh "${ssh_options[@]}" "$target" sudo -S -p sudo: -- systemctl try-restart pi-scanner-vhf-worker.service pi-scanner-uhf-worker.service
  fi
fi

printf 'BACKUP=%s\nFINAL=PASS\n' "$backup"

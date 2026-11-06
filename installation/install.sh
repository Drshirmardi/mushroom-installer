#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $(id -u) -ne 0 ]]; then
  printf 'Run with sudo: sudo bash installation/install.sh\n' >&2
  exit 1
fi
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || {
  printf 'Supported host: Ubuntu Server 24.04 LTS. No changes made.\n' >&2
  exit 1
}
[[ $(uname -m) == x86_64 ]] || {
  printf 'Current release images require an x86_64/amd64 server. No changes made.\n' >&2
  exit 1
}
if ! command -v python3 >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends python3
fi
exec python3 -X utf8 "$(dirname -- "${BASH_SOURCE[0]}")/manager.py" "${@:-install}"

#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run with sudo bash.' >&2; exit 1; }
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 && $(uname -m) == x86_64 ]] || {
  echo 'Supported host: Ubuntu 24.04 LTS on amd64.' >&2; exit 1;
}
version=latest
run_tool=true
while [[ $# -gt 0 ]]; do
  case $1 in
    --version) [[ $# -ge 2 ]] || exit 2; version=$2; shift 2 ;;
    --no-run) run_tool=false; shift ;;
    *) echo 'Usage: install.sh [--version v1.0.1] [--no-run]' >&2; exit 2 ;;
  esac
done
[[ $version == latest || $version =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
if ! command -v python3 >/dev/null || ! command -v curl >/dev/null; then
  apt-get update
  apt-get install -y --no-install-recommends python3 curl ca-certificates
fi
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
base=https://github.com/Drshirmardi/mushroom-installer/releases
if [[ $version == latest ]]; then base=$base/latest/download; else base=$base/download/$version; fi
for name in mushroom-installer.tar.gz mushroom-installer.tar.gz.sha256; do
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 10 --max-time 120 --retry 2 "$base/$name" -o "$scratch/$name"
done
# Verify before loading any downloaded Python. Never extract an archive as root with tar.
python3 - "$scratch" <<'PY'
import hashlib, pathlib, re, sys, tarfile
def check(condition,message):
    if not condition: raise ValueError(message)
root=pathlib.Path(sys.argv[1]); archive=root/'mushroom-installer.tar.gz'
check(archive.stat().st_size <= 8*1024*1024, 'Installer archive is too large')
checksum=(root/'mushroom-installer.tar.gz.sha256').read_text().strip()
check(re.fullmatch(r'[a-f0-9]{64}  mushroom-installer\.tar\.gz',checksum), 'Invalid checksum file')
check(hashlib.sha256(archive.read_bytes()).hexdigest()==checksum.split()[0], 'Checksum mismatch')
with tarfile.open(archive,'r:gz') as bundle:
    all_members=bundle.getmembers()
    check(len(all_members)<=129 and sum(m.size for m in all_members)<=16*1024*1024, 'Archive is too large')
    members=[m for m in all_members if m.name=='mushroom-installer/installation/updater.py']
    check(len(members)==1 and members[0].isfile() and members[0].size<256*1024, 'Invalid updater entry')
    (root/'updater.py').write_bytes(bundle.extractfile(members[0]).read())
PY
arguments=(--install-archive "$scratch/mushroom-installer.tar.gz")
if [[ $version != latest ]]; then arguments+=(--expected-version "$version"); fi
python3 "$scratch/updater.py" "${arguments[@]}"
if $run_tool; then
  if [[ -t 1 ]]; then
    /usr/local/sbin/mushroom </dev/tty
  else
    echo 'Installer installed. Run sudo mushroom in an interactive terminal.'
  fi
fi

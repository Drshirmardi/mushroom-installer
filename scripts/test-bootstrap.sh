#!/usr/bin/env bash
# Only run in a disposable Ubuntu container, never on an installed host.
set -Eeuo pipefail
[[ -f /.dockerenv && ! -e /etc/mushroom && ! -e /opt/mushroom-installer ]] || {
  echo 'Requires a fresh disposable container.' >&2; exit 1;
}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
mkdir -p "$scratch/bin" "$scratch/assets" /etc/mushroom /usr/local/sbin
if [[ ${BOOTSTRAP_LEGACY:-0} == 1 ]]; then
  mkdir -p /opt/mushroom-installer/installation
  printf 'legacy source\n' >/opt/mushroom-installer/installation/manager.py
  printf 'legacy launcher\n' >/usr/local/sbin/mushroom
  chmod 755 /usr/local/sbin/mushroom
fi
printf '{"version":1}\n' >/etc/mushroom/installation.json
printf 'synthetic-secret\n' >/etc/mushroom/ga4_api_secret
chmod 600 /etc/mushroom/ga4_api_secret
export TEST_ASSETS="$scratch/assets"
python3 - "$repo" "$TEST_ASSETS" <<'PY'
import pathlib,sys
sys.path.insert(0,str(pathlib.Path(sys.argv[1])/'installation'))
import build
build.build(pathlib.Path(sys.argv[2])/'mushroom-installer.tar.gz','v1.0.1')
PY
cat >"$scratch/bin/curl" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
while [[ $# -gt 0 ]]; do
  case $1 in
    -o) output=$2; shift 2 ;;
    https:*) name=${1##*/}; shift ;;
    *) shift ;;
  esac
done
cp "$TEST_ASSETS/$name" "$output"
SH
chmod 755 "$scratch/bin/curl"
export PATH="$scratch/bin:$PATH"
bash "$repo/install.sh" --version v1.0.1 --no-run
[[ $(mushroom --version) == v1.0.1 ]]
bash "$repo/install.sh" --version v1.0.1 --no-run
[[ $(cat /etc/mushroom/ga4_api_secret) == synthetic-secret ]]
[[ $(stat -c %a /etc/mushroom/ga4_api_secret) == 600 ]]
[[ $(cat /etc/mushroom/installation.json) == '{"version":1}' ]]
if [[ ${BOOTSTRAP_LEGACY:-0} == 1 ]]; then
  [[ $(cat /opt/mushroom-installer/installation/manager.py) == 'legacy source' ]]
fi
printf 'corrupt' >>"$TEST_ASSETS/mushroom-installer.tar.gz"
if bash "$repo/install.sh" --version v1.0.1 --no-run; then exit 1; fi
[[ $(mushroom --version) == v1.0.1 ]]
echo 'Fresh/legacy bootstrap, repeat, preservation and corrupt-download rejection: PASS'

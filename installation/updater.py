"""Versioned installer updates. Never provision services or change private runtime files."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request, urlopen

REPOSITORY = 'Drshirmardi/mushroom-installer'
ROOT = Path('/opt/mushroom-installer')
STATE = Path('/etc/mushroom')
LAUNCHER = Path('/usr/local/sbin/mushroom')
LIMIT = 8 * 1024 * 1024
REQUIRED = {'installation/manager.py', 'installation/updater.py', 'installation/install.sh'}


class UpdateError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise UpdateError(message)


def stable(value):
    require(isinstance(value, str) and re.fullmatch(r'v\d+\.\d+\.\d+', value), 'Use a stable release tag such as v1.0.1.')
    return value


def version_key(value):
    return tuple(map(int, stable(value)[1:].split('.')))


def current_version(bundle=None):
    path = (Path(bundle) if bundle else ROOT / 'current') / 'release.json'
    return json.loads(path.read_text())['version'] if path.is_file() else 'legacy'


def plain(path):
    path = Path(path)
    require(not path.is_symlink(), f'Refusing symlink: {path}')
    if path.exists() and os.name == 'posix':
        info = path.stat()
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022, f'Unsafe ownership or permissions: {path}')
    return path


def write(path, data, mode=0o644):
    path = plain(path)
    plain(path.parent).mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.installer-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def operation_lock():
    import fcntl
    plain(STATE).mkdir(parents=True, exist_ok=True, mode=0o700)
    with plain(STATE / 'installation.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError('Another manager operation is running.') from None
        yield


def download(url):
    require(url.startswith(('https://api.github.com/', 'https://github.com/')), 'Invalid release URL.')
    with urlopen(Request(url, headers={'User-Agent': 'mushroom-installer'}), timeout=5) as response:
        require(response.geturl().startswith(('https://api.github.com/', 'https://github.com/',
                    'https://release-assets.githubusercontent.com/', 'https://objects.githubusercontent.com/')), 'Invalid release redirect.')
        data = response.read(LIMIT + 1)
    require(len(data) <= LIMIT, 'Release download is too large.')
    return data


def release_info(version=None):
    suffix = f'tags/{stable(version)}' if version else 'latest'
    value = json.loads(download(f'https://api.github.com/repos/{REPOSITORY}/releases/{suffix}'))
    tag = stable(value.get('tag_name'))
    require(not value.get('draft') and not value.get('prerelease') and (version is None or tag == version), 'Invalid stable release.')
    return value


def safe_name(name):
    path = PurePosixPath(name)
    require(name and '\\' not in name and ':' not in name and not path.is_absolute()
            and '..' not in path.parts and str(path) == name, 'Invalid archive path.')
    return name


def validate_manifest(manifest, expected=None):
    require(manifest.get('schemaVersion') == 1, 'Unsupported installer manifest.')
    version = stable(manifest.get('version'))
    require(expected is None or expected == version, 'Archive version mismatch.')
    files = manifest.get('files')
    require(isinstance(files, dict) and REQUIRED <= files.keys() and len(files) <= 128, 'Incomplete installer manifest.')
    for name, digest in files.items():
        safe_name(name)
        require(name != 'release.json' and isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest), 'Invalid installer digest.')
    return version


def unpack(archive, destination, expected=None):
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        require(len(members) <= 129 and sum(m.size for m in members) <= 2 * LIMIT, 'Archive exceeds size limits.')
        names = {}
        for member in members:
            require(member.isfile() and member.name.startswith('mushroom-installer/'), 'Only regular installer files are allowed.')
            name = safe_name(member.name[len('mushroom-installer/'):])
            require(name not in names, 'Duplicate archive entry.')
            names[name] = member
        require('release.json' in names, 'Installer manifest is missing.')
        manifest = json.load(bundle.extractfile(names['release.json']))
        version = validate_manifest(manifest, expected)
        require(set(names) == {*manifest['files'], 'release.json'}, 'Unexpected archive contents.')
        for name, member in names.items():
            data = bundle.extractfile(member).read()
            require(name == 'release.json' or hashlib.sha256(data).hexdigest() == manifest['files'][name], 'File checksum mismatch.')
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755 if name.endswith('.sh') else 0o644)
    return version


def link(path, target):
    temporary = path.with_name(f'.{path.name}-{os.getpid()}')
    require(not temporary.exists() and not temporary.is_symlink(), 'Another activation is in progress.')
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def activate(directory, version):
    releases = plain(ROOT / 'releases')
    releases.mkdir(exist_ok=True)
    current = ROOT / 'current'
    require(not current.exists() or current.is_symlink(), 'Current path is not a managed link.')
    old = os.readlink(current) if current.is_symlink() else None
    require(old is None or current.resolve().parent == releases.resolve(), 'Current link points outside managed releases.')
    destination = plain(releases / version)
    if destination.exists():
        require((destination / 'release.json').read_bytes() == (directory / 'release.json').read_bytes(), 'Different build for an existing version.')
        manifest = json.loads((directory / 'release.json').read_text())
        require(all(not (destination / n).is_symlink() and hashlib.sha256((destination / n).read_bytes()).hexdigest() == h
                    for n, h in manifest['files'].items()), 'Existing release files are damaged.')
    else:
        os.replace(directory, destination)
    check = subprocess.run([sys.executable, '-B', str(destination / 'installation/manager.py'), '--version'],
                           capture_output=True, text=True, timeout=15)
    require(check.returncode == 0 and check.stdout.strip() == version, 'New installer startup check failed.')
    old_launcher = LAUNCHER.read_bytes() if plain(LAUNCHER).exists() else None
    previous = ROOT / 'previous'
    require(not previous.exists() or previous.is_symlink(), 'Previous path is not a managed link.')
    previous_old = os.readlink(previous) if previous.is_symlink() else None
    launcher = f'#!/usr/bin/env bash\nset -Eeuo pipefail\nexec python3 -X utf8 {ROOT}/current/installation/manager.py "$@"\n'.encode()
    try:
        link(current, f'releases/{version}')
        write(LAUNCHER, launcher, 0o755)
        if old and old != f'releases/{version}':
            link(previous, old)
    except BaseException:
        if old:
            link(current, old)
        elif current.is_symlink():
            current.unlink()
        if old_launcher is not None:
            write(LAUNCHER, old_launcher, 0o755)
        elif LAUNCHER.is_file():
            LAUNCHER.unlink()
        if previous_old:
            link(previous, previous_old)
        elif previous.is_symlink():
            previous.unlink()
        raise
    return version


def install_archive(archive, expected=None):
    plain(ROOT).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.incoming-', dir=ROOT) as scratch:
        directory = Path(scratch) / 'bundle'
        directory.mkdir()
        version = unpack(archive, directory, expected)
        return activate(directory, version)


def install_local(bundle):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / 'release.json').read_text())
    version = validate_manifest(manifest)
    if bundle == (ROOT / 'current').resolve():
        return version
    plain(ROOT).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.incoming-', dir=ROOT) as scratch:
        directory = Path(scratch) / 'bundle'
        for name, digest in manifest['files'].items():
            source = bundle / name
            require(source.is_file() and not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'Local file checksum mismatch.')
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if name.endswith('.sh') else 0o644)
        shutil.copyfile(bundle / 'release.json', directory / 'release.json')
        return activate(directory, version)


def update(version=None, check=False, yes=False):
    release = release_info(version)
    target, current = release['tag_name'], current_version()
    print(f'Installed: {current}; available: {target}')
    if check or current == target:
        return target
    if current.startswith('v') and version_key(target) < version_key(current) and not yes:
        require(sys.stdin.isatty() and input(f'Type {target} to downgrade: ').strip() == target, 'Downgrade cancelled; unattended downgrade requires --yes.')
    assets = {item['name']: item['browser_download_url'] for item in release.get('assets', [])}
    name = 'mushroom-installer.tar.gz'
    prefix = f'https://github.com/{REPOSITORY}/releases/download/{target}/'
    require(all(assets.get(n) == prefix + n for n in [name, name + '.sha256']), 'Incomplete release assets.')
    data = download(assets[name])
    checksum = download(assets[name + '.sha256']).decode('ascii').strip()
    require(re.fullmatch(r'[a-f0-9]{64}  mushroom-installer\.tar\.gz', checksum)
            and hashlib.sha256(data).hexdigest() == checksum.split()[0], 'Download checksum mismatch.')
    with tempfile.TemporaryDirectory(prefix='mushroom-download-') as scratch:
        archive = Path(scratch) / name
        archive.write_bytes(data)
        install_archive(archive, target)
    print(f'Installer {target} activated. Reopen sudo mushroom. Private settings and application services were preserved.')
    return target


def panel_offer(bundle):
    cache = STATE / 'installer-update-check.json'
    try:
        record = json.loads(cache.read_text()) if cache.is_file() else {}
        if record.get('failed') and time.time() - record.get('checkedAt', 0) < 6 * 3600:
            return
        latest = record.get('latest') if time.time() - record.get('checkedAt', 0) < 6 * 3600 else None
        if latest is None:
            latest = release_info()['tag_name']
            write(cache, json.dumps({'checkedAt': time.time(), 'latest': latest}).encode(), 0o600)
        current = current_version(bundle)
        if current.startswith('v') and version_key(latest) <= version_key(current):
            return
        if input(f'Installer {latest} is available. Install it now? [y/N]: ').strip().lower() == 'y':
            update(latest)
            return True
    except (OSError, ValueError, KeyError, EOFError, UpdateError) as error:
        print(f'Update check unavailable ({type(error).__name__}); continuing with the installed tool.')
        try:
            write(cache, json.dumps({'checkedAt': time.time(), 'failed': True}).encode(), 0o600)
        except (OSError, UpdateError):
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-archive', type=Path, required=True)
    parser.add_argument('--expected-version')
    args = parser.parse_args()
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0, 'Run as root on Ubuntu 24.04.')
        with operation_lock():
            print(install_archive(args.install_archive, args.expected_version))
    except Exception as error:
        print(str(error) if isinstance(error, UpdateError) else f'Update failed ({type(error).__name__}).', file=sys.stderr)
        sys.exit(1)

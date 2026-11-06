"""Build an allowlisted, self-describing installer release."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from manager import BUNDLE_FILES

ROOT = Path(__file__).resolve().parent.parent
FILES = [*BUNDLE_FILES, 'release.json']


def build(destination, version='development', source_sha=''):
    if version != 'development' and not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError('Use a stable version such as v1.0.1')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {}
    for name in BUNDLE_FILES:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Not a regular installation input: {name}')
        payload[name] = path.read_bytes().replace(b'\r\n', b'\n')
    manifest = {'schemaVersion': 1, 'version': version, 'sourceSha': source_sha,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    payload['release.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
    with tarfile.open(destination, 'w:gz') as archive:
        for name, data in payload.items():
            info = tarfile.TarInfo(f'mushroom-installer/{name}')
            info.size = len(data)
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(
        f'{digest}  {destination.name}\n', encoding='utf-8')
    destination.with_name('release.json').write_bytes(payload['release.json'])
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='development')
    parser.add_argument('--source-sha', default='')
    args = parser.parse_args()
    print(build(ROOT / 'installation/dist/mushroom-installer.tar.gz', args.version, args.source_sha))

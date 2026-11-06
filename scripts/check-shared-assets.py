"""Verify the recorded public runtime assets, optionally against an application checkout."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check(repository=None):
    manifest = json.loads((ROOT / 'shared-assets.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid shared asset path')
        for base in [ROOT] + ([Path(repository)] if repository else []):
            path = base / relative
            if path.is_symlink() or hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
                raise ValueError(f'Shared asset mismatch: {name}')
    print(f"Verified {len(manifest['files'])} shared assets")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository')
    check(parser.parse_args().repository)

"""Build the standalone, allowlisted server installer; no checkout secrets are packaged."""
import hashlib
from pathlib import Path
import tarfile
from manager import BUNDLE_FILES

ROOT = Path(__file__).resolve().parent.parent
FILES = BUNDLE_FILES


def build(destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, 'w:gz') as archive:
        for name in FILES:
            path = ROOT / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Not a regular installation input: {name}')
            info = archive.gettarinfo(str(path), arcname=f'mushroom-installer/{name}')
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mode = 0o755 if name.endswith('.sh') else 0o644
            with path.open('rb') as source:
                archive.addfile(info, source)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(
        f'{digest}  {destination.name}\n', encoding='utf-8')
    return destination


if __name__ == '__main__':
    print(build(ROOT / 'installation/dist/mushroom-installer.tar.gz'))

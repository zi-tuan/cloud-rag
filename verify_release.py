"""Validate the release archive without unpacking or executing its contents."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts for n in names):
            raise ValueError('unsafe or duplicate release paths')
        manifest = json.loads(archive.read('release-manifest.json'))
        if set(names) != set(manifest['files']) | {'release-manifest.json'}:
            raise ValueError('release file list mismatch')
        for name,digest in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('release checksum mismatch: '+name)
        return dict(verified_files=len(manifest['files']),corpus=manifest['corpus'])


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('archive',nargs='?',default=str(Path(__file__).parent/'data/uav-rag-local-release.zip'))
    print(json.dumps(verify(parser.parse_args().archive),ensure_ascii=False,indent=2))

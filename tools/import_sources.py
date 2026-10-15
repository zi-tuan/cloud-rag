"""One-time audited import. Never overwrite an existing destination file."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import argparse

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description='Optional provenance utility; not needed to build or run the merged project.')
    parser.add_argument('--business', required=True, type=Path)
    parser.add_argument('--rag', required=True, type=Path)
    parser.add_argument('--openssl', required=True, type=Path)
    args = parser.parse_args()
    business, rag = args.business, args.rag
    records = []
    def copy(source, destination):
        raw = source.read_bytes()
        if any(marker in raw for marker in (b'-----BEGIN PRIVATE KEY-----', b'-----BEGIN OPENSSH PRIVATE KEY-----', b'-----BEGIN RSA PRIVATE KEY-----', b'-----BEGIN EC PRIVATE KEY-----')):
            return  # Even public demo keys are excluded from the delivery corpus.
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != raw:
                raise ValueError('existing modification preserved: ' + str(destination))
        else:
            destination.write_bytes(raw)
        records.append({'path': destination.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest()})
    for folder in ('cloud', 'jetson', 'shared', 'services', 'deploy', 'tests'):
        for path in (business/folder).rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part.startswith('.') or part in ('__pycache__', 'node_modules') for part in path.relative_to(business).parts):
                continue
            if path.suffix not in ('.py', '.json', '.yaml', '.yml', '.toml', '.md', '.sh', '.txt') and path.name not in ('Dockerfile', 'pi-edge'):
                continue
            copy(path, ROOT/path.relative_to(business))
    for name in ('requirements-dev.txt', '.dockerignore', '.gitignore'):
        copy(business/name, ROOT/name)
    copy(business/'README.md', ROOT/'docs/business-original.md')
    copy(business/'docs/service-layout.md', ROOT/'docs/service-layout.md')
    for folder in ('uav_rag', 'docs'):
        for path in (rag/folder).glob('*'):
            if path.suffix in ('.py', '.md'):
                copy(path, ROOT/path.relative_to(rag))
    copy(rag/'tests/test_rag.py', ROOT/'tests/test_rag.py')
    copy(rag/'ON-DEMAND-RAG.md', ROOT/'docs/rag-original-on-demand.md')
    # Copy exactly the indexed public source corpus, verifying every input hash.
    with sqlite3.connect(f'file:{(rag/"data/openssl.sqlite3").as_posix()}?mode=ro', uri=True) as db:
        for relative, digest in db.execute('SELECT DISTINCT path,sha256 FROM chunks'):
            source = args.openssl/relative
            if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError('source changed: ' + relative)
            copy(source, ROOT/'sources/openssl'/relative)
    (ROOT/'docs/import-manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(f'Imported {len(records)} files without overwriting modifications; database will be rebuilt locally.')

if __name__ == '__main__':
    main()

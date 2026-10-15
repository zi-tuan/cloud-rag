"""Versioned SQLite backup and self-contained RAG release; never ships credentials."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from uav_rag.index import KnowledgeBase

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def verify(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory/'release-manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or sha(path) != expected:
            raise ValueError('release checksum mismatch: ' + name)
    kb = KnowledgeBase(directory/'data/openssl.sqlite3')
    if kb.metadata != manifest['knowledge']:
        raise ValueError('database metadata mismatch')
    from uav_rag.catalog import SERVICES
    from uav_rag.openssl_source import descriptions
    checks = {'catalog_revision': hashlib.sha256(json.dumps(SERVICES, sort_keys=True).encode()).hexdigest(),
              'description_revision': hashlib.sha256(json.dumps(descriptions(), ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              'algorithm_document_revision': sha(directory/'docs/OPENSSL-ALGORITHMS.md')}
    if any(kb.metadata[key] != value for key, value in checks.items()):
        raise ValueError('code/document/database version mismatch; rebuild before release')
    return manifest

def build():
    ROOT.joinpath('dist').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'dist') as temporary:
        stage = Path(temporary)
        (stage/'data').mkdir()
        with closing(sqlite3.connect(f'file:{(ROOT/"data/openssl.sqlite3").as_posix()}?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(stage/'data/openssl.sqlite3')) as target:
                source.backup(target)
                assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        kb = KnowledgeBase(stage/'data/openssl.sqlite3')
        paths = [*ROOT.joinpath('uav_rag').glob('*.py'), ROOT/'cloud/rag/Dockerfile',
                 ROOT/'sources/openssl/LICENSE.txt', ROOT/'docs/OPENSSL-ALGORITHMS.md',
                 ROOT/'docs/CLOUD-DEPLOYMENT.md', ROOT/'tools/release.py',
                 ROOT/'tools/probe_rag.py', ROOT/'tools/__init__.py', ROOT/'deploy/scripts/render_rag.py',
                 ROOT/'deploy/rag-upload.example.yaml', ROOT/'deploy/rag.example.json']
        for path in paths:
            dest = stage/path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        (stage/'.dockerignore').write_text('data/\nsources/\n**/__pycache__/\n*.token\n', encoding='utf-8')
        files = {p.relative_to(stage).as_posix(): sha(p) for p in stage.rglob('*') if p.is_file()}
        manifest = {'knowledge': kb.metadata, 'files': files, 'database_copy_method': 'sqlite3.Connection.backup'}
        raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
        version = hashlib.sha256(raw.encode()).hexdigest()[:16]
        manifest['release_id'] = version
        (stage/'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        verify(stage)
        output = ROOT/'dist'/('rag-'+version)
        if output.exists():
            verify(output)
        else:
            shutil.copytree(stage, output)
        archive = ROOT/'dist'/('rag-'+version+'.zip')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(output.rglob('*')):
                if path.is_file():
                    bundle.write(path, path.relative_to(output))
        archive.with_suffix('.sha256').write_text(sha(archive)+'  '+archive.name+'\n', encoding='utf-8')
        print(json.dumps({'release_id': version, 'directory': str(output), 'archive': str(archive),
                          'sha256': sha(archive)}, indent=2))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        print('verified release ' + verify(args.verify)['release_id'])
    else:
        build()

if __name__ == '__main__':
    main()

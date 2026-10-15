"""Unified local entry point; all paths are relative to this delivery."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import hashlib
from contextlib import closing

ROOT = Path(__file__).resolve().parent

def doctor():
    from uav_rag.index import KnowledgeBase
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
    kb = KnowledgeBase(ROOT/'data/openssl.sqlite3')
    with closing(kb.connect()) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    from uav_rag.catalog import SERVICES
    from uav_rag.openssl_source import descriptions
    assert kb.metadata['catalog_revision'] == hashlib.sha256(json.dumps(SERVICES, sort_keys=True).encode()).hexdigest(), 'catalog changed: rebuild index'
    assert kb.metadata['description_revision'] == hashlib.sha256(json.dumps(descriptions(), ensure_ascii=False, sort_keys=True).encode()).hexdigest(), 'description changed: rebuild index'
    assert kb.metadata['algorithm_document_revision'] == hashlib.sha256((ROOT/'docs/OPENSSL-ALGORITHMS.md').read_bytes()).hexdigest(), 'document changed: rebuild index'
    print(json.dumps({'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'fts5': True,
                      'knowledge': kb.metadata}, ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['doctor', 'index', 'serve', 'test', 'integration', 'release', 'build'])
    args, extra = parser.parse_known_args()
    os.chdir(ROOT)
    if args.action == 'doctor':
        doctor()
        return
    commands = {'index': ['-m', 'uav_rag', 'index'], 'serve': ['-m', 'uav_rag', 'serve'],
                'test': ['-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                'integration': ['-m', 'tools.local_integration'], 'release': ['-m', 'tools.release'],
                'build': ['-m', 'tools.build_images']}
    raise SystemExit(subprocess.call([sys.executable, '-X', 'utf8', *commands[args.action], *extra]))

if __name__ == '__main__':
    main()

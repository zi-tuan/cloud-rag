"""Portable local release bundle; no SSH or remote writes."""
import hashlib
import json
from pathlib import Path
import zipfile
from contextlib import closing

from uav_rag.index import KnowledgeBase


def main():
    root = Path(__file__).resolve().parent
    source = root/'sources/GmSSL' if (root/'sources/GmSSL').exists() else root.parent/'GmSSL'
    kb = KnowledgeBase(root/'data/gmssl.sqlite3')
    output = root/'data/uav-rag-local-release.zip'
    files = [*sorted((root/'uav_rag').glob('*.py')), *sorted((root/'tests').glob('*.py')),
             *sorted((root/'deploy').glob('*')), root/'README.md',root/'ON-DEMAND-RAG.md',root/'Dockerfile',
             root/'.dockerignore',root/'demo.py',root/'package_release.py',root/'verify_release.py',root/'VALIDATION.md',root/'data/gmssl.sqlite3']
    if (root/'data/demo-result.json').exists():
        files.append(root/'data/demo-result.json')
    manifest = {'corpus':kb.metadata,'files':{}}
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if not path.is_file():
                raise ValueError(f'missing release input: {path.name}')
            relative = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            manifest['files'][relative] = hashlib.sha256(raw).hexdigest()
            archive.writestr(relative,raw)
        license_bytes = (source/'LICENSE').read_bytes()
        archive.writestr('licenses/GmSSL-LICENSE',license_bytes)
        manifest['files']['licenses/GmSSL-LICENSE'] = hashlib.sha256(license_bytes).hexdigest()
        with closing(kb.connect()) as db:
            for path,digest in db.execute('SELECT DISTINCT path,sha256 FROM chunks ORDER BY path'):
                raw = (source/path).read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('source changed since indexing; rebuild before packaging')
                relative = 'sources/GmSSL/'+path
                archive.writestr(relative,raw)
                manifest['files'][relative] = digest
        archive.writestr('release-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (root/'data/release-sha256.txt').write_text(digest+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps(dict(archive=str(output),bytes=output.stat().st_size,sha256=digest),indent=2))


if __name__ == '__main__':
    main()

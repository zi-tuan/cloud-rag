"""Drone-side read-only RAG client; local endpoint override never changes flight APIs."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RagClient:
    def __init__(self, url='http://127.0.0.1:8787', token=None, timeout=20):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('','/')):
            raise ValueError('expected a base URL without credentials, path, or query')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('remote RAG endpoint requires HTTPS or a loopback tunnel')
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        headers = {'Content-Type':'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer '+self.token
        from .retrieval import encoded
        request = Request(self.url+path, None if data is None else encoded(data), headers)
        return self.opener.open(request, timeout=self.timeout)

    def query(self, query, candidates, *, top_k=1, cursor=None, max_bytes=6000):
        from .catalog import service_id
        from .retrieval import encoded
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 12000:
            raise ValueError('invalid page budget')
        data = dict(query=query, candidate_services=candidates, top_k=top_k, max_bytes=max_bytes)
        if cursor is not None:
            data['cursor'] = cursor
        with self.request('/v1/knowledge/query', data) as response:
            raw = response.read(max_bytes+1)
        if len(raw) > max_bytes:
            raise ValueError('oversized knowledge page')
        result = json.loads(raw)
        expected = {service_id(c) for c in candidates}
        if (not isinstance(result, dict) or not isinstance(result.get('items'), list)
                or not result['items'] or len(encoded(result)) > max_bytes
                or any(not isinstance(item, dict) or item.get('service_id') not in expected
                       for item in result['items'])
                or not isinstance(result.get('index_revision'), str)
                or not isinstance(result.get('description_revision'), str)
                or not (result.get('next_cursor') is None or isinstance(result['next_cursor'], str))):
            raise ValueError('invalid knowledge page')
        return result

    def enrich(self, task_id, query, candidates, top_k=3):
        with self.request('/v1/services/enrich', dict(task_id=task_id, query=query, candidate_services=candidates, top_k=top_k)) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('oversized RAG response')
        result = json.loads(raw)
        if result.get('task_id') != task_id or not isinstance(result.get('services'), list):
            raise ValueError('RAG response does not match task')
        from .catalog import service_id
        expected = list(dict.fromkeys(map(service_id,candidates)))
        if [s.get('service_id') for s in result['services'] if isinstance(s,dict)] != expected or len(result['services']) != len(expected):
            raise ValueError('RAG candidate binding mismatch')
        return result

    def download(self, service, directory, *, architecture='arm64', max_bytes=2_000_000_000):
        artifact = service['artifact']
        if artifact.get('available') is not True or artifact.get('architecture') != architecture or artifact.get('format') != 'oci-archive':
            raise ValueError('no compatible registered artifact')
        name = service['service_id']
        from .catalog import SERVICES
        if name not in SERVICES or artifact.get('download_path') != '/v1/artifacts/'+name:
            raise ValueError('unregistered artifact path')
        size, expected = artifact['size'], artifact['sha256']
        import re
        if type(size) is not int or not 0 < size <= max_bytes or not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
            raise ValueError('invalid artifact size or digest')
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (name+'-'+expected+'.tar')
        if target.exists():
            with target.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest() == expected:
                    return target
            raise ValueError('cached artifact checksum mismatch')
        fd, tmp = tempfile.mkstemp(prefix='download-', dir=directory)
        try:
            digest, received = hashlib.sha256(), 0
            with os.fdopen(fd,'wb') as out, self.request(artifact['download_path']) as response:
                while block := response.read(1024*1024):
                    received += len(block)
                    if received > size:
                        raise ValueError('oversized artifact')
                    digest.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if received != size or digest.hexdigest() != expected:
                raise ValueError('artifact checksum or size mismatch')
            os.replace(tmp,target)
            return target
        finally:
            Path(tmp).unlink(missing_ok=True)

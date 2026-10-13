"""Bounded JSON API and explicitly registered, immutable artifact distribution."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re

from .catalog import SERVICES


class Artifacts:
    def __init__(self, manifest=None):
        self.entries = {}
        if manifest is None:
            return
        manifest = Path(manifest).resolve()
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if set(data) != {'services'} or not isinstance(data['services'], dict):
            raise ValueError('invalid artifact registry')
        for name, entry in data['services'].items():
            if name not in SERVICES or set(entry) != {'file','sha256','architecture','format'}:
                raise ValueError('unregistered artifact or invalid fields')
            path = (manifest.parent / entry['file']).resolve()
            if not path.is_relative_to(manifest.parent) or not path.is_file():
                raise ValueError('artifact must be a file inside the registry directory')
            if not re.fullmatch('[0-9a-f]{64}', entry['sha256']) or entry['architecture'] not in ('arm64','amd64') or entry['format'] != 'oci-archive':
                raise ValueError('invalid artifact metadata')
            self.entries[name] = {**entry, 'file': path}

    def open(self, name):
        entry = self.entries[name]
        stream = entry['file'].open('rb')
        try:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if not hmac.compare_digest(digest, entry['sha256']):
                raise ValueError('artifact checksum differs from registry')
            # file_digest can leave the position unspecified; obtain size separately.
            size = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            return stream, size
        except Exception:
            stream.close()
            raise

    def describe(self, name):
        if name not in self.entries:
            return {'available': False, 'reason': 'no_registered_service_artifact'}
        with self.open(name)[0] as stream:
            size = os.fstat(stream.fileno()).st_size
        entry = self.entries[name]
        return dict(available=True, sha256=entry['sha256'], size=size,
                    architecture=entry['architecture'], format=entry['format'],
                    download_path='/v1/artifacts/'+name)


def make_server(kb, host='127.0.0.1', port=8787, *, token=None, artifacts=None):
    token = token or ''
    if host not in ('127.0.0.1', 'localhost') and len(token) < 32:
        raise ValueError('non-loopback server requires a token of at least 32 characters')
    if token and len(token) < 32:
        raise ValueError('token must contain at least 32 characters')
    artifacts = artifacts or Artifacts()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            pass  # Do not log tasks, authorization headers, or source excerpts.

        def send_json(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)

        def authorized(self):
            supplied = self.headers.get('Authorization', '')
            if token and not hmac.compare_digest(supplied.encode(), ('Bearer '+token).encode()):
                self.send_json(401, {'error':'unauthorized'})
                return False
            return True

        def do_GET(self):
            if self.path == '/health':
                return self.send_json(200, {'status':'ok','index_revision':kb.metadata['revision']})
            if not self.authorized():
                return
            if self.path == '/v1/services':
                return self.send_json(200, {'services':[dict(service_id=k, label=v['label'], binding=v['binding']) for k,v in SERVICES.items()]})
            prefix = '/v1/artifacts/'
            if self.path.startswith(prefix) and self.path[len(prefix):] in artifacts.entries:
                try:
                    stream, size = artifacts.open(self.path[len(prefix):])
                except (OSError, ValueError):
                    return self.send_json(409, {'error':'artifact_unavailable_or_changed'})
                with stream:
                    self.send_response(200)
                    self.send_header('Content-Type','application/octet-stream')
                    self.send_header('Content-Length',str(size))
                    self.end_headers()
                    while block := stream.read(1024*1024):
                        self.wfile.write(block)
                return
            self.send_json(404, {'error':'not_found'})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != '/v1/services/enrich':
                return self.send_json(404, {'error':'not_found'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= 65536:
                    return self.send_json(413, {'error':'invalid_body_size'})
                if self.headers.get_content_type() != 'application/json':
                    return self.send_json(415, {'error':'expected_application_json'})
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('incomplete request')
                result = kb.enrich(json.loads(raw))
                for service in result['services']:
                    service['artifact'] = artifacts.describe(service['service_id'])
                return self.send_json(200, result)
            except (ValueError, TypeError, UnicodeError):
                return self.send_json(400, {'error':'invalid_request'})
            except (OSError, KeyError):
                return self.send_json(503, {'error':'service_unavailable'})

    return ThreadingHTTPServer((host, port), Handler)

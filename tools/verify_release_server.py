"""Boot the actual packaged server in an isolated working directory and probe it."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from uav_rag.client import RagClient
from tools.release import verify

def main():
    root = Path(__file__).resolve().parents[1]
    release = json.loads((root/'data/release-result.json').read_text(encoding='utf-8-sig'))
    directory = Path(release['directory'])
    manifest = verify(directory)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory() as temporary:
        token = secrets.token_urlsafe(32)
        token_path = Path(temporary)/'service.token'
        token_path.write_text(token, encoding='utf-8')
        env = {**os.environ, 'PYTHONPATH': str(directory), 'UAV_RAG_TOKEN_FILE': str(token_path)}
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'uav_rag', 'serve', '--port', str(port)],
                                   cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            client = RagClient(f'http://127.0.0.1:{port}', token, timeout=1)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('packaged server exited before readiness')
                try:
                    with client.request('/health') as response:
                        health = json.load(response)
                    break
                except (URLError, TimeoutError):
                    time.sleep(.1)
            else:
                raise TimeoutError('packaged server never became ready')
            page = client.query('SM4-GCM 用途与限制', ['sm4-gcm-v1'])
            assert page['index_revision'] == manifest['knowledge']['revision']
            try:
                RagClient(client.url).query('用途', ['sm4-gcm-v1'])
            except HTTPError as exc:
                assert exc.code == 401
                exc.close()
            else:
                raise AssertionError('unauthenticated query unexpectedly accepted')
            result = {'release_id': manifest['release_id'], 'files_verified': len(manifest['files']),
                      'packaged_server': 'passed', 'authenticated_query': 'passed', 'without_token': 401,
                      'revision': page['index_revision'], 'items': len(page['items']),
                      'scope': 'Windows Python HTTP process; container not tested'}
            (root/'data/release-validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result, indent=2))
        finally:
            process.terminate()
            process.communicate(timeout=10)

if __name__ == '__main__':
    main()

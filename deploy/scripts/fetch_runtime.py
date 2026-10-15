"""Download pinned upstream ARM64 runtimes; no credentials are stored here."""
import concurrent.futures
import hashlib
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1] / '.cache'
ROOT.mkdir(exist_ok=True)
VERSION = 'v1.34.11+k3s1'
BASE = 'https://github.com/k3s-io/k3s/releases/download/' + VERSION.replace('+', '%2B') + '/'

def fetch(name, url):
    path = ROOT / name
    with urllib.request.urlopen(url, timeout=60) as response, path.open('wb') as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
    print(name, path.stat().st_size, flush=True)
    return path

if __name__ == '__main__':
    jobs = {
        'k3s-arm64': BASE + 'k3s-arm64',
        'sha256sum-arm64.txt': BASE + 'sha256sum-arm64.txt',
        'kubernetes-mcp-server-linux-arm64': 'https://github.com/containers/kubernetes-mcp-server/releases/download/v0.0.67/kubernetes-mcp-server-linux-arm64',
        'k3s-install.sh': 'https://raw.githubusercontent.com/k3s-io/k3s/' + VERSION + '/install.sh',
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda entry: fetch(*entry), jobs.items()))
    expected = next(line.split()[0] for line in (ROOT / 'sha256sum-arm64.txt').read_text().splitlines() if line.split()[-1] == 'k3s-arm64')
    actual = hashlib.sha256((ROOT / 'k3s-arm64').read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError('K3s checksum mismatch')
    print('K3s upstream checksum verified:', actual)

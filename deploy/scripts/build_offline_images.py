"""按模块 Dockerfile 的 COPY、入口和依赖构建离线 OCI；不执行目标架构代码。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'deploy/.cache'
MODULES = {
    'cloud-api': ('cloud/cloud_api', ('amd64',)),
    'edge-agent': ('jetson/edge_agent', ('arm64',)),
    'pi-runner': ('jetson/pi_runner', ('arm64',)),
    'crypto-service': ('services/crypto_service', ('amd64', 'arm64')),
    'auth-service': ('services/auth_service', ('amd64', 'arm64')),
}


def digest(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def add(archive, name, data, *, mode=0o644, kind=tarfile.REGTYPE, link=''):
    member = tarfile.TarInfo(name)
    member.size, member.mode, member.mtime = len(data), mode, 0
    member.uid = member.gid = 1000 if name.startswith(('state', 'pi-state', 'run/uav-pi', 'home/node', 'workspace/project')) else 0
    member.type, member.linkname = kind, link
    archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def layout(module):
    directory, architectures = MODULES[module]
    copies, stages, environment = [], {}, {}
    dockerfile = ROOT / directory / 'Dockerfile'
    for line in dockerfile.read_text(encoding='utf-8').splitlines():
        if line.startswith('COPY --from='):
            continue
        if line.startswith('COPY '):
            source, target = shlex.split(line)[1:]
            copies.append((source, target))
        elif line.startswith('FROM ') and ' AS ' in line:
            parts = shlex.split(line)
            stages[parts[-1]] = parts[-3]
        elif line.startswith('ENV '):
            environment.update(item.split('=', 1) for item in shlex.split(line)[1:])
        elif line.startswith('WORKDIR '):
            working = line.split()[1]
        elif line.startswith('ENTRYPOINT '):
            entrypoint = json.loads(line[len('ENTRYPOINT '):])
    return {'directory': directory, 'architectures': architectures, 'copies': copies,
            'node': stages.get('node'), 'environment': environment, 'working': working,
            'entrypoint': entrypoint, 'dockerfile_sha256': digest(dockerfile.read_bytes())}


def read_base(path, architecture):
    with tarfile.open(path) as archive:
        def blob(descriptor):
            data = archive.extractfile('blobs/sha256/' + descriptor['digest'].split(':')[1]).read()
            if digest(data) != descriptor['digest'] or len(data) != descriptor['size']:
                raise ValueError('base blob digest or size mismatch')
            return data
        descriptor = json.load(archive.extractfile('index.json'))['manifests'][0]
        manifest = json.loads(blob(descriptor))
        config = json.loads(blob(manifest['config']))
        if config['architecture'] != architecture or config['os'] != 'linux':
            raise ValueError('base platform mismatch')
        layers = [(item, blob(item)) for item in manifest['layers']]
        diff_ids = [digest(gzip.decompress(data) if item['mediaType'].endswith('gzip') else data)
                    for item, data in layers]
        if diff_ids != config['rootfs']['diff_ids']:
            raise ValueError('base uncompressed layers mismatch')
        return descriptor, manifest, config, layers


def wheels(module, architecture, output):
    directory = MODULES[module][0]
    cache = CACHE / ('wheels-runtime-arm64' if module == 'edge-agent' else
                     'wheels-arm64' if architecture == 'arm64' else 'wheels-cloud-amd64')
    platform = 'aarch64' if architecture == 'arm64' else 'x86_64'
    report = output / f'{module}-{architecture}.pip.json'
    subprocess.run(['rtk', 'proxy', sys.executable, '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
                    '--no-index', '--find-links', str(cache), '--only-binary=:all:',
                    '--platform', 'manylinux2014_' + platform, '--platform', 'manylinux_2_28_' + platform,
                    '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312', '--abi', 'abi3',
                    '--report', str(report), '-r', str(ROOT / directory / 'requirements.txt'), '--quiet'], check=True)
    result = []
    for item in json.loads(report.read_text(encoding='utf-8'))['install']:
        url = urlparse(item['download_info']['url'])
        if url.scheme != 'file':
            raise ValueError('offline dependency is not a local wheel')
        path = Path(url2pathname(unquote(url.path)))
        if path.suffix != '.whl':
            raise ValueError('offline dependency is not a wheel')
        expected = item['download_info']['archive_info']['hashes']['sha256']
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('wheel changed after dependency resolution')
        result.append(path)
    return result


def build(module, architecture, output):
    spec = layout(module)
    if architecture not in spec['architectures']:
        raise ValueError(f'{module} does not target {architecture}')
    output.mkdir(parents=True, exist_ok=True)
    dependencies = wheels(module, architecture, output)
    base_descriptor, base_manifest, config, layers = read_base(CACHE / f'python-{architecture}.tar', architecture)
    buffer = io.BytesIO()
    sources = {}
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for source, target in spec['copies']:
            path = ROOT / source
            paths = sorted(path.rglob('*')) if path.is_dir() else [path]
            for file in paths:
                if not file.is_file() or '__pycache__' in file.parts or file.suffix in ('.pyc', '.pem', '.key', '.token') or '.db' in file.name or file.name.startswith('.env'):
                    continue
                name = target.rstrip('/') + '/' + file.relative_to(path).as_posix() if path.is_dir() else target
                data = file.read_bytes()
                add(archive, name.lstrip('/'), data)
                sources[file.relative_to(ROOT).as_posix()] = digest(data)
        vendor = spec['environment']['PYTHONPATH'].split(':')[0].lstrip('/')
        for wheel in dependencies:
            with zipfile.ZipFile(wheel) as packed:
                for name in packed.namelist():
                    if name.endswith('/'):
                        continue
                    if name.startswith('/') or '..' in PurePosixPath(name).parts:
                        raise ValueError('unsafe wheel member')
                    if '.data/' in name:
                        _, scheme, name = name.split('/', 2)
                        if scheme not in ('purelib', 'platlib'):
                            continue
                    add(archive, vendor + '/' + name, packed.read(name))
        if spec['node']:
            descriptor, _, _, node_layers = read_base(CACHE / 'node-arm64.tar', 'arm64')
            if spec['node'].split('@')[1] != descriptor['digest']:
                raise ValueError('Node cache differs from Dockerfile pin')
            found = set()
            for _, data in node_layers:
                with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as packed:
                    for member in packed:
                        name = member.name.removeprefix('./')
                        if name == 'usr/local/bin/node' or name.startswith('usr/lib/aarch64-linux-gnu/libstdc++.so.6') or name == 'usr/lib/aarch64-linux-gnu/libgcc_s.so.1':
                            add(archive, name, packed.extractfile(member).read() if member.isfile() else b'',
                                mode=member.mode, kind=member.type, link=member.linkname)
                            found.add(name)
            if not {'usr/local/bin/node', 'usr/lib/aarch64-linux-gnu/libstdc++.so.6', 'usr/lib/aarch64-linux-gnu/libgcc_s.so.1'} <= found:
                raise ValueError('Node runtime libraries missing')
        for name in ('state', 'pi-state', 'run/uav-pi', 'home/node', 'workspace/project'):
            add(archive, name, b'', mode=0o755, kind=tarfile.DIRTYPE)
    layer = buffer.getvalue()
    config['config'].update(User='1000:1000', WorkingDir=spec['working'], Entrypoint=spec['entrypoint'], Cmd=None)
    environment = dict(value.split('=', 1) for value in config['config']['Env'])
    environment.update(spec['environment'])
    config['config']['Env'] = [key + '=' + value for key, value in environment.items()]
    config['rootfs']['diff_ids'].append(digest(layer))
    config.setdefault('history', []).append({'created_by': 'UAV module Dockerfile sources and resolved offline wheels'})
    config_data = encoded(config)
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': digest(config_data), 'size': len(config_data)},
                'layers': base_manifest['layers'] + [{'mediaType': 'application/vnd.oci.image.layer.v1.tar', 'digest': digest(layer), 'size': len(layer)}]}
    manifest_data = encoded(manifest)
    tag = f'47.109.49.98:30881/uav-mvp/{module}:layout-{architecture}-{digest(manifest_data)[7:19]}'
    descriptor = {'mediaType': manifest['mediaType'], 'digest': digest(manifest_data), 'size': len(manifest_data),
                  'annotations': {'io.containerd.image.name': tag, 'org.opencontainers.image.ref.name': tag}}
    archive_path = output / f'{module}-{architecture}.oci.tar'
    with tarfile.open(archive_path, 'w') as archive:
        add(archive, 'oci-layout', encoded({'imageLayoutVersion': '1.0.0'}))
        add(archive, 'index.json', encoded({'schemaVersion': 2, 'manifests': [descriptor]}))
        for data in [config_data, layer, manifest_data] + [data for _, data in layers]:
            add(archive, 'blobs/sha256/' + digest(data)[7:], data)
    lock = {'module': module, 'architecture': architecture, 'image': tag, 'digest': digest(manifest_data),
            'base_digest': base_descriptor['digest'], 'dockerfile_sha256': spec['dockerfile_sha256'],
            'entrypoint': spec['entrypoint'], 'archive_sha256': digest(archive_path.read_bytes()),
            'sources': sources, 'wheels': {p.name: digest(p.read_bytes()) for p in dependencies}}
    (output / f'{module}-{architecture}.json').write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: lock[key] for key in ('module', 'architecture', 'image', 'digest')}), flush=True)
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', choices=MODULES, action='append')
    parser.add_argument('--architecture', choices=['amd64', 'arm64'])
    parser.add_argument('--output', type=Path, default=CACHE / 'service-layout')
    args = parser.parse_args()
    if args.module and args.architecture:
        unsupported = [module for module in args.module if args.architecture not in MODULES[module][1]]
        if unsupported:
            parser.error('目标架构不适用于模块：' + ', '.join(unsupported))
    for module in args.module or MODULES:
        for architecture in MODULES[module][1]:
            if args.architecture is None or architecture == args.architecture:
                build(module, architecture, args.output)


if __name__ == '__main__':
    main()

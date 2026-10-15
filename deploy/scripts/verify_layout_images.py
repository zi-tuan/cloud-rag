"""核验离线 OCI 的摘要、平台、入口、源码与完整仓库包依赖；不运行目标架构代码。"""
import argparse
import ast
import io
import json
from pathlib import Path
import struct
import tarfile

from deploy.scripts.build_offline_images import CACHE, MODULES, ROOT, digest, layout, read_base


def verify(directory, module, architecture):
    lock = json.loads((directory / f'{module}-{architecture}.json').read_text(encoding='utf-8'))
    path = directory / f'{module}-{architecture}.oci.tar'
    if digest(path.read_bytes()) != lock['archive_sha256']:
        raise ValueError('archive checksum differs from build record')
    descriptor, _, config, layers = read_base(path, architecture)
    if descriptor['digest'] != lock['digest'] or config['config']['Entrypoint'] != layout(module)['entrypoint']:
        raise ValueError('image lock differs from platform or entrypoint')
    sources = {}
    app = layout(module)['working'].lstrip('/') + '/'
    with tarfile.open(fileobj=io.BytesIO(layers[-1][1]), mode='r:') as packed:
        members = {item.name: item for item in packed if item.isfile()}
        native = 0
        for name, member in members.items():
            if '.so' not in Path(name).name and name != 'usr/local/bin/node':
                continue
            header = packed.extractfile(member).read(20)
            if header[:4] == b'\x7fELF':
                machine = struct.unpack('<H' if header[5] == 1 else '>H', header[18:20])[0]
                if machine != {'amd64': 62, 'arm64': 183}[architecture]:
                    raise ValueError('native library architecture mismatch: ' + name)
                native += 1
        for source, checksum in lock['sources'].items():
            data = packed.extractfile(members[app + source]).read()
            if digest(data) != checksum or digest((ROOT / source).read_bytes()) != checksum:
                raise ValueError('image source differs from current repository: ' + source)
            if source.endswith('.py'):
                sources[source] = data.decode('utf-8')
        repository_packages = {'shared', 'cloud', 'jetson', 'services', 'deploy'}
        for source, data in sources.items():
            tree = ast.parse(data, filename=source)
            for node in ast.walk(tree):
                names = [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else [item.name for item in node.names] if isinstance(node, ast.Import) else []
                for name in names:
                    if name.split('.')[0] not in repository_packages:
                        continue
                    candidate = name.replace('.', '/')
                    # 初始化工具按运行角色选择导入另外一端，另一端无需打包。
                    if source == 'deploy/scripts/initialize_control.py' and name.split('.')[0] not in {p.split('/')[0] for p in sources}:
                        continue
                    if candidate + '.py' not in sources and candidate + '/__init__.py' not in sources:
                        raise ValueError(f'image package missing: {source} -> {name}')
        if config['config']['User'] != '1000:1000':
            raise ValueError('runtime must use the documented uid/gid')
        if any(name.startswith((app + 'tests/', app + 'docs/evidence/')) for name in members):
            raise ValueError('unexpected test, evidence or credential in application layer')
    print(f'{module}/{architecture}: OCI、入口、{len(sources)} 个 Python 源文件及 {native} 个原生 ELF 核验通过')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=CACHE / 'service-layout')
    args = parser.parse_args()
    for module, (_, architectures) in MODULES.items():
        for architecture in architectures:
            verify(args.directory, module, architecture)


if __name__ == '__main__':
    main()

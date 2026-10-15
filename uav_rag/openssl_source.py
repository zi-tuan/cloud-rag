"""Inspect supplied source, never infer runtime capabilities from another binary."""
import hashlib
import re
from pathlib import Path

ROOTS = {'apps', 'crypto', 'doc', 'include', 'providers', 'ssl', 'test', 'demos', 'Configurations'}
ROOT_FILES = {'README.md', 'README-PROVIDERS.md', 'README-FIPS.md', 'INSTALL.md',
              'NOTES-WINDOWS.md', 'NOTES-UNIX.md', 'VERSION.dat', 'Configure', 'LICENSE.txt'}
PINNED_VERSION = '4.2.0-dev'


def inspect(source):
    source = Path(source)
    values = dict(re.findall(r'^(\w+)=(.*)$', (source/'VERSION.dat').read_text(encoding='utf-8'), re.M))
    version = '.'.join(values[k] for k in ('MAJOR', 'MINOR', 'PATCH'))
    if values.get('PRE_RELEASE_TAG'):
        version += '-' + values['PRE_RELEASE_TAG']
    if version != PINNED_VERSION:
        raise ValueError('algorithm documentation requires review for OpenSSL ' + version)
    names_path = 'providers/implementations/include/prov/names.h'
    names = dict(re.findall(r'#\s*define\s+(PROV_NAMES_\w+)\s+"([^"]+)"',
                           (source/names_path).read_text(encoding='utf-8')))
    inventory = []
    # Static registrations, including conditional branches, not runtime output.
    for provider, filename, prefix in [('default', 'defltprov.c', 'deflt'),
                                       ('legacy', 'legacyprov.c', 'legacy')]:
        path = 'providers/' + filename
        content = (source/path).read_text(encoding='utf-8')
        for match in re.finditer(r'static const OSSL_ALGORITHM(?:_CAPABLE)?\s+'
                                + prefix + r'_(\w+)\[\]\s*=\s*\{(.*?)\n\};', content, re.S):
            operation, body = match.groups()
            if operation in ('encoder', 'decoder', 'store', 'skeymgmt'):
                continue
            for macro in dict.fromkeys(re.findall(r'PROV_NAMES_\w+', body)):
                if macro not in names:
                    raise ValueError('unresolved OpenSSL algorithm name: ' + macro)
                inventory.append(dict(provider=provider, operation=operation,
                    names=names[macro].split(':'), macro=macro, source=path,
                    runtime_enabled='unverified'))
    if not inventory:
        raise ValueError('empty OpenSSL algorithm inventory')
    inputs = ['VERSION.dat', 'Configure', names_path, 'providers/defltprov.c',
              'providers/legacyprov.c', 'doc/man7/OSSL_PROVIDER-default.pod',
              'doc/man7/OSSL_PROVIDER-legacy.pod']
    return dict(version=version, runtime_enabled='unverified',
                runtime_reason='Supplied source has not been built/probed by this indexer.',
                inventory=inventory, source_hashes={p: hashlib.sha256((source/p).read_bytes()).hexdigest()
                                                  for p in inputs})


def descriptions():
    from copy import deepcopy
    from .descriptions import DESCRIPTIONS
    from .catalog import SERVICES
    result = deepcopy(DESCRIPTIONS)
    for sid, description in result.items():
        description['sources'] = [dict(title='OpenSSL 4.2.0-dev local documentation', path=p)
                                  for p in SERVICES[sid]['paths'] if p.endswith('.pod')]
    result['sm4-gcm-v1']['limitations'].append(
        '本地 OpenSSL 文档提示 SM4 的可移植实现可能存在秘密相关查表；须评估目标处理器的缓存时序侧信道。')
    return result

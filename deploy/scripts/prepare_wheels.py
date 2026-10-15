"""下载独立模块的目标 Linux wheel，供统一离线 OCI 构建器使用。"""
import argparse
import subprocess
import sys

from deploy.scripts.build_offline_images import CACHE, MODULES, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', choices=MODULES, action='append')
    args = parser.parse_args()
    for module in args.module or MODULES:
        directory, architectures = MODULES[module]
        for architecture in architectures:
            cache = CACHE / ('wheels-runtime-arm64' if module == 'edge-agent' else
                             'wheels-arm64' if architecture == 'arm64' else 'wheels-cloud-amd64')
            cache.mkdir(parents=True, exist_ok=True)
            platform = 'aarch64' if architecture == 'arm64' else 'x86_64'
            subprocess.run(['rtk', 'proxy', sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                            '--python-version', '312', '--implementation', 'cp', '--abi', 'cp312', '--abi', 'abi3',
                            '--platform', 'manylinux2014_' + platform, '--platform', 'manylinux_2_28_' + platform,
                            '--dest', str(cache), '-r', str(ROOT / directory / 'requirements.txt')], check=True)


if __name__ == '__main__':
    main()

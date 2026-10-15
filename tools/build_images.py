"""One explicit build entry for all deployment roles, using the root context."""
import argparse
from pathlib import Path
import subprocess
from deploy.scripts.build_offline_images import MODULES

def main():
    modules = {**MODULES, 'rag': ('cloud/rag', ('amd64', 'arm64'))}
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', choices=list(modules), required=True)
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--tag', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--load', action='store_true')
    mode.add_argument('--push', action='store_true')
    args = parser.parse_args()
    directory, supported = modules[args.module]
    if args.architecture not in supported:
        parser.error('architecture is not supported for this role')
    root = Path(__file__).resolve().parents[1]
    command = ['docker', 'buildx', 'build', '--platform', 'linux/'+args.architecture,
               '-f', directory+'/Dockerfile', '-t', args.tag, '--push' if args.push else '--load', '.']
    raise SystemExit(subprocess.call(command, cwd=root))

if __name__ == '__main__':
    main()

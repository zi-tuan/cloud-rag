#!/bin/bash
set -eu
python3 - <<'PY'
import json,pathlib,shutil,time
root=pathlib.Path('/home/uav/uav-agent-mvp')
project=root/'project'
config=project/'.mcp.json'
data=json.loads(config.read_text())
args=data['mcpServers']['kubernetes']['args']
if '--config' not in args:
    shutil.copy2(str(config),str(config)+'.before-scope-'+str(int(time.time())))
    args.extend(['--config','/workspace/project/mcp-readonly.toml'])
elif args[args.index('--config')+1]!='/workspace/project/mcp-readonly.toml':
    raise RuntimeError('Existing custom MCP config must be merged')
shutil.copy2(str(root/'jetson/pi_runner/config/mcp-readonly.toml'),str(project/'mcp-readonly.toml'))
instructions=project/'AGENTS.md'
text=(root/'jetson/pi_runner/config/pi-project-instructions.md').read_text()
if instructions.exists() and instructions.read_text()!=text:
    raise RuntimeError('Existing project instructions must be merged')
instructions.write_text(text)
config.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print('MCP scope configuration installed')
PY

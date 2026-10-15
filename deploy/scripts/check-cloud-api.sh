#!/bin/bash
set -eu
python3 - <<'PY'
import pathlib,ssl,urllib.request,json
root=pathlib.Path('/home/uav/uav-agent-mvp')
import os
token=os.environ['UAV_ADMIN_TOKEN']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(root/'tls/ca.crt'))))
req=urllib.request.Request('https://localhost:8443/api/overview',headers={'Authorization':'Bearer '+token})
with opener.open(req,timeout=10) as response:
    data=json.load(response)
    print(json.dumps({'authenticated':response.status==200,'device':data['device'],'tasks':len(data['tasks'])}))
PY

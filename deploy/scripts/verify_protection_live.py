"""核对现场源码、认证与只读遥测；结果只包含公开状态和摘要。"""
import hashlib
import json
from pathlib import Path
import shlex
import time

from deploy.scripts.remote_control import connect, run, kubectl

ROOT = Path(__file__).resolve().parents[2]
FILES = ['services/crypto_service/repository/crypto.py','services/crypto_service/service/crypto.py','services/crypto_service/controller/crypto.py','shared/communication/auth_protocol.py','shared/communication/crypto_client.py']


def main():
    cloud,edge=connect('47.109.49.98',24531),connect('192.168.8.101')
    try:
        code='import hashlib,json\nfrom pathlib import Path\nprint(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in '+repr(FILES)+'}))\n'
        actual_cloud=json.loads(run(cloud,'docker exec -i uav-cloud-sm4 python -',data=code))
        actual_edge=json.loads(run(edge,'env KUBECONFIG=/home/uav/.kube/config kubectl -n uav-mvp exec -i deploy/sm4-service -- python -',data=code))
        expected={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in FILES}
        assert actual_cloud==actual_edge==expected,'deployed source drift'
        request_code='''import json,time,urllib.request
from pathlib import Path
token=Path('/home/uav/uav-agent-mvp/control/keys/operator.token').read_text().strip()
request=urllib.request.Request('http://127.0.0.1:38443/api/overview',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(request,timeout=10) as res: d=json.load(res)
telemetry=d['device'].get('status',{}).get('telemetry',{})
auth=d['identity_auth']; comm=d['communication']
assert d['device']['online'] and auth['authenticated'] and auth['bidirectional_confirmation']
assert auth['epoch']==comm['epoch'] and auth['protection_profile']==comm['protection_profile']
assert telemetry.get('read_only') and telemetry.get('online') and telemetry['messages']['GLOBAL_POSITION_INT']['age_seconds']<5
print(json.dumps({'identity_auth':auth,'communication':comm,'telemetry':{'read_only':True,'online':True,'position':telemetry['messages']['GLOBAL_POSITION_INT'],'invalid_frames':telemetry['invalid_frames']},'trust':d['device']['status'].get('trust_start','unknown'),'mission':d['mission']}))
'''
        state=json.loads(run(cloud,'python3 -',data=request_code))
        result={'verified_at':int(time.time()),'source_hashes':expected,'cloud_edge_sources_match':True,**state}
        (ROOT/'docs/evidence/deploy/protection-live-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        cloud.close();edge.close()


if __name__=='__main__': main()

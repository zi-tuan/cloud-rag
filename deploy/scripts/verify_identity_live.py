"""复核真实云边认证、只读仿真遥测和持有 root.key 的负面准入测试。"""
import json
import argparse
from pathlib import Path
import shlex

from deploy.scripts.remote_control import BASE, connect, run

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-policy', choices=['Ed25519', 'RSA', 'SM2'])
    args = parser.parse_args()
    cloud = connect('47.109.49.98', 24531)
    try:
        output = run(cloud, 'python3 -', data="""import json,ssl,time,urllib.request
from pathlib import Path
root=Path('/home/uav/uav-agent-mvp')
token=(root/'control/keys/operator.token').read_text().strip()
context=ssl.create_default_context(cafile=str(root/'tls/ca.crt'))
request=urllib.request.Request('https://localhost:8443/api/overview',headers={'Authorization':'Bearer '+token})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
with opener.open(request,timeout=10) as response: data=json.load(response)
device=data['device']; auth=data['identity_auth'];telemetry=(device.get('status') or {}).get('telemetry') or {}
assert device['online'] and time.time()-device['last_seen']<20,'edge heartbeat stale'
assert auth['authenticated'] and auth['policy'] in ('Ed25519','RSA','SM2') and auth['bidirectional_confirmation']
communication=data['communication']
assert auth['epoch']==communication['epoch'] and auth['suite']==communication['suite'] and auth['policy']==communication['auth_policy']
assert telemetry.get('online') and telemetry.get('read_only') is True,'simulation unavailable'
assert telemetry.get('invalid_frames')==0,'invalid telemetry frames'
assert {'HEARTBEAT','GLOBAL_POSITION_INT','ATTITUDE'}.issubset(telemetry.get('messages',{})), 'simulation position or attitude missing'
print(json.dumps({'device_id':device['device_id'],'device_online':True,'identity_auth':auth,
                 'flight_online':True,'read_only':True,'received_messages':telemetry['received_messages'],
                 'messages':sorted(telemetry['messages']),'invalid_frames':telemetry['invalid_frames']}))
""", timeout=20)
        record = json.loads(output)
        if args.expected_policy and record['identity_auth']['policy'] != args.expected_policy:
            raise ValueError('actual authentication policy differs from expected policy')
        print(json.dumps(record), flush=True)
        image = json.loads(run(cloud, 'docker inspect uav-cloud-api'))[0]['Config']['Image']
        command = ('docker run --rm -i --network host --read-only --cap-drop ALL --security-opt no-new-privileges '
                   '--tmpfs /tmp:rw,noexec,nosuid,size=16m -v ' + shlex.quote(BASE + '/control/keys/root.key:/root.key:ro') +
                   ' -v ' + shlex.quote(BASE + '/control/state/api:/auth-state:ro') +
                   ' -v ' + shlex.quote(BASE + '/tls/ca.crt:/ca.crt:ro') +
                   ' --entrypoint python ' + shlex.quote(image) + ' -')
        proof = run(cloud, command, data="""import base64,json,sqlite3,ssl,urllib.error,urllib.request
from pathlib import Path
from services.crypto_service.repository.crypto import CounterRepository
from shared.communication.envelope import EnvelopeCodec,canonical
with sqlite3.connect('file:/auth-state/auth.db?mode=ro',uri=True) as db:
 row=db.execute("SELECT transcript FROM auth_sessions WHERE state='active' ORDER BY rowid DESC LIMIT 1").fetchone()
transcript=json.loads(row[0])
codec=EnvelopeCodec(device_id=transcript['device_id'],key_id=transcript['key_id'],root_key=Path('/root.key').read_bytes(),
                    role='edge',counters=CounterRepository('/tmp/attacker.db'),epochs={transcript['epoch']:transcript['suite']})
context=ssl.create_default_context(cafile='/ca.crt')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
results={}
for case, session, signed in [('legacy_root_key_only',None,False),('observed_session_id',transcript['auth_session_id'],False),('forged_identity_proof',transcript['auth_session_id'],True)]:
 message=codec.seal('task.claim',{},epoch=transcript['epoch'],auth_session_id=session)
 if signed: message['identity_proof']=base64.b64encode(bytes(64)).decode()
 request=urllib.request.Request('https://localhost:8443/api/edge/exchange',canonical(message),{'Content-Type':'application/json'})
 try:
  opener.open(request,timeout=10)
  raise AssertionError('unauthenticated business accepted')
 except urllib.error.HTTPError as error:
  assert error.code==400,case
  results[case]=error.code
print(json.dumps(results))
""", timeout=40)
        record['negative_tests'] = json.loads(proof)
        (ROOT / 'docs/evidence/deploy/identity-live-verification.json').write_text(json.dumps(record, indent=2) + '\n')
        print('Root-key-only, observed-session and forged-identity requests all rejected.', flush=True)
    finally:
        cloud.close()


if __name__ == '__main__':
    main()

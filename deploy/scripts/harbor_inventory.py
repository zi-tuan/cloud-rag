"""Read Harbor metadata over SSH; the admin credential is never sent over public HTTP."""
import base64
import http.client
import json
import os
import pathlib
import paramiko

c=paramiko.SSHClient()
c.load_host_keys(str(pathlib.Path.home()/'.ssh/known_hosts'))
c.connect('47.109.49.98',port=24531,username='uav',key_filename=str(pathlib.Path.home()/'.ssh/id_ed25519'),timeout=15)
auth=base64.b64encode(('admin:'+os.environ['HARBOR_ADMIN_PASSWORD']).encode()).decode()
def get(path):
    conn=http.client.HTTPConnection('127.0.0.1',30881,timeout=20)
    conn.sock=c.get_transport().open_channel('direct-tcpip',('127.0.0.1',30881),('127.0.0.1',0))
    conn.request('GET',path,headers={'Authorization':'Basic '+auth})
    res=conn.getresponse()
    body=res.read(); conn.close()
    if res.status!=200: raise RuntimeError('Harbor HTTP '+str(res.status))
    return json.loads(body)
try:
    for p in get('/api/v2.0/projects?page_size=100'):
        print('Project:',p['name'],'repositories:',p['repo_count'])
        for r in get('/api/v2.0/projects/'+p['name']+'/repositories?page_size=100'):
            print('  Repository:',r['name'],'artifacts:',r['artifact_count'])
finally:
    c.close()

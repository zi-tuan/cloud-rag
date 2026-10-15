"""Fetch a Docker Hub ARM64 image into a verified OCI archive for offline import."""
import argparse
import concurrent.futures
import hashlib
import io
import json
import pathlib
import tarfile
import urllib.request

p=argparse.ArgumentParser()
p.add_argument('repository')
p.add_argument('reference')
p.add_argument('output')
p.add_argument('--architecture', choices=['arm64', 'amd64'], default='arm64')
a=p.parse_args()
base='https://registry-1.docker.io/v2/'+a.repository
token=json.load(urllib.request.urlopen('https://auth.docker.io/token?service=registry.docker.io&scope=repository:'+a.repository+':pull',timeout=30))['token']
headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}

def get(path):
    return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers),timeout=120).read()

manifest=get('/manifests/'+a.reference)
data=json.loads(manifest)
if 'manifests' in data:
    descriptor=next(x for x in data['manifests'] if x.get('platform',{}).get('architecture')==a.architecture and x.get('platform',{}).get('os')=='linux')
    manifest=get('/manifests/'+descriptor['digest'])
    assert 'sha256:'+hashlib.sha256(manifest).hexdigest()==descriptor['digest']
    data=json.loads(manifest)
digest='sha256:'+hashlib.sha256(manifest).hexdigest()
if a.reference.startswith('sha256:'):
    assert digest==a.reference
out=pathlib.Path(a.output)
cache=out.parent/'oci-blobs'
cache.mkdir(parents=True,exist_ok=True)

def blob(desc):
    name=desc['digest'].split(':',1)[1]
    path=cache/name
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=name:
        payload=get('/blobs/'+desc['digest'])
        if hashlib.sha256(payload).hexdigest()!=name:
            raise RuntimeError('Blob checksum mismatch')
        path.write_bytes(payload)
    print('Verified blob',name[:12],path.stat().st_size,flush=True)
    return path

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    paths=list(pool.map(blob,[data['config']]+data['layers']))
ref='docker.io/'+a.repository+':'+(a.reference if not a.reference.startswith('sha256:') else 'uav-pinned')
index={'schemaVersion':2,'manifests':[{'mediaType':data['mediaType'],'digest':digest,'size':len(manifest),'annotations':{'io.containerd.image.name':ref,'org.opencontainers.image.ref.name':ref}}]}
with tarfile.open(out,'w') as tar:
    for name,payload in [('oci-layout',b'{"imageLayoutVersion":"1.0.0"}'),('index.json',json.dumps(index).encode()),('blobs/sha256/'+digest.split(':')[1],manifest)]:
        info=tarfile.TarInfo(name); info.size=len(payload); tar.addfile(info,io.BytesIO(payload))
    for path in paths:
        tar.add(path,arcname='blobs/sha256/'+path.name)
print('OCI archive:',out,'image:',ref,'digest:',digest)

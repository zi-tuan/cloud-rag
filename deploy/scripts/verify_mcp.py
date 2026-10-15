"""Verify MCP initialization, tool discovery, and a real namespaced read."""
import json
import select
import subprocess
import time

p=subprocess.Popen(['/home/uav/uav-agent-mvp/bin/kubernetes-mcp-server','--kubeconfig','/home/uav/uav-agent-mvp/mcp/kubeconfig','--read-only','--disable-multi-cluster','--toolsets','core'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,bufsize=1)

def send(payload):
    p.stdin.write(json.dumps(payload)+'\n'); p.stdin.flush()

def call(i,method,params):
    send({'jsonrpc':'2.0','id':i,'method':method,'params':params})
    deadline=time.time()+30
    while time.time()<deadline:
        ready,_,_=select.select([p.stdout,p.stderr],[],[],1)
        for stream in ready:
            line=stream.readline()
            if not line: raise RuntimeError('MCP process exited')
            if stream is p.stderr: continue
            reply=json.loads(line)
            if reply.get('id')==i:
                if 'error' in reply: raise RuntimeError(str(reply['error']))
                return reply['result']
    raise RuntimeError('MCP response timeout')

try:
    print('Initialize:',call(1,'initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'uav-bootstrap-check','version':'1'}})['serverInfo'])
    send({'jsonrpc':'2.0','method':'notifications/initialized'})
    tools=call(2,'tools/list',{})['tools']
    print('Tools:',[x['name'] for x in tools])
    tool=next(x for x in tools if x['name']=='pods_list_in_namespace')
    result=call(3,'tools/call',{'name':tool['name'],'arguments':{'namespace':'uav-mvp'}})
    if result.get('isError'):
        raise RuntimeError('MCP namespaced read failed: '+json.dumps(result))
    print('Real cluster result:',json.dumps(result,ensure_ascii=False))
finally:
    p.terminate()
    p.wait(timeout=5)

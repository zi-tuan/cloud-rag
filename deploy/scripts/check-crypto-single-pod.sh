#!/bin/bash
set -eu
cfg=/home/uav/.kube/config
kubectl --kubeconfig "$cfg" get deployments aes-service sm4-service -n uav-mvp -o json |
python3 -c 'import json,sys
items=json.load(sys.stdin)["items"]
counts={item["metadata"]["name"]:item["spec"]["replicas"] for item in items}
if set(counts)!={"aes-service","sm4-service"} or sorted(counts.values()) != [0,1]:
    raise SystemExit("expected exactly one active crypto Deployment: "+str(counts))
print("crypto deployment replicas:",counts)'
kubectl --kubeconfig "$cfg" get pods -n uav-mvp -l 'app in (aes-service,sm4-service)' -o json |
python3 -c 'import json,sys
items=json.load(sys.stdin)["items"]
active=[item for item in items if item.get("status",{}).get("phase") not in ("Succeeded","Failed")]
if len(active)!=1 or active[0].get("metadata",{}).get("deletionTimestamp"):
    raise SystemExit("expected exactly one nonterminating crypto Pod: "+str([x["metadata"]["name"] for x in active]))
pod=active[0]
if pod.get("status",{}).get("phase")!="Running" or not any(c.get("type")=="Ready" and c.get("status")=="True" for c in pod.get("status",{}).get("conditions",[])):
    raise SystemExit("current crypto Pod is not Ready")
print("ready crypto Pod:",pod["metadata"]["name"])'

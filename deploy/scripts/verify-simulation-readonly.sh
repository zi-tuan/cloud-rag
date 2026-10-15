#!/bin/bash
set -eu
python3 - <<'PY'
import collections,json,socket,time
from pymavlink.dialects.v20 import ardupilotmega as mavlink
parser=mavlink.MAVLink(None)
parser.robust_parsing=True
counts=collections.Counter()
sources=set()
with socket.create_connection(('192.168.8.110',5763),timeout=5) as stream:
    stream.settimeout(2)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        try:
            data=stream.recv(4096)
        except socket.timeout:
            continue
        if not data:
            break
        for message in parser.parse_buffer(data) or []:
            counts[message.get_type()]+=1
            if message.get_type()!='BAD_DATA':
                sources.add((message.get_srcSystem(),message.get_srcComponent()))
        if counts['HEARTBEAT'] and counts['GLOBAL_POSITION_INT']:
            break
print(json.dumps({'endpoint':'192.168.8.110:5763','message_counts':dict(counts),'sources':sorted(sources),'application_bytes_sent':0}))
PY

"""Verify live localhost frontend -> cloud -> Jetson -> simulator telemetry."""
import json,time,urllib.request
url='http://localhost:8080/edgeAgentApi/api/overview'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(url,timeout=10) as response:
 data=json.load(response)
device=data['device']; status=device.get('status') or {}; telemetry=status.get('telemetry') or {}
assert device['online'] and time.time()-device['last_seen']<20, 'Jetson heartbeat is stale'
assert telemetry.get('online'), 'Flight heartbeat is missing'
assert telemetry.get('read_only') is True, 'Flight data must be collected read-only'
assert telemetry.get('invalid_frames')==0, 'MAVLink checksum errors'
required={'HEARTBEAT','GLOBAL_POSITION_INT','ATTITUDE'}
assert required.issubset(telemetry.get('messages',{})), 'Position or attitude data is missing'
print(json.dumps({'frontend_http':200,'device':device['device_id'],'device_online':True,
 'flight_online':True,'received_messages':telemetry['received_messages'],
 'messages':sorted(telemetry['messages']),'invalid_frames':telemetry['invalid_frames']}))

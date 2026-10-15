#!/bin/bash
set -eu
python3 - <<'PY'
import glob,re
for path in glob.glob('/etc/nginx/sites-enabled/*')+glob.glob('/etc/nginx/conf.d/*.conf'):
    try:
        lines=open(path).read().splitlines()
    except OSError:
        continue
    print('File:',path)
    for line in lines:
        if re.match(r'\s*(listen|server_name|location|proxy_pass)\b',line):
            print(re.sub(r'://[^/@\s]+:[^/@\s]+@','://[REDACTED]@',line.strip()))
PY

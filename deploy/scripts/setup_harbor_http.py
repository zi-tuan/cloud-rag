"""创建监听 35443 的 Harbor HTTP 代理，不生成或挂载 TLS 证书。"""
import json
import os
from pathlib import Path
import socket
import subprocess


NGINX_CONFIG = r'''
worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 256; }
http {
    access_log off;
    client_body_temp_path /tmp/client_body;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    map $upstream_http_www_authenticate $uav_auth {
        "~^Bearer realm=\"[^\"]+\"(?<auth_tail>.*)$" 'Bearer realm="http://$http_host/service/token"$auth_tail';
        default $upstream_http_www_authenticate;
    }
    server {
        listen 0.0.0.0:35443;
        server_name _;
        client_max_body_size 0;
        location / {
            proxy_pass http://127.0.0.1:30881;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Proto http;
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            proxy_hide_header WWW-Authenticate;
            add_header WWW-Authenticate $uav_auth always;
            proxy_redirect ~^http://[^/]+/(.*)$ http://$http_host/$1;
        }
    }
}
'''


def main():
    os.umask(0o077)
    root = Path.home() / 'uav-agent-mvp/harbor-http'
    root.mkdir(mode=0o700, exist_ok=True)
    with socket.socket() as check:
        check.bind(('0.0.0.0', 35443))
    config = root / 'nginx.conf'
    config.write_text(NGINX_CONFIG)
    image = json.loads(subprocess.check_output(['docker', 'inspect', 'nginx']))[0]['Image']
    options = ['--network', 'host', '--user', f'{os.getuid()}:{os.getgid()}',
               '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
               '-v', str(config) + ':/etc/nginx/nginx.conf:ro', '--entrypoint', 'nginx']
    subprocess.run(['docker', 'run', '--rm', *options, image, '-t'], check=True)
    subprocess.run(['docker', 'run', '-d', '--name', 'uav-harbor-http', '--restart', 'unless-stopped',
                    '--log-opt', 'max-size=5m', '--log-opt', 'max-file=3', *options,
                    image, '-g', 'daemon off;'], check=True)
    print('Harbor HTTP listening on 0.0.0.0:35443')


if __name__ == '__main__':
    main()

"""Read-only upstream endpoint check on the 946/NPS server."""
import pathlib
import paramiko


client = paramiko.SSHClient()
client.load_host_keys(str(pathlib.Path.home() / '.ssh' / 'known_hosts'))
client.connect('47.109.49.98', port=24531, username='uav',
               key_filename=str(pathlib.Path.home() / '.ssh' / 'id_ed25519'),
               timeout=20, banner_timeout=30, auth_timeout=20)
try:
    command = r'''ss -lntp | grep -E '15443|8443' || true
docker ps --format '{{.Names}} {{.Status}}' | grep -E 'harbor|uav-cloud' || true
for p in 15443 8443; do timeout 4 curl --noproxy '*' -k -sS -o /dev/null -w "port=$p http=%{http_code}\n" "https://127.0.0.1:$p/v2/" || true; done'''
    _, stdout, stderr = client.exec_command(command, timeout=15)
    print(stdout.read().decode(errors='replace'), end='')
    print(stderr.read().decode(errors='replace'), end='')
finally:
    client.close()

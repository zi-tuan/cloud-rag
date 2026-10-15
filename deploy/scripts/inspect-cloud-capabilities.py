"""只读确认部署通道；配置凭据仅留在远端进程内存，不输出。"""
import base64
import json
import subprocess
import urllib.error
import urllib.request


def main():
    inspect = json.loads(subprocess.check_output(['docker', 'inspect', 'harbor-core']))[0]
    environment = dict(item.split('=', 1) for item in inspect['Config']['Env'] if '=' in item)
    password = environment.get('HARBOR_ADMIN_PASSWORD')
    print('harbor_configured_admin_credential_present=' + str(bool(password)).lower())
    if password:
        request = urllib.request.Request('http://127.0.0.1:30881/api/v2.0/projects?page_size=100',
                                         headers={'Authorization': 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=10) as response:
                projects = json.load(response)
                print('harbor_authenticated_read=true')
                print('harbor_projects=' + json.dumps([{'name': p['name'], 'repositories': p['repo_count']} for p in projects]))
        except urllib.error.HTTPError as exc:
            print('harbor_authenticated_read=false http_status=' + str(exc.code))
        except OSError as exc:
            print('harbor_authenticated_read=false error_type=' + type(exc).__name__)
    images = subprocess.check_output(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}} {{.ID}}']).decode().splitlines()
    print('python_or_uav_images=' + json.dumps([line for line in images if 'python' in line or 'uav-mvp' in line]))


if __name__ == '__main__':
    main()

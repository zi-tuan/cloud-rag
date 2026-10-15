"""修复本次尚未分发的初始 CA 重复扩展；保留原私钥和叶子证书。"""
import pathlib
import subprocess

root = pathlib.Path.home() / 'uav-agent-mvp/tls'
check = subprocess.run(['openssl', 'verify', '-CAfile', str(root / 'ca.crt'), str(root / 'loopback.crt')],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if check.returncode == 0:
    raise SystemExit('Certificate chain already valid; refusing unnecessary replacement')
config = root / 'ca.cnf'
config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ca\nprompt=no\n[dn]\nCN=UAV Phase1 Internal CA\n[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
subprocess.run(['openssl', 'req', '-new', '-x509', '-key', str(root / 'ca.key'), '-days', '730',
                '-config', str(config), '-out', str(root / 'ca.corrected.crt')], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['openssl', 'verify', '-CAfile', str(root / 'ca.corrected.crt'), str(root / 'loopback.crt')], check=True)
(root / 'ca.crt').rename(root / 'ca.invalid-initial.crt')
(root / 'ca.corrected.crt').rename(root / 'ca.crt')
print('Initial CA repaired with original key; existing service certificates unchanged')

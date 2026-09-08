"""Read-only AutoDL audit; password is prompted and never persisted."""
import getpass
import json
from pathlib import Path
import paramiko

client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / '.ssh' / 'known_hosts'))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect('connect.westd.seetacloud.com', port=37678, username='root',
               password=getpass.getpass('AutoDL password: '), look_for_keys=False,
               allow_agent=False, timeout=20)
command = """/root/miniconda3/bin/python - <<'PY'
import json, subprocess, pathlib
r={}
commands={'disk':['df','-hT','/','/root/autodl-tmp'],
'mount':['findmnt','-n','-o','TARGET,SOURCE,FSTYPE','-T','/root/autodl-tmp'],
'envs':['conda','env','list'],
'root_dirs':['find','/root','-maxdepth','2','-type','d','-not','-path','*/.*'],
'comfy_packages':['/root/miniconda3/envs/comfyui/bin/python','-m','pip','list','--format=json']}
for k,v in commands.items():
 try:
  p=subprocess.run(v,capture_output=True,text=True,timeout=30);r[k]=p.stdout+p.stderr
 except Exception as e:r[k]=str(e)
for f in ['memory.max','memory.current','cpu.max']:
 p=pathlib.Path('/sys/fs/cgroup')/f
 r[f]=p.read_text().strip() if p.exists() else 'unknown'
print(json.dumps(r))
PY"""
try:
    _, stdout, stderr = client.exec_command(command, timeout=90)
    raw = stdout.read().decode()
    if not raw.strip():
        raise RuntimeError(stderr.read().decode()[:1000])
    result = json.loads(raw)
    report = Path(__file__).parent / 'reports' / 'remote-audit.json'
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
finally:
    client.close()

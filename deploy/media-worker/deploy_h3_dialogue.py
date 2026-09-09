"""Deploy the tested dialogue adapter while the existing worker is idle."""

import getpass
import json
import shlex
import time
from datetime import datetime
from pathlib import Path

import paramiko

LOCAL = Path(__file__).resolve().parent
REMOTE = "/root/autodl-tmp/arcreel-worker"
stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
stage = REMOTE + "/tmp/h3-deploy-" + stamp
backup = REMOTE + "/backups/before-h3-dialogue-" + stamp
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect(
    "connect.westd.seetacloud.com",
    port=37678,
    username="root",
    password=getpass.getpass("AutoDL password: "),
    look_for_keys=False,
    allow_agent=False,
    timeout=20,
)


def run(command, timeout=180):
    _, out, err = client.exec_command(command, timeout=timeout)
    stdout, stderr = out.read().decode(), err.read().decode()
    status = out.channel.recv_exit_status()
    if stdout.strip():
        print(stdout.strip(), flush=True)
    if status:
        raise RuntimeError(f"Cloud command failed ({status}): {stderr[-4000:]}")
    return stdout


def py(code, env="gateway"):
    return run(f"{REMOTE}/envs/{env}/bin/python - <<'PY'\n{code}\nPY")


try:
    run("mkdir -p " + shlex.quote(stage))
    with client.open_sftp() as sftp:
        for name in ("cloud_gateway.py", "h3_prompt.py", "start_worker.sh"):
            sftp.put(str(LOCAL / name), stage + "/" + name)
        sftp.put(str(LOCAL / "outputs/smoke-20260908-172858/sample-preview.mp4"), stage + "/probe.mp4")
    run(f"{REMOTE}/tools/bin/uv pip install --python {REMOTE}/envs/gateway/bin/python PyYAML==6.0.3")
    py(f"""import ast, subprocess, json, py_compile
from pathlib import Path
stage=Path({stage!r})
for name in ('cloud_gateway.py','h3_prompt.py'): py_compile.compile(str(stage/name), doraise=True)
tree=ast.parse((stage/'cloud_gateway.py').read_text())
probe=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='VIDEO_PROBE' for t in n.targets))
p=subprocess.run([{(REMOTE + "/envs/video/bin/python")!r},'-c',probe,str(stage/'probe.mp4')],capture_output=True,text=True,check=True,timeout=90)
data=json.loads(p.stdout)
assert data['frames']==81 and data['has_audio'] and data['audio_peak']>1e-5
print(json.dumps({{'existing_media_probe':data}}))
""")
    py(f"""import urllib.request,json,sqlite3,shutil,os
from pathlib import Path
root=Path({REMOTE!r}); backup=Path({backup!r}); stage=Path({stage!r})
state=json.load(urllib.request.urlopen('http://127.0.0.1:9000/health/ready',timeout=10))
assert not state['busy'] and not state['queued'], 'Worker is busy; no deployment performed'
db=sqlite3.connect(root/'runtime/jobs.sqlite')
assert not db.execute("select id from jobs where status in ('running','queued')").fetchall()
backup.mkdir(mode=0o700,parents=True,exist_ok=False)
with sqlite3.connect(backup/'jobs.sqlite') as target: db.backup(target)
for name in ('cloud_gateway.py','h3_prompt.py','start_worker.sh'):
 p=root/'scripts'/name
 if p.exists(): shutil.copy2(p,backup/name)
(backup/'deployment.json').write_text(json.dumps({{'stage':str(stage),'backup':str(backup),'previous_health':state}}))
print(json.dumps({{'backup':str(backup),'idle':True}}))
""")
    run(f"bash {REMOTE}/scripts/stop_worker.sh")
    py(f"""import socket,time,os
from pathlib import Path
old_pid=int(Path({(REMOTE + "/runtime/gateway.pid")!r}).read_text())
for i in range(45):
    alive=True
    try: os.kill(old_pid,0)
    except ProcessLookupError: alive=False
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',9000))!=0 and not alive: break
    time.sleep(1)
else: raise RuntimeError('Old gateway did not stop; deployment aborted')
""")
    py(f"""import os, shutil
from pathlib import Path
stage=Path({stage!r}); root=Path({REMOTE!r})
for name in ('cloud_gateway.py','h3_prompt.py','start_worker.sh'):
 target=root/'scripts'/name; temporary=target.with_suffix(target.suffix+'.upload')
 temporary.write_bytes((stage/name).read_bytes().replace(b'\\r\\n',b'\\n'))
 temporary.chmod(0o700 if name.endswith('.sh') else 0o600)
 temporary.replace(target)
""")
    run(f"bash {REMOTE}/scripts/start_worker.sh --authorize-gpu", timeout=180)
    py("""import time,urllib.request,json
for i in range(30):
 try:
  result=json.load(urllib.request.urlopen('http://127.0.0.1:9000/health/ready',timeout=3))
  assert result['ready']; print(json.dumps(result));break
 except Exception:
  time.sleep(1)
else: raise RuntimeError('Deployed gateway not ready')
""")
    py(f"""import sys,json
sys.path.insert(0,{(REMOTE + "/scripts")!r})
import cloud_gateway, yaml
assert cloud_gateway.h3_prepared()
print(json.dumps({{'h3_prepared':True,'yaml_version':yaml.__version__,'models':list(cloud_gateway.VIDEO_MODELS)}}))
""")
    report = {"deployed_at": time.time(), "backup": backup, "stage": stage, "gpu_inference_submitted": False}
    (LOCAL / "reports/h3-dialogue-deploy.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
finally:
    client.close()

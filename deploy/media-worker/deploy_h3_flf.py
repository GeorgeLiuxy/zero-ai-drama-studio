"""Deploy first/last H3 support without enabling GPU work or replacing job history."""

import getpass
import json
from datetime import datetime
from pathlib import Path

import paramiko

LOCAL = Path(__file__).resolve().parent
REMOTE = "/root/autodl-tmp/arcreel-worker"
FILES = {
    "cloud_gateway.py": "scripts/cloud_gateway.py",
    "h3_prompt.py": "scripts/h3_prompt.py",
    "preflight_h3_flf.py": "scripts/preflight_h3_flf.py",
    "start_worker.sh": "scripts/start_worker.sh",
    "h3-flf-api.json": "workflows/h3-flf-api.json",
}


def main():
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    stage = REMOTE + "/tmp/h3-flf-deploy-" + stamp
    backup = REMOTE + "/backups/before-h3-flf-" + stamp
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

    def run(command, timeout=120):
        _, out, err = client.exec_command(command, timeout=timeout)
        stdout, stderr = out.read().decode(), err.read().decode()
        status = out.channel.recv_exit_status()
        if stdout.strip():
            print(stdout.strip(), flush=True)
        if status:
            raise RuntimeError(f"Cloud command failed ({status}): {stderr[-2500:]}")
        return stdout

    def py(code):
        return run(f"{REMOTE}/envs/gateway/bin/python - <<'PY'\n{code}\nPY")

    try:
        run(f"mkdir -p {stage}")
        with client.open_sftp() as sftp:
            for name in FILES:
                sftp.put(str(LOCAL / name), stage + "/" + name)
        py(f"""import json,py_compile,socket,sqlite3,urllib.request,shutil
from pathlib import Path
root=Path({REMOTE!r}); stage=Path({stage!r}); backup=Path({backup!r}); files={FILES!r}
for name in files:
 if name.endswith('.py'): py_compile.compile(str(stage/name),doraise=True)
with socket.socket() as s: running=s.connect_ex(('127.0.0.1',9000))==0
if running:
 state=json.load(urllib.request.urlopen('http://127.0.0.1:9000/health/ready',timeout=10))
 assert not state['busy'] and not state['queued'] and not state['gpu_authorized'], 'Deploy only to idle CPU mode'
with sqlite3.connect(root/'runtime/jobs.sqlite') as db:
 assert not db.execute("select id from jobs where status in ('queued','running')").fetchall()
 backup.mkdir(parents=True,mode=0o700,exist_ok=False)
 with sqlite3.connect(backup/'jobs.sqlite') as destination: db.backup(destination)
manifest={{'previous_gateway_running':running,'files':{{}}}}
for name,target in files.items():
 source=root/target; manifest['files'][target]=source.exists()
 if source.exists(): shutil.copy2(source,backup/name)
(backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({{'backup':str(backup),'idle':True,'previous_gateway_running':running}}))
""")
        py(f"""import json,subprocess,socket,time
from pathlib import Path
root=Path({REMOTE!r}); manifest=json.loads(Path({(backup + "/manifest.json")!r}).read_text())
if manifest['previous_gateway_running']:
 subprocess.run(['bash',str(root/'scripts/stop_worker.sh')],check=True)
 for _ in range(45):
  with socket.socket() as s:
   if s.connect_ex(('127.0.0.1',9000))!=0: break
  time.sleep(1)
 else: raise RuntimeError('Gateway did not stop')
""")
        py(f"""from pathlib import Path
root=Path({REMOTE!r}); stage=Path({stage!r})
for name,target in {FILES!r}.items():
 p=root/target; temporary=p.with_suffix(p.suffix+'.upload')
 temporary.write_bytes((stage/name).read_bytes().replace(b'\\r\\n',b'\\n'))
 temporary.chmod(0o700 if name.endswith('.sh') else 0o600); temporary.replace(p)
""")
        try:
            run(f"{REMOTE}/envs/gateway/bin/python {REMOTE}/scripts/preflight_h3_flf.py")
        except Exception:
            py(f"""import json,shutil
from pathlib import Path
root=Path({REMOTE!r}); backup=Path({backup!r}); manifest=json.loads((backup/'manifest.json').read_text())
for name,target in {FILES!r}.items():
 if manifest['files'][target]: shutil.copy2(backup/name,root/target)
 else: (root/target).unlink(missing_ok=True)
print('Preflight failed; restored prior code and workflows')
""")
            raise
        run(f"bash {REMOTE}/scripts/start_worker.sh --prepare-only")
        py("""import json,time,urllib.request
for _ in range(30):
 try:
  state=json.load(urllib.request.urlopen('http://127.0.0.1:9000/health/ready',timeout=3))
  assert not state['gpu_authorized'] and not state['busy'] and not state['queued']
  print(json.dumps(state)); break
 except (OSError,ValueError): time.sleep(1)
else: raise RuntimeError('CPU gateway unavailable')
""")
        with client.open_sftp() as sftp, sftp.open(REMOTE + "/reports/h3-flf-preflight.json") as f:
            report = json.loads(f.read())
        report.update(
            deployed_at=datetime.now().astimezone().isoformat(),
            backup=backup,
            gateway_mode="prepare-only",
            gpu_tested=False,
        )
        (LOCAL / "reports").mkdir(exist_ok=True)
        (LOCAL / "reports/h3-flf-deploy.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps({"deployed": True, "gpu_mode": False, "backup": backup}),
            flush=True,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()

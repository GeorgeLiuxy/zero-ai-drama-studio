"""Sync final deployment sources and read compact cloud preparation status."""
import getpass
import json
from pathlib import Path

import paramiko

local = Path(__file__).resolve().parent
remote = "/root/autodl-tmp/arcreel-worker"
client = paramiko.SSHClient()
client.load_host_keys(str(Path.home() / ".ssh" / "known_hosts"))
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect("connect.westd.seetacloud.com", port=37678, username="root", password=getpass.getpass("AutoDL password: "), look_for_keys=False, allow_agent=False, timeout=20)
try:
    sftp = client.open_sftp()
    for source, target in {
        "cloud_gateway.py": "scripts/cloud_gateway.py",
        "cloud_tts.py": "scripts/cloud_tts.py",
        "remote_finish_prepare.py": "scripts/finish_prepare.py",
        "prepare_demo_voice.py": "scripts/prepare_demo_voice.py",
        "preflight_cloud.py": "scripts/preflight_cloud.py",
        "tts-requirements.txt": "manifests/tts-requirements.txt",
        "start_worker.sh": "scripts/start_worker.sh",
        "remote_install.py": "scripts/install.py",
        "build-constraints.txt": "manifests/build-constraints.txt",
        "prepare_text_resources.py": "scripts/prepare_text_resources.py",
        "offline_text.py": "scripts/offline_text.py",
        "wan-api.json": "workflows/wan-api.json",
        "README_OPERATIONS.md": "README_OPERATIONS.md",
        "reports/GPU_TEST_PLAN.md": "reports/GPU_TEST_PLAN.md",
    }.items():
        sftp.put(str(local / source), remote + "/" + target)
    for source, target in {
        "reports/preflight.json": "reports/cloud-preflight.json",
        "reports/preparation-status.json": "reports/cloud-installation.json",
        "reports/demo-voice.json": "reports/cloud-demo-voice.json",
        "manifests/environment.lock.json": "reports/cloud-environment.lock.json",
        "manifests/wetext-models.json": "reports/cloud-wetext-manifest.json",
        "reports/text-resources.json": "reports/cloud-text-resources.json",
    }.items():
        try:
            sftp.stat(remote + "/" + source)
        except FileNotFoundError:
            continue
        sftp.get(remote + "/" + source, str(local / target))
    sftp.close()
    code = """import json,pathlib,time
p=pathlib.Path('/root/autodl-tmp/arcreel-worker')
r={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'files':[]}
for manifest,base in [('wan-models.json',p/'models'),('cosyvoice-models.json',p/'models/cosyvoice3')]:
 for f in json.loads((p/'manifests'/manifest).read_text()):
  name=f['Path'].removeprefix('split_files/') if manifest=='wan-models.json' else f['Path']
  dest=base/name;partial=dest.with_name(dest.name+'.part')
  actual=dest.stat().st_size if dest.exists() else partial.stat().st_size if partial.exists() else 0
  r['files'].append({'name':name,'expected':f['Size'],'actual':actual,'renamed_after_hash_check':dest.exists()})
for name in ['preparation-status.json','models-ready.json']:
 f=p/'reports'/name
 if f.exists():r[name]=json.loads(f.read_text())
r['memory_events']=(pathlib.Path('/sys/fs/cgroup/memory.events')).read_text()
print(json.dumps(r))
"""
    _, stdout, stderr = client.exec_command("/root/miniconda3/bin/python - <<'PY'\n" + code + "\nPY")
    status = json.loads(stdout.read())
    (local / "reports" / "cloud-progress.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))
finally:
    client.close()

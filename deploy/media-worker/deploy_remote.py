"""Upload only deployment files; start one bounded CPU-only preparation job."""
import getpass,json,shlex
from pathlib import Path
import paramiko
ROOT=Path(__file__).resolve().parent
remote='/root/autodl-tmp/arcreel-worker'
c=paramiko.SSHClient();c.load_host_keys(str(Path.home()/'.ssh'/'known_hosts'))
c.set_missing_host_key_policy(paramiko.RejectPolicy())
c.connect('connect.westd.seetacloud.com',port=37678,username='root',password=getpass.getpass('AutoDL password: '),look_for_keys=False,allow_agent=False,timeout=20)
try:
    cmd='mkdir -p '+ ' '.join(shlex.quote(remote+'/'+d) for d in ['scripts','manifests','workflows','logs','runtime','reports'])
    _,out,err=c.exec_command(cmd)
    if out.channel.recv_exit_status()!=0:raise RuntimeError(err.read().decode())
    s=c.open_sftp()
    for source,destination in [('remote_prepare.py','scripts/prepare.py'),('source-lock.json','manifests/source-lock.json'),('wan-models.json','manifests/wan-models.json'),('wan-ui.json','workflows/wan-ui.json')]:
        s.put(str(ROOT/source),remote+'/'+destination)
    s.close()
    cmd=f"flock -n {remote}/runtime/prepare.lock -c '/root/miniconda3/bin/python -u {remote}/scripts/prepare.py'"
    _,out,err=c.exec_command(f"nohup {cmd} > {remote}/logs/prepare.log 2>&1 < /dev/null & echo $!")
    print('Preparation process:',out.read().decode().strip())
finally:c.close()

"""Queue cloud TTS preparation behind the existing Wan preparation lock."""
import getpass,shlex
from pathlib import Path
import paramiko
p=Path(__file__).resolve().parent
r='/root/autodl-tmp/arcreel-worker'
c=paramiko.SSHClient();c.load_host_keys(str(Path.home()/'.ssh'/'known_hosts'))
c.set_missing_host_key_policy(paramiko.RejectPolicy())
c.connect('connect.westd.seetacloud.com',port=37678,username='root',password=getpass.getpass('AutoDL password: '),look_for_keys=False,allow_agent=False,timeout=20)
try:
    s=c.open_sftp()
    files={'remote_finish_prepare.py':'scripts/finish_prepare.py','cosyvoice-source.zip':'cache/cosyvoice-source.zip',
           'matcha-source.zip':'cache/matcha-source.zip','reports/cosyvoice-model-manifest.json':'manifests/cosyvoice-models.json',
           'tts-requirements.txt':'manifests/tts-requirements.txt','tts-constraints.txt':'manifests/tts-constraints.txt',
           'video-constraints.txt':'manifests/video-constraints.txt','build-constraints.txt':'manifests/build-constraints.txt'}
    for a,b in files.items():s.put(str(p/a),r+'/'+b)
    s.close()
    command=f"flock -n {r}/runtime/finish.lock flock {r}/runtime/prepare.lock /root/miniconda3/bin/python -u {r}/scripts/finish_prepare.py"
    _,out,err=c.exec_command(f'nohup {command} > {r}/logs/finish_prepare.log 2>&1 < /dev/null & echo $!')
    print('Queued cloud TTS preparation:',out.read().decode().strip())
finally:c.close()

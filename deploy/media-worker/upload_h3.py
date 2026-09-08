"""Upload only explicitly named H3 artifacts; credentials stay in memory."""
import getpass
from pathlib import Path
import paramiko

local = Path(__file__).resolve().parent
root = '/root/autodl-tmp/arcreel-worker'
client = paramiko.SSHClient()
client.load_system_host_keys()
client.connect('connect.westd.seetacloud.com', port=37678, username='root',
               password=getpass.getpass('AutoDL password: '), look_for_keys=False, allow_agent=False)
with client.open_sftp() as sftp:
    for name, dest in [('h3-source.json', 'manifests/h3-source.json'), ('h3-ui.json', 'workflows/h3-upstream-ui.json'),
                       ('prepare_h3.py', 'scripts/prepare_h3.py'), ('h3-api.json', 'workflows/h3-api.json'),
                       ('preflight_h3.py', 'scripts/preflight_h3.py'),
                       ('H3-LICENSE.txt', 'manifests/H3-LICENSE.txt'),
                       ('h3-license-source.json', 'manifests/h3-license-source.json'),
                       ('H3_PREPARATION.md', 'reports/H3_PREPARATION.md')]:
        if (local/name).exists():
            sftp.put(str(local/name), root+'/'+dest+'.upload')
            sftp.posix_rename(root+'/'+dest+'.upload', root+'/'+dest)
            print('UPLOADED', dest)
client.close()

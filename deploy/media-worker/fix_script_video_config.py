"""Configure the prepared I2V model and verify script duration resolution without inference."""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent / 'ArcReel'
sys.path.insert(0, str(ARC))
from dotenv import dotenv_values
from local_connection import configure_local_connection

configure_local_connection()
settings = dotenv_values(ARC / '.env')
base = 'http://127.0.0.1:1241/api/v1'
login = urllib.parse.urlencode({'username': settings.get('AUTH_USERNAME') or 'admin',
                               'password': settings.get('AUTH_PASSWORD') or ''}).encode()
with urllib.request.urlopen(base + '/auth/token', data=login, timeout=10) as response:
    token = json.load(response)['access_token']

def api(path, method='GET', body=None):
    req = urllib.request.Request(base + path, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

provider = api('/custom-providers/1')
assert provider['base_url'] == 'http://127.0.0.1:19000'
config = api('/system/config')['settings']
keys = ['default_video_backend', 'default_video_backend_i2v']
backup = {'models': provider['models'], 'settings': {k: config.get(k, '') for k in keys}}
(ROOT / 'backups' / ('before-script-video-config-' + time.strftime('%Y%m%d-%H%M%S') + '.json')).write_text(
    json.dumps(backup, indent=2, ensure_ascii=False), encoding='utf-8')
fields = {'model_id', 'display_name', 'endpoint', 'is_default', 'is_enabled', 'price_unit',
          'price_input', 'price_output', 'currency', 'supported_durations', 'resolution', 'capability_overrides'}
models = [{k: v for k, v in m.items() if k in fields} for m in provider['models']]
video = next(m for m in models if m['model_id'] == 'wan22-i2v-fp8')
video.update(is_enabled=True, is_default=True, supported_durations=[3, 5])
api('/custom-providers/1/models', 'PUT', {'models': models})
api('/system/config', 'PATCH', {k: 'custom-1/wan22-i2v-fp8' for k in keys})

from lib.script_generator import ScriptGenerator

reports = []
for path in (ARC / 'deploy/projects').glob('*/project.json'):
    project = json.loads(path.read_text(encoding='utf-8'))
    if project.get('generation_mode') != 'storyboard':
        continue
    caps = api('/projects/' + urllib.parse.quote(path.parent.name, safe='') + '/video-capabilities')
    generator = object.__new__(ScriptGenerator)
    generator.project_json = project
    durations = generator._resolve_raw_supported_durations(caps)
    assert durations == [3, 5], durations
    reports.append({'project': path.parent.name, 'supported_durations': durations,
                    'provider_id': caps.get('provider_id'), 'model': caps.get('model')})
assert reports
report = {'passed': True, 'checks': reports, 'gpu_inference_submitted': False,
          'model_inference_validated': False, 'audio_configuration_unchanged': True}
(ROOT / 'reports/script-video-config-fix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))

"""CPU-only node/schema check. Does not submit prompts or load model weights."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

root = Path('/root/autodl-tmp/arcreel-worker')
port = 18189
env = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
           HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
with socket.socket() as s:
    assert s.connect_ex(('127.0.0.1', port)) != 0, 'Diagnostic port already occupied'
report = {'gpu_tested': False, 'model_inference_tested': False}
with (root / 'logs/h3-schema-check.log').open('ab') as log:
    process = subprocess.Popen([str(root/'envs/video/bin/python'), str(root/'repos/ComfyUI/main.py'),
                               '--cpu', '--listen', '127.0.0.1', '--port', str(port),
                               '--disable-auto-launch', '--disable-all-custom-nodes'],
                              cwd=root/'repos/ComfyUI', env=env, stdout=log, stderr=log)
try:
    info = None
    for _ in range(90):
        if process.poll() is not None:
            raise RuntimeError('CPU schema service failed; inspect h3-schema-check.log')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/object_info', timeout=3) as response:
                info = json.load(response)
            break
        except (OSError, ValueError):
            time.sleep(1)
    assert info is not None, 'Schema service timeout'
    workflow = json.loads((root/'workflows/h3-api.json').read_text())
    needed = {n['class_type'] for n in workflow.values()}
    missing = sorted(needed - info.keys())
    assert not missing, f'Missing native nodes: {missing}'
    errors = []
    for nid, n in workflow.items():
        schema = info[n['class_type']]
        for key in schema['input'].get('required', {}):
            if key not in n['inputs']:
                errors.append(f'{nid}: missing required input {key}')
        for name, value in n['inputs'].items():
            if isinstance(value, list):
                source = info[workflow[value[0]]['class_type']]
                if not 0 <= value[1] < len(source['output']):
                    errors.append(f'{nid}: invalid output link {name}')
    assert not errors, errors
    (root/'reports/h3-node-schemas.json').write_text(json.dumps({k:info[k] for k in needed}, indent=2))
    report.update(node_schema_passed=True, nodes=sorted(needed), node_count=len(workflow),
                  workflow='workflows/h3-api.json', checked_at=time.time())
    print(json.dumps(report), flush=True)
except Exception as exc:
    report.update(node_schema_passed=False, error=str(exc))
    raise
finally:
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    (root/'reports/h3-environment.json').write_text(json.dumps(report, indent=2))

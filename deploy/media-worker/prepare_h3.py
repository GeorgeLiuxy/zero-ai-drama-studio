"""Sequential, resumable H3 preparation. Never imports torch or executes inference."""
import fcntl
import hashlib
import json
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path('/root/autodl-tmp/arcreel-worker')


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    tmp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    guard = (ROOT / 'runtime/h3-download.lock').open('a')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    source = json.loads((ROOT / 'manifests/h3-source.json').read_text(encoding='utf-8-sig'))
    url = 'https://modelscope.cn/api/v1/models/Comfy-Org/MiniMax-H3/repo/files?Recursive=true'
    with urllib.request.urlopen(url, timeout=30) as response:
        mirror = {f['Path']: f for f in json.load(response)['Data']['Files']}
    manifest = []
    for f in source['files']:
        m = mirror[f['path']]
        assert m['Sha256'] == f['lfs']['oid'] and m['Size'] == f['size'], 'Mirror differs from pinned official file'
        manifest.append(dict(path=f['path'], size=f['size'], sha256=f['lfs']['oid'],
                             hf_revision=source['revision'], mirror_revision=m['Revision'],
                             license='https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE'))
    save(ROOT / 'manifests/h3-models.json', manifest)
    space = shutil.disk_usage(ROOT)
    reserve = max(30 * 1024**3, int(space.total * .15))
    remaining = sum(max(0, f['size'] - ((ROOT/'models'/f['path']).stat().st_size if (ROOT/'models'/f['path']).exists() else 0)) for f in manifest)
    assert space.free >= remaining + reserve, 'Insufficient disk reserve'
    report = dict(status='downloading', started_at=time.time(), gpu_tested=False,
                  official_hashes_matched=True, total_bytes=sum(f['size'] for f in manifest), files=[])
    report_path = ROOT / 'reports/h3-preparation.json'
    save(report_path, report)
    try:
        for f in manifest:
            p = ROOT / 'models' / f['path']
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                assert p.stat().st_size == f['size'] and digest(p) == f['sha256'], 'Existing file differs; not overwriting'
            else:
                partial = p.with_name(p.name + '.part')
                query = urllib.parse.urlencode({'Revision': f['mirror_revision'], 'FilePath': f['path']})
                download = 'https://modelscope.cn/api/v1/models/Comfy-Org/MiniMax-H3/repo?' + query
                report.update(current_file=f['path'], status='downloading')
                save(report_path, report)
                print('DOWNLOAD', f['path'], flush=True)
                subprocess.run(['curl', '-4', '-fL', '--silent', '--show-error', '--connect-timeout', '30',
                                '--speed-time', '120', '--speed-limit', '1024', '--retry', '3',
                                '--retry-delay', '5', '-C', '-', download, '-o', str(partial)], check=True)
                report['status'] = 'verifying'
                save(report_path, report)
                assert partial.stat().st_size == f['size'], 'Size mismatch'
                actual = digest(partial)
                assert actual == f['sha256'], 'SHA256 mismatch'
                partial.replace(p)
            report['files'].append(dict(path=f['path'], bytes=f['size'], sha256=f['sha256'], verified=True))
            save(report_path, report)
            print('VERIFIED', f['path'], flush=True)
        report.update(status='download_verified', finished_at=time.time(), current_file=None)
        save(report_path, report)
        print('H3_DOWNLOAD_VERIFIED', flush=True)
    except Exception as exc:
        report.update(status='failed', error=str(exc), updated_at=time.time())
        save(report_path, report)
        raise


if __name__ == '__main__':
    main()

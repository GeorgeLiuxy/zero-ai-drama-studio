"""Deploy tested gateway files and register disabled ArcReel model entries."""

import getpass
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import paramiko

P = Path(__file__).resolve().parent
R = "/root/autodl-tmp/arcreel-worker"
c = paramiko.SSHClient()
c.load_host_keys(str(Path.home() / ".ssh" / "known_hosts"))
c.set_missing_host_key_policy(paramiko.RejectPolicy())
c.connect(
    "connect.westd.seetacloud.com",
    port=37678,
    username="root",
    password=getpass.getpass("AutoDL password: "),
    look_for_keys=False,
    allow_agent=False,
    timeout=20,
)
try:
    _, out, err = c.exec_command("mkdir -p " + R + "/config")
    assert out.channel.recv_exit_status() == 0
    s = c.open_sftp()
    for source, target in [
        ("cloud_gateway.py", "scripts/cloud_gateway.py"),
        ("cloud_tts.py", "scripts/cloud_tts.py"),
        ("gpu_probe.py", "scripts/gpu_probe.py"),
        ("start_worker.sh", "scripts/start_worker.sh"),
        ("stop_worker.sh", "scripts/stop_worker.sh"),
        ("wan-api.json", "workflows/wan-api.json"),
        ("remote_finish_prepare.py", "scripts/finish_prepare.py"),
    ]:
        s.put(str(P / source), R + "/" + target)
        if source.endswith(".sh"):
            s.chmod(R + "/" + target, 0o700)
    _, out, err = c.exec_command(
        "/root/miniconda3/bin/python - <<'PY'\nimport os,secrets\np='"
        + R
        + "/config/gateway.key'\nif not os.path.exists(p):\n fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\n with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(32))\nPY"
    )
    if out.channel.recv_exit_status() != 0:
        raise RuntimeError(err.read().decode())
    with s.open(R + "/config/gateway.key") as f:
        key = f.read().decode().strip()
    s.close()
finally:
    c.close()
sys.path.append(str(P.parent / "ArcReel" / ".venv" / "Lib" / "site-packages"))
from dotenv import dotenv_values

settings = dotenv_values(P.parent / "ArcReel" / ".env")
base = "http://127.0.0.1:1241/api/v1"
login = urllib.parse.urlencode(
    {
        "username": settings.get("AUTH_USERNAME") or "admin",
        "password": settings.get("AUTH_PASSWORD") or "",
    }
).encode()
with urllib.request.urlopen(base + "/auth/token", data=login, timeout=10) as response:
    token = json.load(response)["access_token"]


def api(path, body=None):
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


catalog = api("/custom-providers")
items = catalog if isinstance(catalog, list) else catalog.get("providers", [])
existing = [p for p in items if p.get("base_url") == "http://127.0.0.1:19000"]
if existing:
    report = {
        "status": "existing_provider_preserved",
        "provider_id": existing[0].get("id"),
    }
else:
    data = Path(
        json.loads((P / "reports/local-audit.json").read_text(encoding="utf-8"))[
            "data_dir"
        ]
    )
    backup = P / "backups"
    backup.mkdir(exist_ok=True)
    backup_file = backup / (
        "arcreel-before-cloud-provider-" + time.strftime("%Y%m%d-%H%M%S") + ".sqlite"
    )
    with (
        sqlite3.connect(
            (data / ".arcreel.db").as_uri() + "?mode=ro", uri=True
        ) as source,
        sqlite3.connect(backup_file) as destination,
    ):
        source.backup(destination)
    result = api(
        "/custom-providers",
        {
            "display_name": "AutoDL Wan / CosyVoice (待 GPU 验收)",
            "discovery_format": "openai",
            "base_url": "http://127.0.0.1:19000",
            "api_key": key,
            "video_max_workers": 1,
            "audio_max_workers": 1,
            "models": [
                {
                    "model_id": "wan22-i2v-fp8",
                    "display_name": "Wan2.2 I2V FP8 (未验证)",
                    "endpoint": "newapi-video",
                    "is_enabled": False,
                    "supported_durations": [3, 5],
                },
                {
                    "model_id": "cosyvoice3",
                    "display_name": "CosyVoice3 (未验证)",
                    "endpoint": "openai-tts",
                    "is_enabled": False,
                },
            ],
        },
    )
    report = {
        "status": "registered_disabled",
        "provider_id": result.get("id"),
        "backup": str(backup_file),
        "base_url": "http://127.0.0.1:19000",
        "gpu_tested": False,
    }
(P / "reports" / "arcreel-cloud-provider.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True))

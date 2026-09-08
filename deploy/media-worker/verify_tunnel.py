"""Check the real SSH tunnel against a gateway deliberately started without GPU authorization."""

import json
import sqlite3
import time
from pathlib import Path

import httpx
from local_connection import configure_local_connection

configure_local_connection()

ROOT = Path(__file__).resolve().parent
data = Path(
    json.loads((ROOT / "reports/local-audit.json").read_text(encoding="utf-8"))[
        "data_dir"
    ]
)
with sqlite3.connect((data / ".arcreel.db").as_uri() + "?mode=ro", uri=True) as db:
    key, base = db.execute(
        "SELECT api_key,base_url FROM custom_provider WHERE base_url=?",
        ("http://127.0.0.1:19000",),
    ).fetchone()
with httpx.Client(base_url=base, timeout=15) as client:
    live = client.get("/health/live")
    live.raise_for_status()
    health = client.get("/health/ready").json()
    assert (
        health["preparation_checked"]
        and not health["gpu_authorized"]
        and not health["ready"]
    )
    report = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "health": health,
        "statuses": {},
        "gpu_inference": False,
    }
    for path, body in [
        (
            "/v1/audio/speech",
            {"model": "cosyvoice3", "voice": "alloy", "input": "connection test"},
        ),
        (
            "/v1/video/generations",
            {"model": "wan22-i2v-fp8", "prompt": "connection test", "image": "unused"},
        ),
    ]:
        unauthenticated = client.post(path, json=body)
        authenticated = client.post(
            path, json=body, headers={"Authorization": "Bearer " + key}
        )
        assert unauthenticated.status_code == 401
        assert authenticated.status_code == 503
        report["statuses"][path] = {"without_key": 401, "valid_key_gpu_disabled": 503}
report["passed"] = True
(ROOT / "reports/tunnel-check.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print(json.dumps(report, indent=2))

"""Read-only checks of the existing ArcReel service; never print credentials."""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent / "ArcReel"
sys.path.insert(0, str(ARC))
from dotenv import dotenv_values
from lib.app_data_dir import app_data_dir
from lib.codex_runtime import connection_summary

settings = dotenv_values(ARC / ".env")
base = "http://127.0.0.1:1241"
data = urllib.parse.urlencode(
    {
        "username": settings.get("AUTH_USERNAME") or "admin",
        "password": settings.get("AUTH_PASSWORD") or "",
    }
).encode()
with urllib.request.urlopen(
    base + "/api/v1/auth/token", data=data, timeout=10
) as response:
    token = json.load(response)["access_token"]


def get(path):
    req = urllib.request.Request(
        base + path, headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


report = {
    "health": get("/health"),
    "data_dir": str(app_data_dir()),
    "provider": connection_summary(),
}
schema = get("/openapi.json")
paths = [p for p in schema["paths"] if "config" in p or "custom-providers" in p]
report["configuration_routes"] = paths
config = get("/api/v1/system/config")["settings"]
report["model_defaults"] = {
    k: v
    for k, v in config.items()
    if (k.startswith("default_") and k.endswith("_backend")) or k in ("text_backend_simple", "text_backend_complex")
}
catalog = get("/api/v1/custom-providers")
items = catalog if isinstance(catalog, list) else catalog.get("providers", [])
report["cloud_providers"] = [
    {
        "id": p.get("id"),
        "display_name": p.get("display_name"),
        "base_url": p.get("base_url"),
        "models": [
            {"model_id": m.get("model_id"), "is_enabled": m.get("is_enabled")}
            for m in p.get("models", [])
        ],
    }
    for p in items
    if p.get("base_url") == "http://127.0.0.1:19000"
]
(ROOT / "reports").mkdir(exist_ok=True)
(ROOT / "reports" / "local-audit.json").write_text(
    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True, indent=2))

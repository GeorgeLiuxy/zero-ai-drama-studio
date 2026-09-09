"""Publish an already deployed H3 tail-frame contract through authenticated ArcReel APIs."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
PROJECT = "proj-8fbe5aa8"


def main():
    report = json.loads((ROOT / "reports/h3-flf-deploy.json").read_text())
    assert report["cpu_contract_passed"]
    assert report["gateway_mode"] == "prepare-only"
    for cloud_name, digest in report["files"].items():
        local = ROOT / Path(cloud_name).name
        assert hashlib.sha256(local.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest, local
    with httpx.Client(base_url="http://127.0.0.1:19000", trust_env=False, timeout=15) as gateway:
        live = gateway.get("/health/live")
        live.raise_for_status()
        state = gateway.get("/health/ready")
        state.raise_for_status()
        state = state.json()
        assert not state["busy"]
        assert not state["queued"]
        assert not state["gpu_authorized"]
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=30) as api:
        token = api.post(
            "/auth/token",
            data={"username": cfg.get("AUTH_USERNAME") or "admin", "password": cfg.get("AUTH_PASSWORD") or ""},
        )
        token.raise_for_status()
        api.headers["Authorization"] = "Bearer " + token.json()["access_token"]

        def call(method, path, body=None):
            r = api.request(method, path, json=body)
            if r.is_error:
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:1800]}")
            return r.json()

        project = call("GET", f"/projects/{PROJECT}")["project"]
        assert project["video_provider_i2v"] == "custom-1/h3-i2v-int8"
        for status in ("queued", "running"):
            assert not call("GET", f"/projects/{PROJECT}/tasks?status={status}")["items"], "Project is busy"
        provider = next(p for p in call("GET", "/custom-providers")["providers"] if p["id"] == 1)
        model = next(m for m in provider["models"] if m["model_id"] == "h3-i2v-int8")
        assert (model.get("capability_overrides") or {}).get("last_frame") is not False
        endpoint_id = int(model["endpoint"].removeprefix("ce-"))
        previous = call("GET", f"/custom-endpoints/{endpoint_id}")
        definition = json.loads((ROOT / "arcreel-h3-endpoint.json").read_text())
        assert definition["capabilities"]["last_frame"] is True
        assert definition["capabilities"]["audio_track"] == "always_on"
        for k in ("author", "name"):
            assert previous["definition"]["meta"][k] == definition["meta"][k]
        validation = call("POST", "/custom-endpoints/validate", definition)
        assert not validation["errors"], validation["errors"]
        backup = ROOT / "backups" / ("before-h3-flf-config-" + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S"))
        backup.mkdir(parents=True, exist_ok=False)
        (backup / "endpoint.json").write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        before = call("GET", f"/projects/{PROJECT}/video-capabilities")
        (backup / "capabilities.json").write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
        call("PUT", f"/custom-endpoints/{endpoint_id}", definition)
        actual = call("GET", f"/projects/{PROJECT}/video-capabilities")
        assert actual["model"] == "h3-i2v-int8"
        assert actual["first_frame"]
        assert actual["last_frame"]
        assert actual["generate_audio"]
        assert actual["supported_durations"] == [5]
        assert actual["duration_constraints"]["resolution"] == "576"
        updated = call("GET", f"/projects/{PROJECT}")["project"]
        for k in (
            "video_provider_i2v",
            "video_generate_audio",
            "model_settings",
            "character_voice_binding",
            "audio_backend",
        ):
            assert updated.get(k) == project.get(k)
        result = {
            "configured_at": datetime.now().astimezone().isoformat(),
            "project": PROJECT,
            "endpoint": model["endpoint"],
            "capabilities": actual,
            "backup": str(backup),
            "gateway_mode": "prepare-only",
            "gpu_inference_submitted": False,
        }
        (ROOT / "reports/h3-flf-config.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()

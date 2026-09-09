"""Register H3 and run one formal, ledger-tracked ArcReel dialogue sample."""

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
PROJECT = "proj-8fbe5aa8"
MODEL = "custom-1/h3-i2v-int8"
REPORT = ROOT / "reports/h3-formal-sample.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "configure", "submit", "poll"])
    args = parser.parse_args()
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=30) as api:
        token = api.post(
            "/auth/token",
            data={"username": cfg.get("AUTH_USERNAME") or "admin", "password": cfg.get("AUTH_PASSWORD") or ""},
        )
        token.raise_for_status()
        api.headers["Authorization"] = "Bearer " + token.json()["access_token"]

        def call(method, path, payload=None):
            response = api.request(method, path, json=payload)
            if response.is_error:
                raise RuntimeError(f"{method} {path}: {response.status_code}: {response.text[:2000]}")
            return response.json()

        project = call("GET", f"/projects/{PROJECT}")["project"]
        if args.mode == "poll":
            report = json.loads(REPORT.read_text())
            task = call("GET", "/tasks/" + report["task_id"])
            print(json.dumps(task))
            return

        active = []
        for status in ("queued", "running"):
            active.extend(call("GET", f"/projects/{PROJECT}/tasks?status={status}&page_size=500")["items"])
        print(
            json.dumps(
                {
                    "active_tasks": [
                        {k: t.get(k) for k in ("task_id", "status", "task_type", "resource_id")} for t in active
                    ],
                    "project_config": {
                        k: project.get(k)
                        for k in (
                            "video_provider_i2v",
                            "video_generate_audio",
                            "character_voice_binding",
                            "model_settings",
                        )
                    },
                }
            )
        )
        if args.mode == "inspect":
            providers = call("GET", "/custom-providers")["providers"]
            print(json.dumps({"models": next(p for p in providers if p["id"] == 1)["models"]}))
            return
        if active:
            raise RuntimeError("Project tasks are active; configuration and sample submission deferred")

        REPORT.parent.mkdir(parents=True, exist_ok=True)

        if args.mode == "configure":
            definition = json.loads((ROOT / "arcreel-h3-endpoint.json").read_text())
            validation = call("POST", "/custom-endpoints/validate", definition)
            if validation["errors"]:
                raise RuntimeError(str(validation["errors"]))
            providers = call("GET", "/custom-providers")["providers"]
            provider = next(p for p in providers if p["id"] == 1)
            assert provider["base_url"] == "http://127.0.0.1:19000"
            backup = ROOT / "backups" / ("before-h3-config-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            backup.mkdir(parents=True, exist_ok=False)
            with (
                sqlite3.connect((ARC / "deploy/projects/.arcreel.db").as_uri() + "?mode=ro", uri=True) as source,
                sqlite3.connect(backup / "arcreel.sqlite") as target,
            ):
                source.backup(target)
            shutil.copy2(ARC / "deploy/projects" / PROJECT / "project.json", backup / "project.json")
            duplicates = validation["duplicates"]
            if len(duplicates) > 1:
                raise RuntimeError("Multiple H3 endpoint definitions require selection")
            endpoint = (
                call("PUT", f"/custom-endpoints/{duplicates[0]['id']}", definition)
                if duplicates
                else call("POST", "/custom-endpoints", definition)
            )
            fields = (
                "model_id",
                "display_name",
                "endpoint",
                "is_default",
                "is_enabled",
                "price_unit",
                "price_input",
                "price_output",
                "currency",
                "supported_durations",
                "resolution",
                "capability_overrides",
            )
            models = [{k: m[k] for k in fields if k in m} for m in provider["models"] if m["model_id"] != "h3-i2v-int8"]
            models.append(
                {
                    "model_id": "h3-i2v-int8",
                    "display_name": "AutoDL H3 人物对白（原生音画）",
                    "endpoint": endpoint["key"],
                    "is_default": False,
                    "is_enabled": True,
                    "supported_durations": [5],
                    "resolution": "576",
                }
            )
            call("PUT", "/custom-providers/1/models", {"models": models})
            settings = dict(project.get("model_settings") or {})
            settings[MODEL] = {**settings.get(MODEL, {}), "resolution": "576"}
            call(
                "PATCH",
                f"/projects/{PROJECT}",
                {
                    "video_provider_i2v": MODEL,
                    "video_generate_audio": True,
                    "character_voice_binding": "prompt",
                    "model_settings": settings,
                },
            )
            result = {"endpoint": endpoint["key"], "backup": str(backup), "project": PROJECT, "model": MODEL}
            (ROOT / "reports/h3-project-config.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
            return

        if REPORT.exists():
            raise RuntimeError("A sample record already exists; poll/reconcile it instead of submitting again")
        assert project["video_provider_i2v"] == MODEL
        assert project["video_generate_audio"] is True
        assert project["model_settings"][MODEL]["resolution"] == "576"
        script = json.loads((ARC / "deploy/projects" / PROJECT / "scripts/episode_2.json").read_text(encoding="utf-8"))
        scene = next(s for s in script["scenes"] if s["scene_id"] == "E2S05")
        assert scene["duration_seconds"] == 5
        assert scene["utterances"] == [{"kind": "dialogue", "speaker": "沈家兄长", "text": "谁要造反？"}]
        assert not scene["generated_assets"].get("video_clip"), "Scene already has video; inspect it first"
        # Record intent before the HTTP request so an interrupted response never causes a blind retry.
        report = {
            "project": PROJECT,
            "scene_id": "E2S05",
            "model": MODEL,
            "status": "submit_intent",
            "submitted_at": datetime.now().astimezone().isoformat(),
            "seed": 20260908,
        }
        REPORT.write_text(json.dumps(report, indent=2))
        response = call(
            "POST",
            f"/projects/{PROJECT}/generate/video/E2S05",
            {
                "script_file": "episode_2.json",
                "prompt": scene["video_prompt"],
                "duration_seconds": 5,
                "seed": 20260908,
                "narration_delivery": "post_production",
            },
        )
        report.update(response)
        REPORT.write_text(json.dumps(report, indent=2))
        print(json.dumps(response))


if __name__ == "__main__":
    main()

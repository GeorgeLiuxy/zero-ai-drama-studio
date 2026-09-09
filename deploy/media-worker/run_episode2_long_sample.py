"""Configure prepared episode durations and submit one durable 12-second sample."""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
sys.path.insert(0, str(ARC))
from lib.project_manager import ProjectManager
from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditor,
    script_revision,
)

PROJECT = "proj-8fbe5aa8"
REPORT = ROOT / "reports/episode2-long-sample.json"
PLAN = ROOT / "reports/episode2-preparation-20260909-094810-321108/manifest.json"


def save(record):
    temp = REPORT.with_suffix(".tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(REPORT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["submit", "poll"])
    args = parser.parse_args()
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(
        base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=45
    ) as api:
        response = api.post(
            "/auth/token",
            data={
                "username": cfg.get("AUTH_USERNAME") or "admin",
                "password": cfg.get("AUTH_PASSWORD") or "",
            },
        )
        response.raise_for_status()
        api.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def call(method, path, body=None):
            r = api.request(method, path, json=body)
            if r.is_error:
                raise RuntimeError(f"{method} {path}: {r.status_code}: {r.text[:1800]}")
            return r.json()

        if args.mode == "poll":
            record = json.loads(REPORT.read_text(encoding="utf-8"))
            if not record.get("task_id"):
                raise RuntimeError(
                    "Reconcile the saved intent; do not retry submission"
                )
            task = call("GET", "/tasks/" + record["task_id"])["task"]
            record.update(status=task["status"], task=task)
            save(record)
            print(
                json.dumps(
                    {
                        k: task.get(k)
                        for k in (
                            "task_id",
                            "status",
                            "provider_job_id",
                            "result",
                            "error_message",
                            "started_at",
                            "finished_at",
                        )
                    },
                    ensure_ascii=False,
                )
            )
            return
        if REPORT.exists():
            raise RuntimeError("Record exists; reconcile rather than submitting twice")
        for status in ("running", "queued", "cancelling"):
            assert not call("GET", f"/projects/{PROJECT}/tasks?status={status}")[
                "items"
            ]
        with httpx.Client(trust_env=False, timeout=10) as gateway:
            response = gateway.get("http://127.0.0.1:19000/health/ready")
            response.raise_for_status()
            health = response.json()
            assert (
                health["ready"]
                and health["gpu_authorized"]
                and not health["busy"]
                and not health["queued"]
            )
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        project_dir = ARC / "deploy/projects" / PROJECT
        pm = ProjectManager(ARC / "deploy/projects")
        current = pm.load_script_readonly(PROJECT, "episode_2.json")
        assert (
            script_revision(current)
            == plan["duration_edit_command"]["expected_revision"]
        )
        assert (
            hashlib.sha256((project_dir / "project.json").read_bytes()).hexdigest()
            == plan["project_sha256"]
        )
        for row in plan["scenes"]:
            for asset in row["assets"].values():
                assert (
                    hashlib.sha256(
                        (project_dir / asset["path"]).read_bytes()
                    ).hexdigest()
                    == asset["sha256"]
                )
        project = call("GET", f"/projects/{PROJECT}")["project"]
        assert project["video_provider_i2v"] == "custom-1/h3-i2v-int8"
        assert project["model_settings"]["custom-1/h3-i2v-int8"]["resolution"] == "864"
        provider = next(
            p for p in call("GET", "/custom-providers")["providers"] if p["id"] == 1
        )
        backup = (
            ROOT
            / "backups"
            / (
                "episode2-durations-"
                + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
            )
        )
        backup.mkdir(parents=True, exist_ok=False)
        for name in ("project.json", "scripts/episode_2.json"):
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_dir / name, target)
        (backup / "models.json").write_text(
            json.dumps(provider["models"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        record = {
            "project": PROJECT,
            "scene": "E2S11",
            "duration": 12,
            "resolution": "864",
            "status": "configure_intent",
            "backup": str(backup),
            "created_at": datetime.now().astimezone().isoformat(),
            "gpu_submissions_requested": 0,
        }
        save(record)
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
        models = [{k: m[k] for k in fields if k in m} for m in provider["models"]]
        target = next(m for m in models if m["model_id"] == "h3-i2v-int8")
        target["supported_durations"] = [5, 8, 10, 12]
        call("PUT", "/custom-providers/1/models", {"models": models})
        caps = call("GET", f"/projects/{PROJECT}/video-capabilities")
        assert caps["supported_durations"] == [5, 8, 10, 12]
        command = ScriptBatchEditCommand.model_validate(plan["duration_edit_command"])
        edit = ScriptBatchEditor(pm).execute(PROJECT, command)
        record["duration_edit"] = edit.model_dump(mode="json")
        save(record)
        if not edit.success:
            raise RuntimeError(json.dumps(record["duration_edit"], ensure_ascii=False))
        script = pm.load_script_readonly(PROJECT, "episode_2.json")
        for old, new in zip(current["scenes"], script["scenes"], strict=True):
            assert old["scene_id"] == new["scene_id"]
            for key in (
                "utterances",
                "image_prompt",
                "video_prompt",
                "generated_assets",
            ):
                assert old[key] == new[key], (old["scene_id"], key)
        scene = next(s for s in script["scenes"] if s["scene_id"] == "E2S11")
        assert scene["duration_seconds"] == 12 and not scene["generated_assets"].get(
            "video_clip"
        )
        record.update(
            status="submit_intent",
            gpu_submissions_requested=1,
            utterances=scene["utterances"],
            capabilities=caps,
            script_revision=script_revision(script),
        )
        save(record)
        result = call(
            "POST",
            f"/projects/{PROJECT}/generate/video/E2S11",
            {
                "script_file": "episode_2.json",
                "prompt": scene["video_prompt"],
                "duration_seconds": 12,
                "seed": 20260909,
                "narration_delivery": "post_production",
            },
        )
        record.update(result)
        save(record)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""One durable HD sample through ArcReel; an uncertain submission is never retried."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
PROJECT = "proj-8fbe5aa8"
MODEL = "custom-1/h3-i2v-int8"
REPORT = ROOT / "reports/h3-hd-sample.json"


def save(data):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(REPORT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["submit", "poll"])
    args = parser.parse_args()
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(
        base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=30
    ) as api:
        token = api.post(
            "/auth/token",
            data={
                "username": cfg.get("AUTH_USERNAME") or "admin",
                "password": cfg.get("AUTH_PASSWORD") or "",
            },
        )
        token.raise_for_status()
        api.headers["Authorization"] = "Bearer " + token.json()["access_token"]

        def call(method, path, body=None):
            response = api.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        if args.mode == "poll":
            record = json.loads(REPORT.read_text(encoding="utf-8"))
            if not record.get("task_id"):
                raise RuntimeError(
                    "Submission needs reconciliation; do not submit again"
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
            raise RuntimeError(
                "Sample already recorded. Poll or reconcile it; never submit twice."
            )
        with httpx.Client(trust_env=False, timeout=10) as gateway:
            response = gateway.get("http://127.0.0.1:19000/health/ready")
            response.raise_for_status()
            state = response.json()
            assert (
                state["ready"]
                and state["gpu_authorized"]
                and not state["busy"]
                and not state["queued"]
            )
        for status in ("queued", "running", "cancelling"):
            assert not call("GET", f"/projects/{PROJECT}/tasks?status={status}")[
                "items"
            ]
        project = call("GET", f"/projects/{PROJECT}")["project"]
        assert project["video_provider_i2v"] == MODEL
        path = ARC / "deploy/projects" / PROJECT
        script = json.loads(
            (path / "scripts/episode_2.json").read_text(encoding="utf-8")
        )
        item = next(s for s in script["scenes"] if s["scene_id"] == "E2S05")
        assert item["duration_seconds"] == 5
        assert item["utterances"] == [
            {"kind": "dialogue", "speaker": "沈家兄长", "text": "谁要造反？"}
        ]
        assert not item.get("end_frame_image")
        first = path / item["generated_assets"]["storyboard_image"]
        from PIL import Image

        with Image.open(first) as image:
            assert image.width * 16 == image.height * 9
            first_size = list(image.size)
        record = {
            "project": PROJECT,
            "scene": "E2S05",
            "script_file": "episode_2.json",
            "status": "configure_intent",
            "created_at": datetime.now().astimezone().isoformat(),
            "previous_model_settings": project.get("model_settings"),
            "resolution": "864",
            "native_target": [864, 1536],
            "first_frame_size": first_size,
            "first_frame_sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
            "seed": 20260908,
            "prior_video": item["generated_assets"].get("video_clip"),
            "prior_video_sha256": hashlib.sha256(
                (path / item["generated_assets"]["video_clip"]).read_bytes()
            ).hexdigest(),
            "source_utterances": item["utterances"],
            "gpu_submissions_requested": 0,
        }
        save(record)
        settings = dict(project.get("model_settings") or {})
        settings[MODEL] = {**settings.get(MODEL, {}), "resolution": "864"}
        call("PATCH", f"/projects/{PROJECT}", {"model_settings": settings})
        caps = call("GET", f"/projects/{PROJECT}/video-capabilities")
        assert caps["duration_constraints"]["resolution"] == "864", caps
        assert caps["model"] == "h3-i2v-int8" and caps["generate_audio"]
        record.update(
            status="submit_intent", gpu_submissions_requested=1, capabilities=caps
        )
        save(record)
        result = call(
            "POST",
            f"/projects/{PROJECT}/generate/video/E2S05",
            {
                "script_file": "episode_2.json",
                "prompt": item["video_prompt"],
                "duration_seconds": 5,
                "seed": 20260908,
                "narration_delivery": "post_production",
            },
        )
        record.update(result)
        save(record)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

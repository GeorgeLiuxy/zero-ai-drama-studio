"""Run one formal H3 first/last-frame sample; never synthesize separate narration."""

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
REPORT = ROOT / "reports/h3-flf-sample.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["submit", "poll"])
    args = parser.parse_args()
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
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:1500]}")
            return r.json()

        if args.mode == "poll":
            record = json.loads(REPORT.read_text(encoding="utf-8"))
            result = call("GET", "/tasks/" + record["task_id"])["task"]
            record.update(status=result["status"], last_task_snapshot=result)
            REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                json.dumps(
                    {
                        k: result.get(k)
                        for k in (
                            "task_id",
                            "status",
                            "provider_job_id",
                            "result",
                            "error_message",
                            "started_at",
                            "finished_at",
                        )
                    }
                )
            )
            return
        if REPORT.exists():
            raise RuntimeError("Sample record exists: poll and reconcile, do not submit twice")
        with httpx.Client(trust_env=False, timeout=15) as gateway:
            r = gateway.get("http://127.0.0.1:19000/health/ready")
            r.raise_for_status()
            state = r.json()
            assert state["ready"]
            assert state["gpu_authorized"]
            assert not state["busy"]
            assert not state["queued"]
        for status in ("queued", "running", "cancelling"):
            assert not call("GET", f"/projects/{PROJECT}/tasks?status={status}")["items"]
        caps = call("GET", f"/projects/{PROJECT}/video-capabilities")
        assert caps["model"] == "h3-i2v-int8"
        assert caps["last_frame"]
        assert caps["generate_audio"]
        assert caps["duration_constraints"]["resolution"] == "576"
        project_dir = ARC / "deploy/projects" / PROJECT
        script = json.loads((project_dir / "scripts/episode_1.json").read_text(encoding="utf-8"))
        scene = next(s for s in script["scenes"] if s["scene_id"] == "E1S01")
        assert scene["duration_seconds"] == 5
        assert not scene["generated_assets"].get("video_clip")
        assert scene["utterances"][0]["kind"] == "voiceover"
        inputs = {
            "first_frame": scene["generated_assets"]["storyboard_image"],
            "last_frame": scene["end_frame_image"],
        }
        from PIL import Image

        digests = {}
        for name, relative in inputs.items():
            path = project_dir / relative
            with Image.open(path) as image:
                assert image.size == (576, 1024)
            digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        record = {
            "project": PROJECT,
            "scene": "E1S01",
            "script_file": "episode_1.json",
            "submitted_at": datetime.now().astimezone().isoformat(),
            "status": "submit_intent",
            "seed": 20260908,
            "model": caps["model"],
            "input_paths": inputs,
            "input_hashes": digests,
            "narration_delivery": "post_production",
            "video_prompt": scene["video_prompt"],
            "known_content_conflict": "Saved Push In camera direction conflicts with a close first frame and wider last frame; content preserved for this sample.",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        result = call(
            "POST",
            f"/projects/{PROJECT}/generate/video/E1S01",
            {
                "script_file": "episode_1.json",
                "duration_seconds": 5,
                "seed": 20260908,
                "narration_delivery": "post_production",
                "prompt": scene["video_prompt"],
            },
        )
        record.update(result)
        REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()

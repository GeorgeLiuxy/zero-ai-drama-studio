"""Resume missing episode assets sequentially with durable submission intents."""

import argparse
import hashlib
import json
import time

import httpx
from dotenv import dotenv_values
from filelock import FileLock
from run_episode2_long_sample import ARC, PLAN, PROJECT, ROOT
from verify_h3_hd_sample import probe

STATE = ROOT / "reports/episode2-remaining.json"
MODEL = "custom-1/h3-i2v-int8"


def save(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(STATE)


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "run"])
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    expected = {row["scene_id"]: row for row in plan["scenes"]}
    original = json.loads(
        (PLAN.parent / "episode_2.before.json").read_text(encoding="utf-8")
    )
    original = {s["scene_id"]: s for s in original["scenes"]}
    directory = ARC / "deploy/projects" / PROJECT

    def read_scenes():
        script = json.loads(
            (directory / "scripts/episode_2.json").read_text(encoding="utf-8")
        )
        scenes = script["scenes"]
        if [s["scene_id"] for s in scenes] != list(expected):
            raise RuntimeError("Episode scene order changed; stop and reconcile")
        for scene in scenes:
            sid = scene["scene_id"]
            row = expected[sid]
            assert scene["duration_seconds"] == row["proposed_duration_seconds"], sid
            for key in (
                "utterances",
                "video_prompt",
                "image_prompt",
                "characters_in_scene",
                "scenes",
                "props",
            ):
                assert scene.get(key) == original[sid].get(key), (sid, key, "changed")
            first = scene["generated_assets"]["storyboard_image"]
            assert first == row["assets"]["storyboard_image"]["path"]
            assert (
                hashlib.sha256((directory / first).read_bytes()).hexdigest()
                == row["assets"]["storyboard_image"]["sha256"]
            )
        return scenes

    def planned():
        scenes = read_scenes()
        videos = [
            {"kind": "video", "scene_id": s["scene_id"]}
            for s in scenes
            if not s["generated_assets"].get("video_clip")
        ]
        audio = [
            {"kind": "tts", "scene_id": s["scene_id"]}
            for s in scenes
            if any(u["kind"] == "voiceover" for u in s["utterances"])
            and not s["generated_assets"].get("narration_audio")
        ]
        return videos + audio

    if args.mode == "inspect":
        jobs = planned()
        emit(
            {
                "resolution_for_remaining": "576",
                "jobs": jobs,
                "count": len(jobs),
                "submitted": 0,
            }
        )
        return
    with FileLock(str(STATE) + ".lock", timeout=0):
        validation = json.loads(
            (ROOT / "reports/episode2-long-validation.json").read_text(encoding="utf-8")
        )
        assert (
            validation["technical_passed"]
            and validation["task"]["status"] == "succeeded"
        )
        cfg = dotenv_values(ARC / ".env")
        with httpx.Client(
            base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=45
        ) as api:
            r = api.post(
                "/auth/token",
                data={
                    "username": cfg.get("AUTH_USERNAME") or "admin",
                    "password": cfg.get("AUTH_PASSWORD") or "",
                },
            )
            r.raise_for_status()
            api.headers["Authorization"] = "Bearer " + r.json()["access_token"]

            def call(method, path, body=None):
                r = api.request(method, path, json=body)
                if r.is_error:
                    raise RuntimeError(
                        f"{method} {path}: {r.status_code}: {r.text[:1200]}"
                    )
                return r.json()

            if STATE.exists():
                state = json.loads(STATE.read_text(encoding="utf-8"))
            else:
                for status in ("running", "queued", "cancelling"):
                    assert not call(
                        "GET", f"/projects/{PROJECT}/tasks?status={status}"
                    )["items"]
                project = call("GET", f"/projects/{PROJECT}")["project"]
                assert project["video_provider_i2v"] == MODEL
                state = {
                    "project": PROJECT,
                    "resolution": "576",
                    "previous_model_settings": project["model_settings"],
                    "status": "configure_intent",
                    "jobs": planned(),
                    "completed": 0,
                    "quality_review": "Native speech and lip sync still require viewing; technical pass is not content acceptance.",
                }
                save(state)
            if state["status"] == "configure_intent":
                project = call("GET", f"/projects/{PROJECT}")["project"]
                settings = dict(project.get("model_settings") or {})
                settings[MODEL] = {**settings.get(MODEL, {}), "resolution": "576"}
                call("PATCH", f"/projects/{PROJECT}", {"model_settings": settings})
                caps = call("GET", f"/projects/{PROJECT}/video-capabilities")
                assert caps["duration_constraints"]["resolution"] == "576"
                for sid, variant in (
                    ("E2S01", "use_tts"),
                    ("E2S05", "post_production"),
                    ("E2S11", "post_production"),
                ):
                    presentation = call(
                        "GET",
                        f"/projects/{PROJECT}/presentations/videos/{sid}?variant={variant}",
                    )
                    assert (
                        presentation["currency"] == "current"
                        and presentation["provenance"] == "verified"
                    )
                state["status"] = "running"
                save(state)
            for job in state["jobs"]:
                if job.get("status") == "succeeded":
                    continue
                sid = job["scene_id"]
                if job.get("task_id"):
                    task = call("GET", "/tasks/" + job["task_id"])["task"]
                else:
                    if job.get("status"):
                        raise RuntimeError(
                            "Unresolved submission intent: inspect task history, never blindly retry"
                        )
                    scene = next(s for s in read_scenes() if s["scene_id"] == sid)
                    asset_key = (
                        "video_clip" if job["kind"] == "video" else "narration_audio"
                    )
                    if scene["generated_assets"].get(asset_key):
                        raise RuntimeError(
                            f"{sid}: asset appeared outside this run; reconcile before submitting"
                        )
                    for status in ("running", "queued", "cancelling"):
                        assert not call(
                            "GET", f"/projects/{PROJECT}/tasks?status={status}"
                        )["items"]
                    with httpx.Client(trust_env=False, timeout=10) as gateway:
                        r = gateway.get("http://127.0.0.1:19000/health/ready")
                        r.raise_for_status()
                        h = r.json()
                        assert h["ready"] and not h["busy"] and not h["queued"]
                    project = call("GET", f"/projects/{PROJECT}")["project"]
                    assert project["video_provider_i2v"] == MODEL
                    assert project["model_settings"][MODEL]["resolution"] == "576"
                    job["status"] = "submit_intent"
                    save(state)
                    body = {"script_file": "episode_2.json"}
                    if job["kind"] == "video":
                        body.update(
                            prompt=scene["video_prompt"],
                            duration_seconds=scene["duration_seconds"],
                            seed=20260909,
                            narration_delivery="post_production",
                        )
                    result = call(
                        "POST",
                        f"/projects/{PROJECT}/generate/{job['kind']}/{sid}",
                        body,
                    )
                    job.update(task_id=result["task_id"], status="submitted")
                    save(state)
                    emit(
                        {
                            "submitted": job,
                            "completed": state["completed"],
                            "total": len(state["jobs"]),
                        }
                    )
                    task = call("GET", "/tasks/" + job["task_id"])["task"]
                while task["status"] in ("queued", "running", "cancelling"):
                    job.update(
                        status=task["status"],
                        provider_job_id=task.get("provider_job_id"),
                    )
                    save(state)
                    emit(
                        {
                            "scene": sid,
                            "kind": job["kind"],
                            "status": task["status"],
                            "completed": state["completed"],
                            "total": len(state["jobs"]),
                        }
                    )
                    time.sleep(30)
                    task = call("GET", "/tasks/" + job["task_id"])["task"]
                job.update(
                    status="validating"
                    if task["status"] == "succeeded"
                    else task["status"],
                    task=task,
                )
                save(state)
                if task["status"] != "succeeded":
                    raise RuntimeError(
                        f"{sid}: {task['status']}: {task.get('error_message')}"
                    )
                scene = next(s for s in read_scenes() if s["scene_id"] == sid)
                asset_key = (
                    "video_clip" if job["kind"] == "video" else "narration_audio"
                )
                path = (directory / scene["generated_assets"][asset_key]).resolve()
                assert path.is_relative_to(directory.resolve()) and path.is_file()
                info = probe(path)
                if job["kind"] == "video":
                    stream = next(
                        s for s in info["streams"] if s["codec_type"] == "video"
                    )
                    assert (stream["width"], stream["height"]) == (576, 1024)
                else:
                    stream = next(
                        s for s in info["streams"] if s["codec_type"] == "audio"
                    )
                    job["measured_audio_duration"] = float(
                        stream.get("duration", info["format"]["duration"])
                    )
                job["status"] = "succeeded"
                state["completed"] = sum(
                    j.get("status") == "succeeded" for j in state["jobs"]
                )
                save(state)
                emit(
                    {
                        "completed_job": sid,
                        "kind": job["kind"],
                        "completed": state["completed"],
                        "total": len(state["jobs"]),
                    }
                )
            state["status"] = "assets_complete"
            save(state)
            emit(
                {
                    "status": "assets_complete",
                    "completed": state["completed"],
                    "composition": "pending",
                }
            )


if __name__ == "__main__":
    main()

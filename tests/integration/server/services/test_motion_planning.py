"""Real text serialization, evidence caching, and bounded motion admission."""

import copy
import json
import subprocess

import httpx
import pytest
from PIL import Image

from lib.ledger import Ledger
from lib.text_backends.openai import OpenAITextBackend
from lib.text_generator import TextGenerator
from server.services.motion_planning import (
    MotionPlanningBlocked,
    apply_motion_plan,
    extract_motion_review_frames,
    prepare_motion_plan,
    record_task_motion,
    review_task_motion,
)


def plan_response(**updates):
    data = {
        "status": "ready",
        "first_frame_observation": "woman crouching by bed",
        "last_frame_observation": "same woman sitting on bed",
        "spatial_evidence": ["bed stays against north wall"],
        "action_steps": ["rise", "stand steadily", "sit on bed"],
        "action": "She rises, stands steadily, then bends her knees and sits on the bed.",
        "camera_motion": "Static",
        "estimated_minimum_seconds": 5,
        "issues": [],
        "uncertainties": [],
        "review_checks": ["standing visible between crouching and sitting"],
    }
    return {**data, **updates}


def completion(data):
    return httpx.Response(
        200,
        json={
            "id": "chat-motion",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-5.4-mini",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": json.dumps(data)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        },
    )


def inputs(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    first, last = root / "first.png", root / "last.png"
    Image.new("RGB", (72, 128), "red").save(first)
    Image.new("RGB", (72, 128), "blue").save(last)
    item = {
        "scene_id": "E1S01",
        "video_prompt": {"action": "rise and sit", "camera_motion": "Static", "ambiance_audio": "wind"},
        "utterances": [{"kind": "voiceover", "speaker": None, "text": "保留原旁白"}],
    }
    return {
        "project_path": root,
        "project_name": "project",
        "item": item,
        "items": [item],
        "first_frame": first,
        "last_frame": last,
        "duration_seconds": 5,
        "aspect_ratio": "9:16",
    }


async def test_two_images_are_sent_and_unchanged_evidence_is_reused(tmp_path, session_factory, respx_mock):
    args = inputs(tmp_path)
    before = copy.deepcopy(args["item"])
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(return_value=completion(plan_response()))
    generator = TextGenerator(OpenAITextBackend(api_key="test-key"), Ledger(session_factory=session_factory), "openai")
    plan, report = await prepare_motion_plan(**args, generator=generator)
    body = json.loads(route.calls[0].request.content)
    image_blocks = [
        part
        for msg in body["messages"]
        if isinstance(msg["content"], list)
        for part in msg["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_blocks) == 2
    assert all(part["image_url"]["url"].startswith("data:image/png;base64,") for part in image_blocks)
    again, same_report = await prepare_motion_plan(**args, generator=generator)
    assert plan == again
    assert report == same_report
    assert route.call_count == 1
    assert json.loads(report.read_text(encoding="utf-8"))["basis"]["duration_seconds"] == 5
    expanded = apply_motion_plan(args["item"]["video_prompt"], plan)
    assert "stands steadily" in expanded["action"]
    assert expanded["ambiance_audio"] == "wind"
    assert args["item"] == before
    Image.new("RGB", (72, 128), "green").save(args["last_frame"])
    _, new_report = await prepare_motion_plan(**args, generator=generator)
    assert new_report != report
    assert route.call_count == 2


@pytest.mark.parametrize(
    "response",
    [
        plan_response(status="needs_review", issues=["Bed-door geometry is inconsistent; no justified camera path"]),
        plan_response(estimated_minimum_seconds=8),
    ],
)
async def test_spatial_conflict_or_too_short_duration_blocks_and_keeps_report(
    tmp_path, session_factory, respx_mock, response
):
    args = inputs(tmp_path)
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(return_value=completion(response))
    generator = TextGenerator(OpenAITextBackend(api_key="test-key"), Ledger(session_factory=session_factory), "openai")
    for _ in range(2):
        with pytest.raises(MotionPlanningBlocked) as blocked:
            await prepare_motion_plan(**args, generator=generator)
        assert blocked.value.plan.status == "needs_review"
        saved = json.loads(blocked.value.report_path.read_text(encoding="utf-8"))
        assert saved["gpu_requests_submitted"] == 0
        assert saved["plan"]["issues"]
    assert route.call_count == 1


def create_clip(clip):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=72x128:rate=24:duration=1",
            "-c:v",
            "libx264",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )


def test_review_extracts_distributed_video_frames_with_endpoints(tmp_path):
    clip = tmp_path / "clip.mp4"
    create_clip(clip)
    frames = extract_motion_review_frames(clip, tmp_path / "review")
    assert len(frames) == 12
    with Image.open(frames[0]) as first, Image.open(frames[-1]) as last:
        assert first.size == last.size
        assert first.size[0] == 384
        assert first.tobytes() != last.tobytes()


async def test_paid_output_review_sends_references_and_samples_and_rejects_conflict(
    tmp_path, session_factory, respx_mock
):
    args = inputs(tmp_path)
    route = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(return_value=completion(plan_response()))
    generator = TextGenerator(OpenAITextBackend(api_key="test-key"), Ledger(session_factory=session_factory), "openai")
    _, plan_report = await prepare_motion_plan(**args, generator=generator)
    record_task_motion(args["project_path"], "task-1", plan_report, args["first_frame"], args["last_frame"])
    video = args["project_path"] / "paid.mp4"
    create_clip(video)
    route.mock(
        return_value=completion(
            {
                "status": "needs_review",
                "observed_steps": ["woman stays crouching then jumps to sitting"],
                "issues": ["no visible standing phase"],
                "uncertainties": [],
            }
        )
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="motion_output_review_required"):
            await review_task_motion(
                project_path=args["project_path"],
                project_name="project",
                task_id="task-1",
                video=video,
                generator=generator,
            )
    assert route.call_count == 2
    body = json.loads(route.calls[-1].request.content)
    image_blocks = [
        part
        for msg in body["messages"]
        if isinstance(msg["content"], list)
        for part in msg["content"]
        if part.get("type") == "image_url"
    ]
    assert len(image_blocks) == 14
    report_path = next((args["project_path"] / "continuity" / "reviews").glob("*/review.json"))
    cached = json.loads(report_path.read_text(encoding="utf-8"))
    assert cached["scope"] == "sampled_visual_motion_only"
    assert cached["sample_count"] == 12
    cached["video_sha256"] = "corrupted"
    report_path.write_text(json.dumps(cached), encoding="utf-8")
    with pytest.raises(ValueError, match="motion_review_cache_basis_mismatch"):
        await review_task_motion(
            project_path=args["project_path"],
            project_name="project",
            task_id="task-1",
            video=video,
            generator=generator,
        )

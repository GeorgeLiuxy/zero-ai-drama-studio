"""Exercise ArcReel's H3 HTTP adapter and gateway without model inference."""

import asyncio
import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
sys.path.insert(0, str(ARC))
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
from lib.custom_provider.endpoint_definition import validate_definition
from lib.prompt_utils import render_storyboard_video_prompt
from lib.video_backends.base import VideoGenerationRequest


@pytest.fixture
def h3_adapter(tmp_path, monkeypatch):
    definition = json.loads(
        (ROOT / "arcreel-h3-endpoint.json").read_text(encoding="utf-8")
    )
    assert not validate_definition(definition).errors
    monkeypatch.setenv("ARCREEL_WORKER_ROOT", str(tmp_path / "worker"))
    monkeypatch.setenv("ARCREEL_GPU_AUTHORIZED", "1")
    spec = importlib.util.spec_from_file_location(
        "h3_adapter_gateway", ROOT / "cloud_gateway.py"
    )
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    for folder in ("reports", "manifests", "workflows"):
        (gateway.ROOT / folder).mkdir()
    for name, data in (
        ("reports/models-ready.json", {}),
        ("reports/preflight.json", {"prepared": True}),
        ("reports/h3-preparation.json", {"status": "download_verified"}),
        ("reports/h3-environment.json", {"node_schema_passed": True}),
        ("manifests/source-lock.json", {}),
        ("manifests/h3-models.json", []),
    ):
        (gateway.ROOT / name).write_text(json.dumps(data), encoding="utf-8")
    for workflow in ("h3-api.json", "h3-flf-api.json"):
        (gateway.ROOT / "workflows" / workflow).write_bytes(
            (ROOT / workflow).read_bytes()
        )
    first = tmp_path / "first.png"
    Image.new("RGB", (576, 1024), "red").save(first)
    item = {
        "video_prompt": {
            "action": "沈家兄长迈进半步，肩头一偏躲开飞来的软枕。",
            "camera_motion": "Pan Right",
            "ambiance_audio": "木门推开的吱呀声，软枕扑落声。",
        },
        "utterances": [
            {"kind": "dialogue", "speaker": "沈家兄长", "text": "谁要造反？"}
        ],
    }
    prompt = render_storyboard_video_prompt(
        item["video_prompt"],
        item,
        content_mode="drama",
        voice_characters={
            "沈家兄长": {"voice_style": "青年男性声线，爽朗直接，带军中利落感。"}
        },
    )
    backend = DeclarativeVideoBackend(
        api_key=gateway.TOKEN,
        base_url="http://h3.test",
        model="h3-i2v-int8",
        definition=definition,
        provider="custom-1",
    )
    request = VideoGenerationRequest(
        prompt=prompt,
        output_path=tmp_path / "result.mp4",
        start_image=first,
        aspect_ratio="9:16",
        resolution="576",
        duration_seconds=5,
        seed=0,
        generate_audio=True,
    )
    # No lifespan context: no gateway worker or subprocess is started.
    client = TestClient(gateway.app, base_url="http://h3.test")
    yield gateway, client, backend, request
    client.close()


@pytest.mark.parametrize("with_tail", [False, True])
@pytest.mark.parametrize("short_edge,height", [(576, 1024), (864, 1536)])
@pytest.mark.parametrize("duration,frames", [(5, 124), (8, 192), (10, 243), (12, 294)])
def test_h3_canonical_dialogue_submits_and_returns_native_audio_metadata(
    h3_adapter, with_tail, short_edge, height, duration, frames
):
    gateway, client, backend, request = h3_adapter
    request.resolution = str(short_edge)
    request.duration_seconds = duration
    if with_tail:
        request.end_image = request.output_path.with_name("last.png")
        Image.new("RGB", (576, 1024), "blue").save(request.end_image)
    artifact = b"transport-only-native-audio-artifact"
    submissions = []
    polls = []
    caps = backend.video_capabilities
    assert caps.first_frame
    assert not caps.text_to_video
    assert caps.last_frame
    assert caps.audio_track == "always_on"

    def serve(outbound):
        payload = json.loads(outbound.content) if outbound.content else None
        response = client.request(
            outbound.method,
            outbound.url.path,
            json=payload,
            headers={"Authorization": outbound.headers.get("Authorization", "")},
        )
        if outbound.method == "POST":
            assert response.status_code == 200, response.text
            submissions.append(payload)
            job = response.json()["task_id"]
            stored = json.loads(gateway.lookup(job)["body"])
            assert stored["raw_prompt"] == request.prompt
            assert "<d>[Chinese]谁要造反？</d>" in stored["prompt"]
            assert "沈家兄长 (S1) says:" in stored["prompt"]
            assert (
                "Voice and delivery: 青年男性声线，爽朗直接，带军中利落感。"
                in stored["prompt"]
            )
            graph, output_node = gateway.video_workflow(
                job,
                stored,
                "uploaded-first.png",
                "uploaded-last.png" if with_tail else None,
            )
            assert graph["104"]["inputs"]["prompt"] == stored["prompt"]
            assert graph["104"]["inputs"]["length"] == frames
            assert (
                graph["104"]["inputs"]["width"],
                graph["104"]["inputs"]["height"],
            ) == (short_edge, height)
            assert graph["104"]["inputs"]["first_frame"] == ["114", 0]
            assert graph["114"]["inputs"]["image"] == "uploaded-first.png"
            if with_tail:
                assert graph["104"]["inputs"]["last_frame"] == ["115", 0]
                assert graph["115"]["inputs"]["image"] == "uploaded-last.png"
                assert (
                    stored["end_image_sha256"]
                    == hashlib.sha256(request.end_image.read_bytes()).hexdigest()
                )
                assert stored["last_frame_time_seconds"] == pytest.approx(
                    (frames - 1) / 24
                )
                assert "<Picture 2>" in stored["prompt"]
                tail_label = f"{(frames - 1) / 24:.9f}".rstrip("0").rstrip(".")
                assert f"{tail_label}-second mark" in stored["prompt"]
            else:
                assert "last_frame" not in graph["104"]["inputs"]
                assert "115" not in graph
                assert not stored.get("end_image_sha256")
                assert "<Picture 2>" not in stored["prompt"]
            assert graph["91"]["inputs"]["audio"] == ["23", 0]
            assert output_node == "92"
            # GPU output/probe evidence is the simulated boundary; transport and
            # validation remain real. These bytes are not a playable video claim.
            metadata = gateway.validate_video_probe(
                stored,
                {
                    "width": short_edge,
                    "height": height,
                    "fps": 24,
                    "frames": frames,
                    "has_audio": True,
                    "audio_frames": round(frames / 24 * 32000 / 1024),
                    "audio_samples": round(frames / 24 * 32000),
                    "audio_peak": 0.15,
                    "audio_finite": True,
                    "audio_duration": round(frames / 24 * 32000) / 32000,
                    "audio_sample_rate": 32000,
                    "audio_channels": 2,
                },
            )
            (gateway.ROOT / "outputs" / (job + ".mp4")).write_bytes(artifact)
            gateway.update(
                job,
                status="succeeded",
                metadata=json.dumps(metadata),
                sha256=hashlib.sha256(artifact).hexdigest(),
            )
        elif "/generations/" in outbound.url.path:
            polls.append(response.json())
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    with respx.mock as router:
        router.route(host="h3.test").mock(side_effect=serve)
        result = asyncio.run(backend.generate(request))

    assert len(submissions) == 1
    sent = submissions[0]
    assert (sent["width"], sent["height"], sent["duration"]) == (
        short_edge,
        height,
        duration,
    )
    assert sent["model"] == "h3-i2v-int8"
    assert sent["generate_audio"] is True
    assert sent["seed"] == 0
    assert sent["prompt"] == request.prompt
    assert (
        base64.b64decode(sent["image"].split(",", 1)[1])
        == request.start_image.read_bytes()
    )
    if with_tail:
        assert (
            base64.b64decode(sent["end_image"].split(",", 1)[1])
            == request.end_image.read_bytes()
        )
    else:
        assert "end_image" not in sent
    assert len(polls) == 1
    assert polls[0]["status"] == "succeeded"
    meta = polls[0]["metadata"]
    assert meta["requested_duration"] == duration
    assert meta["duration"] == pytest.approx(frames / 24)
    assert meta["actual_duration"] == pytest.approx(frames / 24)
    assert meta["audio_mode"] == "native"
    assert meta["has_audio"] is True
    assert result.generate_audio is True
    assert result.task_id == polls[0]["task_id"]
    # ArcReel's billing result uses whole seconds; media/projection keeps the
    # separate measured duration instead of treating this integer as a cut point.
    assert result.duration_seconds == duration
    assert request.output_path.read_bytes() == artifact


@pytest.mark.parametrize("with_tail", [False, True])
def test_h3_audio_disabled_fails_before_job_or_output(h3_adapter, with_tail):
    gateway, client, backend, request = h3_adapter
    if with_tail:
        request.end_image = request.output_path.with_name("last.png")
        Image.new("RGB", (576, 1024), "blue").save(request.end_image)
    request.generate_audio = False
    rejections = []

    def serve(outbound):
        response = client.request(
            outbound.method,
            outbound.url.path,
            json=json.loads(outbound.content),
            headers={"Authorization": outbound.headers.get("Authorization", "")},
        )
        rejections.append(response.json())
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    with respx.mock as router:
        router.route(host="h3.test").mock(side_effect=serve)
        with pytest.raises(httpx.HTTPStatusError) as exc:
            asyncio.run(backend.generate(request))

    assert exc.value.response.status_code == 422
    assert len(rejections) == 1
    assert "audio mode is fixed" in json.dumps(rejections)
    assert gateway.TASKS.empty()
    with gateway.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert list((gateway.ROOT / "inputs").iterdir()) == []
    assert not request.output_path.exists()

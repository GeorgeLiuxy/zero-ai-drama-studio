"""Exercise real ArcReel serialization through the real gateway without inference."""

import asyncio
import base64
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
from lib.video_backends.base import VideoGenerationRequest


@pytest.mark.parametrize("with_tail", [False, True])
def test_arcreel_submits_actual_images_and_downloads_gateway_result(tmp_path, monkeypatch, with_tail):
    definition = json.loads((ROOT / "arcreel-wan-endpoint.json").read_text(encoding="utf-8"))
    assert not validate_definition(definition).errors
    monkeypatch.setenv("ARCREEL_WORKER_ROOT", str(tmp_path / "worker"))
    monkeypatch.setenv("ARCREEL_GPU_AUTHORIZED", "1")
    spec = importlib.util.spec_from_file_location("adapter_gateway", ROOT / "cloud_gateway.py")
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    for folder in ("reports", "manifests", "workflows"):
        (gateway.ROOT / folder).mkdir()
    (gateway.ROOT / "reports/models-ready.json").write_text("{}")
    (gateway.ROOT / "reports/preflight.json").write_text('{"prepared":true}')
    (gateway.ROOT / "manifests/source-lock.json").write_text("{}")
    for name in ("wan-api.json", "wan-flf-api.json"):
        (gateway.ROOT / "workflows" / name).write_bytes((ROOT / name).read_bytes())
    first, last = tmp_path / "first.png", tmp_path / "last.png"
    Image.new("RGB", (90, 160), "red").save(first)
    Image.new("RGB", (90, 160), "blue").save(last)
    output = tmp_path / "result.mp4"
    # Transport fixture bytes; no assertion of real model/video quality.
    artifact = b"transport-test-artifact"
    submitted = []
    client = TestClient(gateway.app)

    def serve(request):
        payload = json.loads(request.content) if request.content else None
        response = client.request(
            request.method,
            request.url.path,
            json=payload,
            headers={"Authorization": request.headers.get("Authorization", "")},
        )
        if request.method == "POST":
            assert response.status_code == 200, response.text
            submitted.append(payload)
            job = response.json()["task_id"]
            stored = json.loads(gateway.lookup(job)["body"])
            graph, _ = gateway.video_workflow(job, stored, "first.png", "last.png" if with_tail else None)
            assert (graph["98"]["class_type"] == "WanFirstLastFrameToVideo") == with_tail
            (gateway.ROOT / "outputs" / (job + ".mp4")).write_bytes(artifact)
            gateway.update(job, status="succeeded")
        if request.method == "GET" and "/generations/" in request.url.path:
            data = response.json()
            data["url"] = data["url"].replace("http://testserver", "http://wan.test")
            return httpx.Response(response.status_code, json=data)
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)

    backend = DeclarativeVideoBackend(
        api_key=gateway.TOKEN,
        base_url="http://wan.test",
        model="wan22-i2v-fp8",
        definition=definition,
        provider="custom-1",
    )
    assert backend.video_capabilities.last_frame
    with respx.mock as router:
        router.route(host="wan.test").mock(side_effect=serve)
        asyncio.run(
            backend.generate(
                VideoGenerationRequest(
                    prompt="Move slowly",
                    output_path=output,
                    start_image=first,
                    end_image=last if with_tail else None,
                    aspect_ratio="9:16",
                    resolution="720p",
                    duration_seconds=3,
                    seed=0,
                    generate_audio=False,
                )
            )
        )
    client.close()
    assert output.read_bytes() == artifact
    assert len(submitted) == 1
    assert base64.b64decode(submitted[0]["image"].split(",", 1)[1]) == first.read_bytes()
    if with_tail:
        assert base64.b64decode(submitted[0]["end_image"].split(",", 1)[1]) == last.read_bytes()
    else:
        assert not submitted[0].get("end_image")

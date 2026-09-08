"""Contract tests use temporary state and never load a GPU model."""

import base64
import importlib.util
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_WORKER_ROOT", str(tmp_path))
    monkeypatch.delenv("ARCREEL_GPU_AUTHORIZED", raising=False)
    spec = importlib.util.spec_from_file_location(
        "test_cloud_gateway", Path(__file__).with_name("cloud_gateway.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for d in ["reports", "workflows", "manifests"]:
        (tmp_path / d).mkdir()
    (tmp_path / "workflows/wan-api.json").write_text("{}")
    (tmp_path / "manifests/source-lock.json").write_text("{}")
    client = TestClient(module.app)
    yield module, client, {"Authorization": "Bearer " + module.TOKEN}
    client.close()


def body():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, format="PNG")
    return {
        "model": "wan22-i2v-fp8",
        "prompt": "test",
        "image": "data:image/png;base64,"
        + base64.b64encode(buffer.getvalue()).decode(),
        "seed": 0,
    }


def authorize(module, monkeypatch):
    monkeypatch.setenv("ARCREEL_GPU_AUTHORIZED", "1")
    (module.ROOT / "reports/models-ready.json").write_text("{}")
    (module.ROOT / "reports/preflight.json").write_text('{"prepared": true}')


def test_auth_and_offline(gateway):
    m, c, h = gateway
    assert c.post("/v1/video/generations", json=body()).status_code == 401
    assert c.post("/v1/video/generations", json=body(), headers=h).status_code == 503
    assert m.TASKS.empty()


def test_same_request_returns_persistent_job(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    first = c.post("/v1/video/generations", json=body(), headers=h)
    second = c.post("/v1/video/generations", json=body(), headers=h)
    assert first.status_code == 200 and first.json() == second.json()
    assert m.TASKS.qsize() == 1
    assert json.loads(m.lookup(first.json()["task_id"])["body"])["seed"] == 0


def test_unknown_is_not_resubmitted(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    job = c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"]
    m.TASKS.get_nowait()
    m.update(job, status="unknown")
    assert (
        c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"] == job
    )
    assert m.TASKS.empty()
    assert (
        c.get("/v1/video/generations/" + job, headers=h).json()["status"] == "unknown"
    )


def test_arcreel_480p_dimensions_are_aligned_for_wan(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    request = body() | {"width": 896, "height": 504}
    job = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    status = c.get("/v1/video/generations/" + job, headers=h).json()
    assert status["metadata"] == {"width": 768, "height": 432, "duration": 5}


@pytest.mark.parametrize(
    "patch",
    [
        {"image": "http://localhost/private"},
        {"width": 641},
        {"duration": 60},
        {"model": "arbitrary"},
        {"image": "data:image/png;base64,bad"},
    ],
)
def test_unsafe_or_unsupported_input_rejected(gateway, monkeypatch, patch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    request = body()
    request.update(patch)
    assert c.post("/v1/video/generations", json=request, headers=h).status_code == 422
    assert m.TASKS.empty()


def test_audio_cannot_overlap_video_gpu_slot(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    (m.ROOT / "inputs/ref.wav").write_bytes(b"test")
    (m.ROOT / "config/voices.json").write_text(
        json.dumps({"demo": {"reference": "ref.wav", "transcript": "test"}})
    )
    (m.ROOT / "manifests/cosyvoice-models.json").write_text("[]")
    m.GPU.acquire()
    try:
        r = c.post(
            "/v1/audio/speech",
            headers=h,
            json={"model": "cosyvoice3", "input": "test", "voice": "demo"},
        )
        assert r.status_code == 429
        with m.connect() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    finally:
        m.GPU.release()


def test_capacity_counts_dequeued_job_waiting_for_gpu(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    for seed in range(32):
        request = body()
        request["seed"] = seed
        assert (
            c.post("/v1/video/generations", json=request, headers=h).status_code == 200
        )
        if seed == 0:
            m.TASKS.get_nowait()
    request = body()
    request["seed"] = 32
    assert c.post("/v1/video/generations", json=request, headers=h).status_code == 429

"""Contract tests use temporary state and never load a GPU model."""

import base64
import hashlib
import importlib.util
import io
import json
import sqlite3
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
    for name in ("wan-api.json", "wan-flf-api.json", "h3-api.json", "h3-flf-api.json"):
        (tmp_path / "workflows" / name).write_bytes(
            Path(__file__).with_name(name).read_bytes()
        )
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


def h3_body(width=576, height=1024, color="black"):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return body() | {
        "model": "h3-i2v-int8",
        "width": width,
        "height": height,
        "duration": 5,
        "generate_audio": True,
        "prompt": "Action: 王老爷抬眼看向女儿。\nCamera_Motion: Static\nAmbiance_Audio: 烛火轻响与衣料摩擦。\nDialogue:\n- Speaker: 王老爷\n  Line: 谁要造反？\n\n禁止出现：BGM、文字字幕、水印。",
        "image": "data:image/png;base64,"
        + base64.b64encode(buffer.getvalue()).decode(),
    }


def authorize(module, monkeypatch):
    monkeypatch.setenv("ARCREEL_GPU_AUTHORIZED", "1")
    (module.ROOT / "reports/models-ready.json").write_text("{}")
    (module.ROOT / "reports/preflight.json").write_text('{"prepared": true}')


def authorize_h3(module, monkeypatch):
    authorize(module, monkeypatch)
    (module.ROOT / "reports/h3-preparation.json").write_text(
        '{"status":"download_verified"}'
    )
    (module.ROOT / "reports/h3-environment.json").write_text(
        '{"node_schema_passed":true}'
    )
    (module.ROOT / "manifests/h3-models.json").write_text("[]")


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
    assert first.status_code == 200
    assert first.json() == second.json()
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


def test_wan_idempotency_remains_compatible_with_previous_gateway(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    request = body()
    payload = request | {"width": 640, "height": 640, "duration": 5, "n": 1}
    image = payload.pop("image")
    payload["image_sha256"] = hashlib.sha256(
        base64.b64decode(image.split(",", 1)[1])
    ).hexdigest()
    payload["workflow_sha256"] = hashlib.sha256(
        (m.ROOT / "workflows/wan-api.json").read_bytes()
    ).hexdigest()
    payload["source_lock_sha256"] = hashlib.sha256(
        (m.ROOT / "manifests/source-lock.json").read_bytes()
    ).hexdigest()
    previous_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()
    response = c.post("/v1/video/generations", json=request, headers=h)
    assert response.json()["task_id"] == previous_id


def test_h3_requires_auth_authorization_and_its_own_preparation(gateway, monkeypatch):
    m, c, h = gateway
    assert c.post("/v1/video/generations", json=h3_body()).status_code == 401
    assert c.post("/v1/video/generations", json=h3_body(), headers=h).status_code == 503
    authorize(m, monkeypatch)
    assert c.post("/v1/video/generations", json=h3_body(), headers=h).status_code == 503
    assert m.TASKS.empty()


@pytest.mark.parametrize(("width", "height"), [(576, 1024), (1024, 576), (576, 576)])
def test_h3_native_dimensions_duration_and_audio_metadata(
    gateway, monkeypatch, width, height
):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    response = c.post("/v1/video/generations", json=h3_body(width, height), headers=h)
    assert response.status_code == 200
    job = response.json()["task_id"]
    second = c.post("/v1/video/generations", json=h3_body(width, height), headers=h)
    assert second.json()["task_id"] == job
    assert m.TASKS.qsize() == 1
    metadata = c.get("/v1/video/generations/" + job, headers=h).json()["metadata"]
    assert metadata["width"] == width
    assert metadata["height"] == height
    assert metadata["fps"] == 24
    assert metadata["requested_duration"] == 5
    assert metadata["duration"] == pytest.approx(124 / 24)
    assert metadata["audio_mode"] == "native"
    assert (
        "has_audio" not in metadata
    )  # Verified only after decoding the generated file.


@pytest.mark.parametrize(
    "patch",
    [
        {"duration": 3},
        {"duration": 6},
        {"width": 720, "height": 1280},
        {"width": 864, "height": 480},
        {"width": 640, "height": 640},
        {"generate_audio": False},
        {"n": 2},
        {"image": body()["image"]},
    ],
)
def test_h3_rejects_unsupported_requests_before_enqueuing(gateway, monkeypatch, patch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    response = c.post("/v1/video/generations", json=h3_body() | patch, headers=h)
    assert response.status_code == 422
    assert m.TASKS.empty()
    assert list((m.ROOT / "inputs").iterdir()) == []


def test_h3_idempotency_uses_h3_workflow_not_wan(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    first = c.post("/v1/video/generations", json=h3_body(), headers=h).json()["task_id"]
    (m.ROOT / "workflows/wan-api.json").write_text('{"unrelated":true}')
    assert (
        c.post("/v1/video/generations", json=h3_body(), headers=h).json()["task_id"]
        == first
    )
    workflow = m.ROOT / "workflows/h3-api.json"
    workflow.write_text(workflow.read_text() + "\n")
    second = c.post("/v1/video/generations", json=h3_body(), headers=h).json()[
        "task_id"
    ]
    assert second != first
    assert m.TASKS.qsize() == 2


def test_fixed_workflow_adapters_route_both_models(gateway):
    m, _, _ = gateway
    wan = m.Video(**body()).model_dump()
    graph, output = m.video_workflow("f" * 64, wan, "uploaded-wan.png")
    assert output == "108"
    assert graph["97"]["inputs"]["image"] == "uploaded-wan.png"
    assert graph["98"]["inputs"]["length"] == 81
    assert graph["86"]["inputs"]["noise_seed"] == 0
    h3 = m.Video(**h3_body()).model_dump()
    h3["prompt"] = "(S1) says: <d>[Chinese] 父亲，女儿明白。</d>"
    graph, output = m.video_workflow("f" * 64, h3, "uploaded-h3.png")
    assert output == "92"
    assert graph["114"]["inputs"]["image"] == "uploaded-h3.png"
    assert graph["104"]["inputs"]["prompt"] == h3["prompt"]
    assert graph["104"]["inputs"]["width"] == 576
    assert graph["104"]["inputs"]["height"] == 1024
    assert graph["104"]["inputs"]["length"] == 124
    assert graph["15"]["inputs"]["noise_seed"] == 0
    assert graph["91"]["inputs"]["audio"] == ["23", 0]
    assert graph["91"]["inputs"]["fps"] == 24
    assert graph["92"]["inputs"]["filename_prefix"] == "arcreel/" + "f" * 64


def test_changed_persistent_workflow_requires_reconciliation(gateway):
    m, _, _ = gateway
    request = m.Video(**h3_body()).model_dump() | {"workflow_sha256": "outdated"}
    with pytest.raises(RuntimeError, match="workflow_changed_requires_reconciliation"):
        m.video_workflow("f" * 64, request, "frame.png")


def test_h3_workflow_cannot_silently_disconnect_audio(gateway):
    m, _, _ = gateway
    path = m.ROOT / "workflows/h3-api.json"
    graph = json.loads(path.read_text())
    del graph["91"]["inputs"]["audio"]
    path.write_text(json.dumps(graph))
    with pytest.raises(RuntimeError, match="h3_native_audio_not_connected"):
        m.video_workflow("f" * 64, m.Video(**h3_body()).model_dump(), "frame.png")


def h3_probe():
    return {
        "width": 576,
        "height": 1024,
        "fps": 24,
        "frames": 124,
        "has_audio": True,
        "audio_frames": 162,
        "audio_peak": 0.15,
        "audio_finite": True,
        "audio_duration": 5.18,
        "audio_sample_rate": 32000,
        "audio_channels": 2,
    }


@pytest.mark.parametrize(
    ("patch", "reason"),
    [
        ({"width": 720}, "dimensions"),
        ({"fps": 16}, "fps"),
        ({"frames": 120}, "frame_count"),
        ({"has_audio": False}, "audio"),
        ({"audio_frames": 0}, "audio"),
        ({"audio_peak": 0}, "audio"),
        ({"audio_finite": False}, "audio"),
        ({"audio_duration": 1.0}, "audio_duration"),
    ],
)
def test_h3_output_must_decode_exact_canvas_frames_and_non_silent_audio(
    gateway, patch, reason
):
    m, _, _ = gateway
    with pytest.raises(RuntimeError, match=reason):
        m.validate_video_probe(h3_body(), h3_probe() | patch)


def test_h3_verified_metadata_and_wan_silent_output(gateway):
    m, _, _ = gateway
    result = m.validate_video_probe(h3_body(), h3_probe())
    assert result["actual_duration"] == pytest.approx(124 / 24)
    assert result["has_audio"] is True
    assert result["audio_mode"] == "native"
    wan = m.Video(**body()).model_dump()
    result = m.validate_video_probe(
        wan,
        h3_probe()
        | {
            "width": 640,
            "height": 640,
            "frames": 81,
            "fps": 16,
            "has_audio": False,
            "audio_frames": 0,
            "audio_peak": 0,
        },
    )
    assert result["audio_mode"] == "none"
    assert result["has_audio"] is False


def test_h3_shares_existing_queue_capacity(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    for seed in range(32):
        assert (
            c.post(
                "/v1/video/generations", json=body() | {"seed": seed}, headers=h
            ).status_code
            == 200
        )
    assert c.post("/v1/video/generations", json=h3_body(), headers=h).status_code == 429


def test_wan_end_frame_persists_and_changes_job_identity(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    request = body() | {"end_image": body()["image"]}
    first = c.post("/v1/video/generations", json=request, headers=h)
    assert first.status_code == 200
    job = first.json()["task_id"]
    assert (
        c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
        == job
    )
    stored = json.loads(m.lookup(job)["body"])
    assert "end_image" not in stored
    assert stored["end_image_sha256"] == stored["image_sha256"]
    with Image.open(m.ROOT / "inputs" / (job + "-end.png")) as img:
        assert img.size == (32, 32)
    graph, output = m.video_workflow(job, stored, "first-upload.png", "last-upload.png")
    assert output == "108"
    assert graph["98"]["class_type"] == "WanFirstLastFrameToVideo"
    assert graph["98"]["inputs"]["start_image"] == ["97", 0]
    assert graph["98"]["inputs"]["end_image"] == ["99", 0]
    assert graph["99"]["inputs"]["image"] == "last-upload.png"
    assert graph["86"]["inputs"]["steps"] == 20
    single = c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"]
    assert single != job
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buf, format="PNG")
    request["end_image"] = (
        "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    )
    changed = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    assert changed not in (single, job)


@pytest.mark.parametrize(
    "tail", ["", "https://example.com/frame.png", "data:image/png;base64,broken"]
)
def test_wan_invalid_tail_leaves_no_job_or_input(gateway, monkeypatch, tail):
    m, c, h = gateway
    authorize(m, monkeypatch)
    assert (
        c.post(
            "/v1/video/generations", json=body() | {"end_image": tail}, headers=h
        ).status_code
        == 422
    )
    assert m.TASKS.empty()
    assert list((m.ROOT / "inputs").iterdir()) == []


def test_wan_tail_aspect_mismatch_rejected(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    response = c.post(
        "/v1/video/generations",
        json=body() | {"end_image": h3_body()["image"]},
        headers=h,
    )
    assert response.status_code == 422
    assert m.TASKS.empty()


def test_missing_wan_workflow_and_wrong_h3_tail_rejected(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    (m.ROOT / "workflows/wan-flf-api.json").unlink()
    assert (
        c.post(
            "/v1/video/generations",
            json=body() | {"end_image": body()["image"]},
            headers=h,
        ).status_code
        == 503
    )
    assert (
        c.post(
            "/v1/video/generations",
            json=h3_body() | {"end_image": body()["image"]},
            headers=h,
        ).status_code
        == 422
    )
    assert m.TASKS.empty()


def test_wan_tail_cannot_be_silently_disconnected(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    job = c.post(
        "/v1/video/generations", json=body() | {"end_image": body()["image"]}, headers=h
    ).json()["task_id"]
    stored = json.loads(m.lookup(job)["body"])
    with pytest.raises(RuntimeError, match="end_frame_missing"):
        m.video_workflow(job, stored, "first.png")
    path = m.ROOT / "workflows/wan-flf-api.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="workflow_changed"):
        m.video_workflow(job, stored, "first.png", "end.png")


def test_h3_compiles_dialogue_before_queueing_and_keeps_original(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body()
    job = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    payload = json.loads(m.lookup(job)["body"])
    assert payload["raw_prompt"] == request["prompt"]
    assert "<d>[Chinese]谁要造反？</d>" in payload["prompt"]
    assert payload["prompt_format_version"]
    assert payload["prompt"] != payload["raw_prompt"]


def test_h3_unrecognized_prompt_fails_before_queueing(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body() | {
        "prompt": "Unknown free text followed by unstructured dialogue"
    }
    response = c.post("/v1/video/generations", json=request, headers=h)
    assert response.status_code == 422
    assert "prompt" in response.json()["detail"]
    assert m.TASKS.empty()
    assert list((m.ROOT / "inputs").iterdir()) == []


def test_h3_prompt_compiler_changes_invalidate_cached_output(gateway, monkeypatch):
    import h3_prompt

    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    first = c.post("/v1/video/generations", json=h3_body(), headers=h).json()["task_id"]
    original = h3_prompt.format_h3_prompt
    monkeypatch.setattr(
        h3_prompt, "format_h3_prompt", lambda prompt: original(prompt) + "\n"
    )
    second = c.post("/v1/video/generations", json=h3_body(), headers=h).json()[
        "task_id"
    ]
    assert second != first


def test_h3_compiled_prompt_limit_is_checked_before_queueing(gateway, monkeypatch):
    import h3_prompt

    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    monkeypatch.setattr(h3_prompt, "format_h3_prompt", lambda _: "x" * 6001)
    response = c.post("/v1/video/generations", json=h3_body(), headers=h)
    assert response.status_code == 422
    assert "6000" in response.json()["detail"]
    assert m.TASKS.empty()


@pytest.mark.parametrize("silent", [False, True])
@pytest.mark.parametrize("with_tail", [False, True])
def test_h3_comfy_result_is_published_only_after_audio_verification(
    gateway, monkeypatch, silent, with_tail
):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body()
    if with_tail:
        request["end_image"] = h3_body(color="blue")["image"]
    job = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    payload = json.loads(m.lookup(job)["body"])
    source = m.ROOT / "repos/ComfyUI/output/native.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"mock-comfy-output")
    submitted = []
    uploaded = []

    def response(value):
        return m.httpx.Response(
            200, json=value, request=m.httpx.Request("GET", "http://comfy.test")
        )

    class ComfyClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, url, **kwargs):
            if url == "/upload/image":
                uploaded.append(kwargs["files"]["image"][0])
                name = (
                    "server-renamed-frame.png"
                    if len(uploaded) == 1
                    else "server-renamed-tail.png"
                )
                return response({"name": name})
            assert url == "/prompt"
            submitted.append(kwargs["json"]["prompt"])
            return response({"prompt_id": "native-result"})

        def get(self, url):
            assert url == "/history/native-result"
            return response(
                {
                    "native-result": {
                        "outputs": {"92": {"videos": [{"filename": "native.mp4"}]}}
                    }
                }
            )

    monkeypatch.setattr(m.httpx, "Client", lambda **_: ComfyClient())
    monkeypatch.setattr(m, "start_comfy", lambda: None)
    decoded = h3_probe() | ({"audio_peak": 0.0} if silent else {})
    monkeypatch.setattr(
        m, "probe_video", lambda path, request: m.validate_video_probe(request, decoded)
    )
    if silent:
        with pytest.raises(RuntimeError, match="audio"):
            m.run_video(job, payload)
        assert m.lookup(job)["status"] != "succeeded"
        assert c.get("/v1/artifacts/" + job, headers=h).status_code == 404
    else:
        m.run_video(job, payload)
        result = c.get("/v1/video/generations/" + job, headers=h).json()
        assert result["status"] == "succeeded"
        assert result["metadata"]["has_audio"] is True
        assert result["metadata"]["actual_duration"] == pytest.approx(124 / 24)
        assert c.get("/v1/artifacts/" + job, headers=h).content == source.read_bytes()
    assert len(submitted) == 1
    assert submitted[0]["114"]["inputs"]["image"] == "server-renamed-frame.png"
    assert submitted[0]["104"]["inputs"]["prompt"] == payload["prompt"]
    assert uploaded == [job + ".png"] + ([job + "-end.png"] if with_tail else [])
    if with_tail:
        assert submitted[0]["115"]["inputs"]["image"] == "server-renamed-tail.png"
        assert submitted[0]["104"]["inputs"]["last_frame"] == ["115", 0]


def test_existing_job_database_upgrades_without_losing_results(tmp_path, monkeypatch):
    old_root = tmp_path / "old-worker"
    (old_root / "runtime").mkdir(parents=True)
    with sqlite3.connect(old_root / "runtime/jobs.sqlite") as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,body TEXT,remote_id TEXT,error TEXT,sha256 TEXT,created REAL)"
        )
        db.execute(
            "INSERT INTO jobs(id,kind,status,body,sha256,created) VALUES(?,?,?,?,?,?)",
            (
                "existing",
                "video",
                "succeeded",
                json.dumps(
                    {
                        "model": "wan22-i2v-fp8",
                        "width": 720,
                        "height": 1280,
                        "duration": 5,
                    }
                ),
                "old-digest",
                1,
            ),
        )
    monkeypatch.setenv("ARCREEL_WORKER_ROOT", str(old_root))
    monkeypatch.delenv("ARCREEL_GPU_AUTHORIZED", raising=False)
    spec = importlib.util.spec_from_file_location(
        "old_db_gateway", Path(__file__).with_name("cloud_gateway.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = module.lookup("existing")
    assert row["status"] == "succeeded"
    assert row["sha256"] == "old-digest"
    assert row["metadata"] is None
    client = TestClient(module.app)
    result = client.get(
        "/v1/video/generations/existing",
        headers={"Authorization": "Bearer " + module.TOKEN},
    ).json()
    assert result["metadata"] == {"width": 720, "height": 1280, "duration": 5}
    assert result["sha256"] == "old-digest"
    client.close()


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        ("comfy_error", "failed"),
        ("silent_audio", "failed"),
        ("decode_error", "failed"),
        ("network_timeout", "unknown"),
        ("submit_uncertain", "unknown"),
        ("probe_timeout", "unknown"),
    ],
)
def test_worker_finishes_definite_failures_and_preserves_uncertain_jobs(
    gateway, monkeypatch, failure, status
):
    m, c, h = gateway
    authorize(m, monkeypatch)
    job = c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"]

    def fail(*_):
        m.STOP.set()
        if failure == "silent_audio":
            m.validate_video_probe(h3_body(), h3_probe() | {"audio_peak": 0})
        if failure in ("comfy_error", "decode_error"):
            raise m.DefiniteVideoFailure(failure)
        if failure == "network_timeout":
            raise m.httpx.ReadTimeout("history unavailable")
        if failure == "probe_timeout":
            raise m.subprocess.TimeoutExpired("probe", 120)
        raise RuntimeError("submit_timeout_requires_reconciliation")

    monkeypatch.setattr(m, "run_video", fail)
    m.worker()
    result = c.get("/v1/video/generations/" + job, headers=h).json()
    assert result["status"] == status
    assert result["error"]
    assert "url" not in result
    assert c.get("/v1/artifacts/" + job, headers=h).status_code == 404
    assert not m.GPU.locked()
    assert m.TASKS.unfinished_tasks == 0
    assert (
        c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"] == job
    )
    assert m.TASKS.empty()


def test_comfy_explicit_execution_error_is_a_definite_failure(gateway, monkeypatch):
    m, c, h = gateway
    authorize(m, monkeypatch)
    job = c.post("/v1/video/generations", json=body(), headers=h).json()["task_id"]
    client_type = m.httpx.Client

    def handle(request):
        if request.url.path == "/upload/image":
            return m.httpx.Response(200, json={"name": "uploaded.png"})
        if request.url.path == "/prompt":
            return m.httpx.Response(200, json={"prompt_id": "failed-job"})
        assert request.url.path == "/history/failed-job"
        return m.httpx.Response(
            200, json={"failed-job": {"status": {"status_str": "error"}}}
        )

    monkeypatch.setattr(
        m.httpx,
        "Client",
        lambda **kwargs: client_type(**kwargs, transport=m.httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(m, "start_comfy", lambda: None)
    with pytest.raises(m.DefiniteVideoFailure, match="comfy_execution_failed"):
        m.run_video(job, json.loads(m.lookup(job)["body"]))


@pytest.mark.parametrize("failure", ["decode", "invalid_json", "incomplete_result"])
def test_completed_output_probe_failure_is_definite(gateway, monkeypatch, failure):
    m, _, _ = gateway

    def fake_probe(*args, **kwargs):
        if failure == "decode":
            raise m.subprocess.CalledProcessError(1, args[0], stderr="InvalidDataError")
        output = "not-json" if failure == "invalid_json" else "{}"
        return m.subprocess.CompletedProcess(args[0], 0, stdout=output)

    monkeypatch.setattr(m.subprocess, "run", fake_probe)
    with pytest.raises(m.DefiniteVideoFailure):
        m.probe_video(Path("completed.mp4"), h3_body())


def test_probe_timeout_keeps_reconciliation_semantics(gateway, monkeypatch):
    m, _, _ = gateway

    def timeout(*args, **kwargs):
        raise m.subprocess.TimeoutExpired(args[0], 120)

    monkeypatch.setattr(m.subprocess, "run", timeout)
    with pytest.raises(m.subprocess.TimeoutExpired):
        m.probe_video(Path("completed.mp4"), h3_body())


def test_h3_single_frame_keeps_previous_request_identity(gateway, monkeypatch):
    from h3_prompt import H3_PROMPT_FORMAT_VERSION, format_h3_prompt

    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body()
    previous = request | {"n": 1}
    raw = base64.b64decode(previous.pop("image").split(",", 1)[1])
    previous.pop("generate_audio")
    previous.update(
        raw_prompt=request["prompt"],
        prompt=format_h3_prompt(request["prompt"]),
        prompt_format_version=H3_PROMPT_FORMAT_VERSION,
        image_sha256=hashlib.sha256(raw).hexdigest(),
        workflow_sha256=hashlib.sha256(
            (m.ROOT / "workflows/h3-api.json").read_bytes()
        ).hexdigest(),
        source_lock_sha256=hashlib.sha256(
            (m.ROOT / "manifests/source-lock.json").read_bytes()
        ).hexdigest(),
        model_manifest_sha256=hashlib.sha256(
            (m.ROOT / "manifests/h3-models.json").read_bytes()
        ).hexdigest(),
    )
    old_id = hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()
    response = c.post("/v1/video/generations", json=request, headers=h)
    assert response.json()["task_id"] == old_id
    assert json.loads(m.lookup(old_id)["body"]) == previous


@pytest.mark.parametrize(("width", "height"), [(576, 1024), (1024, 576), (576, 576)])
def test_h3_flf_maps_both_frames_audio_and_final_frame_time(
    gateway, monkeypatch, width, height
):
    from h3_prompt import H3_FLF_PROMPT_FORMAT_VERSION

    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body(width, height) | {
        "end_image": h3_body(width, height, "blue")["image"]
    }
    response = c.post("/v1/video/generations", json=request, headers=h)
    assert response.status_code == 200
    job = response.json()["task_id"]
    assert (
        c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
        == job
    )
    stored = json.loads(m.lookup(job)["body"])
    assert stored["end_image_sha256"] != stored["image_sha256"]
    assert stored["last_frame_time_seconds"] == 5.125
    assert stored["prompt_format_version"] == H3_FLF_PROMPT_FORMAT_VERSION
    assert (
        "Picture 2 (from Shot 1) aligns with the 5.125-second mark" in stored["prompt"]
    )
    assert "<d>[Chinese]谁要造反？</d>" in stored["prompt"]
    with Image.open(m.ROOT / "inputs" / (job + "-end.png")) as end:
        assert end.size == (width, height)
        assert end.getpixel((0, 0)) == (0, 0, 255)
    graph, output_node = m.video_workflow(
        job, stored, "first-upload.png", "last-upload.png"
    )
    assert m.workflow_name(stored) == "h3-flf-api.json"
    assert graph["104"]["inputs"]["first_frame"] == ["114", 0]
    assert graph["104"]["inputs"]["last_frame"] == ["115", 0]
    assert graph["114"]["inputs"]["image"] == "first-upload.png"
    assert graph["115"]["inputs"]["image"] == "last-upload.png"
    assert graph["104"]["inputs"]["length"] == 124
    assert graph["91"]["inputs"]["audio"] == ["23", 0]
    assert output_node == "92"
    changed = request | {"end_image": h3_body(width, height, "red")["image"]}
    assert (
        c.post("/v1/video/generations", json=changed, headers=h).json()["task_id"]
        != job
    )


@pytest.mark.parametrize(
    "tail",
    [
        "",
        "https://example.com/tail.png",
        "data:image/png;base64,invalid",
        h3_body(577, 1024)["image"],
        h3_body(576, 1023)["image"],
    ],
)
def test_h3_bad_or_slightly_mismatched_tail_never_queues(gateway, monkeypatch, tail):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    result = c.post(
        "/v1/video/generations", json=h3_body() | {"end_image": tail}, headers=h
    )
    assert result.status_code == 422
    assert m.TASKS.empty()
    assert list((m.ROOT / "inputs").iterdir()) == []


def test_h3_flf_requires_separate_template_without_blocking_single_frame(
    gateway, monkeypatch
):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    (m.ROOT / "workflows/h3-flf-api.json").unlink()
    request = h3_body() | {"end_image": h3_body()["image"]}
    assert c.post("/v1/video/generations", json=request, headers=h).status_code == 503
    assert m.TASKS.empty()
    assert c.post("/v1/video/generations", json=h3_body(), headers=h).status_code == 200


def test_h3_flf_identity_uses_only_its_selected_template(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body() | {"end_image": h3_body()["image"]}
    job = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    for name in ("h3-api.json", "wan-api.json", "wan-flf-api.json"):
        path = m.ROOT / "workflows" / name
        path.write_bytes(path.read_bytes() + b"\n")
    assert (
        c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
        == job
    )
    path = m.ROOT / "workflows/h3-flf-api.json"
    path.write_text(path.read_text() + "\n")
    assert (
        c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
        != job
    )


def test_h3_flf_cannot_silently_drop_tail_input(gateway, monkeypatch):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    job = c.post(
        "/v1/video/generations",
        json=h3_body() | {"end_image": h3_body()["image"]},
        headers=h,
    ).json()["task_id"]
    stored = json.loads(m.lookup(job)["body"])
    with pytest.raises(RuntimeError, match="end_frame_missing"):
        m.video_workflow(job, stored, "first.png")
    path = m.ROOT / "workflows/h3-flf-api.json"
    graph = json.loads(path.read_text())
    del graph["104"]["inputs"]["last_frame"]
    path.write_text(json.dumps(graph))
    with pytest.raises(RuntimeError, match="workflow_changed"):
        m.video_workflow(job, stored, "first.png", "end.png")
    stored.pop("workflow_sha256")
    with pytest.raises(RuntimeError, match="h3_end_frame_not_connected"):
        m.video_workflow(job, stored, "first.png", "end.png")


def test_h3_flf_builder_changes_no_models_sampler_or_audio():
    from build_h3_flf_workflow import build_graph

    single = json.loads(Path(__file__).with_name("h3-api.json").read_text())
    combined = build_graph(single)
    assert combined == json.loads(
        Path(__file__).with_name("h3-flf-api.json").read_text()
    )
    del combined["115"]
    del combined["104"]["inputs"]["last_frame"]
    assert combined == single
    assert "last_frame" not in single["104"]["inputs"]


def test_h3_flf_formatter_version_is_part_of_identity(gateway, monkeypatch):
    import h3_prompt

    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    request = h3_body() | {"end_image": h3_body()["image"]}
    job = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    monkeypatch.setattr(
        h3_prompt, "H3_FLF_PROMPT_FORMAT_VERSION", "changed-test-version"
    )
    changed = c.post("/v1/video/generations", json=request, headers=h).json()["task_id"]
    assert changed != job


def test_h3_flf_first_frame_remains_exact_while_wan_keeps_one_percent_tolerance(
    gateway, monkeypatch
):
    m, c, h = gateway
    authorize_h3(m, monkeypatch)
    near_portrait = h3_body(577, 1024)["image"]
    request = h3_body() | {"image": near_portrait, "end_image": h3_body()["image"]}
    assert c.post("/v1/video/generations", json=request, headers=h).status_code == 422
    assert m.TASKS.empty()
    wan = body() | {
        "width": 720,
        "height": 1280,
        "image": near_portrait,
        "end_image": near_portrait,
    }
    assert c.post("/v1/video/generations", json=wan, headers=h).status_code == 200

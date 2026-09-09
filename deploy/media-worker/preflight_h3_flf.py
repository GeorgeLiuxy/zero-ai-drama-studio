"""Inspect H3 first/last wiring and run an isolated CPU contract; never load models."""

import ast
import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(os.environ.get("ARCREEL_WORKER_ROOT", "/root/autodl-tmp/arcreel-worker"))


def main():
    graph = json.loads((ROOT / "workflows/h3-flf-api.json").read_text())
    original = json.loads((ROOT / "workflows/h3-api.json").read_text())
    assert graph["104"]["inputs"]["first_frame"] == ["114", 0]
    assert graph["104"]["inputs"]["last_frame"] == ["115", 0]
    assert graph["115"]["class_type"] == "LoadImage"
    assert graph["91"]["inputs"]["audio"] == ["23", 0]
    assert "last_frame" not in original["104"]["inputs"]
    assert graph["91"]["inputs"]["fps"] == 24
    for node_id in ("6", "13", "11", "24", "9", "17"):
        assert graph[node_id] == original[node_id]
    schemas = json.loads((ROOT / "reports/h3-node-schemas.json").read_text())
    assert "last_frame" in schemas["MiniMaxH3ImageToVideo"]["input"]["optional"]
    # Read the installed source without importing torch on the 2 GB CPU instance.
    source = ast.parse((ROOT / "repos/ComfyUI/comfy_extras/nodes_minimax_h3.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "MiniMaxH3ImageToVideo")
    execute = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "execute")
    assert "last_frame" in [a.arg for a in execute.args.args]
    weights = json.loads((ROOT / "manifests/h3-models.json").read_text())
    for f in weights:
        assert (ROOT / "models" / f["path"]).stat().st_size == f["size"], f["path"]

    from fastapi.testclient import TestClient
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="h3-flf-contract-", dir=ROOT / "tmp") as temporary:
        isolated = Path(temporary)
        for name in ("workflows", "reports", "manifests"):
            (isolated / name).mkdir()
        for name in ("h3-api.json", "h3-flf-api.json"):
            shutil.copy2(ROOT / "workflows" / name, isolated / "workflows" / name)
        for name in ("h3-models.json", "source-lock.json"):
            shutil.copy2(ROOT / "manifests" / name, isolated / "manifests" / name)
        for name in (
            "h3-preparation.json",
            "h3-environment.json",
            "preflight.json",
            "models-ready.json",
        ):
            shutil.copy2(ROOT / "reports" / name, isolated / "reports" / name)
        os.environ["ARCREEL_WORKER_ROOT"] = str(isolated)
        os.environ["ARCREEL_GPU_AUTHORIZED"] = "0"
        sys.path.insert(0, str(ROOT / "scripts"))
        spec = importlib.util.spec_from_file_location("h3_flf_cpu_contract", ROOT / "scripts/cloud_gateway.py")
        gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gateway)
        # Only isolated admission uses a fake ready value. Lifespan/worker never start.
        gateway.ready = lambda: {"ready": True}
        gateway.start_comfy = lambda: (_ for _ in ()).throw(AssertionError("GPU startup forbidden"))
        client = TestClient(gateway.app)

        def picture(color, size=(90, 160)):
            b = io.BytesIO()
            Image.new("RGB", size, color).save(b, format="PNG")
            return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()

        body = {
            "model": "h3-i2v-int8",
            "prompt": "Action: A man turns toward the camera.\nCamera_Motion: Static\nDialogue:\n- Speaker: Man\n  Line: 你好。\n",
            "image": picture("red"),
            "end_image": picture("blue"),
            "width": 576,
            "height": 1024,
            "duration": 5,
            "seed": 0,
            "generate_audio": True,
        }
        headers = {"Authorization": "Bearer " + gateway.TOKEN}
        r = client.post("/v1/video/generations", json=body, headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()["task_id"]
        data = json.loads(gateway.lookup(job)["body"])
        workflow, output = gateway.video_workflow(job, data, "first.png", "last.png")
        assert output == "92"
        assert workflow["115"]["inputs"]["image"] == "last.png"
        assert "<Picture 2>" in data["prompt"]
        assert "5.125" in data["prompt"]
        assert "<d>[Chinese]你好。</d>" in data["prompt"]
        assert (isolated / "inputs" / (job + "-end.png")).exists()
        assert gateway.lookup(job)["status"] == "queued"
        assert client.post("/v1/video/generations", json=body, headers=headers).json()["task_id"] == job
        changed = {**body, "end_image": picture("green")}
        assert client.post("/v1/video/generations", json=changed, headers=headers).json()["task_id"] != job
        bad = {**body, "end_image": picture("blue", (901, 1600))}
        assert client.post("/v1/video/generations", json=bad, headers=headers).status_code == 422
        single = {k: v for k, v in body.items() if k != "end_image"}
        r = client.post("/v1/video/generations", json=single, headers=headers)
        assert r.status_code == 200, r.text
        single_data = json.loads(gateway.lookup(r.json()["task_id"])["body"])
        assert "<Picture 2>" not in single_data["prompt"]
        assert gateway.workflow_name(single_data) == "h3-api.json"
        client.close()
    report = {
        "cpu_contract_passed": True,
        "installed_source_has_last_frame": True,
        "workflow_audio_connected": True,
        "existing_model_sizes_passed": True,
        "strict_frame_ratio_rejection_passed": True,
        "tail_changes_job_identity": True,
        "single_frame_compatibility_passed": True,
        "gpu_inference_submitted": False,
        "files": {
            n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest()
            for n in (
                "scripts/cloud_gateway.py",
                "scripts/h3_prompt.py",
                "workflows/h3-flf-api.json",
            )
        },
    }
    (ROOT / "reports/h3-flf-preflight.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()

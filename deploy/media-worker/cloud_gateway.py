"""Single-process GPU gateway. Fixed workflows, persistent jobs, no arbitrary URLs or paths."""

import base64
import hashlib
import io
import json
import logging
import math
import os
import queue
import secrets
import signal
import socket
import sqlite3
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)
ROOT = Path(os.environ.get("ARCREEL_WORKER_ROOT", "/root/autodl-tmp/arcreel-worker"))
for d in ["runtime", "config", "inputs", "outputs", "logs"]:
    (ROOT / d).mkdir(parents=True, exist_ok=True)
KEY = ROOT / "config" / "gateway.key"
if not KEY.exists():
    fd = os.open(KEY, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32))
TOKEN = KEY.read_text().strip()
GPU = threading.Lock()
SUBMIT = threading.Lock()
TASKS = queue.Queue(maxsize=32)
COMFY = None
STOP = threading.Event()
DB = ROOT / "runtime" / "jobs.sqlite"
VIDEO_MODELS = {
    "wan22-i2v-fp8": {"workflow": "wan-api.json", "fps": 16, "audio": False},
    "h3-i2v-int8": {"workflow": "h3-api.json", "fps": 24, "audio": True},
}


class DefiniteVideoFailure(RuntimeError):
    """Execution or output validation has conclusively failed, with no live job to reconcile."""


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


with connect() as db:
    db.execute(
        "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,body TEXT,remote_id TEXT,error TEXT,sha256 TEXT,created REAL)"
    )
    if "metadata" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
        db.execute("ALTER TABLE jobs ADD COLUMN metadata TEXT")


def auth(authorization: str | None = Header(default=None)):
    if not authorization or not secrets.compare_digest(
        authorization, "Bearer " + TOKEN
    ):
        raise HTTPException(401, "authentication_required")


def enabled():
    return os.environ.get("ARCREEL_GPU_AUTHORIZED") == "1"


def update(job, **values):
    with connect() as db:
        db.execute(
            "UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
            [*values.values(), job],
        )


def lookup(job):
    with connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
    return dict(row) if row else None


def stop_comfy():
    global COMFY
    if COMFY and COMFY.poll() is None:
        os.killpg(COMFY.pid, signal.SIGTERM)
        try:
            COMFY.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(COMFY.pid, signal.SIGKILL)
            COMFY.wait(timeout=10)
    COMFY = None


def start_comfy():
    global COMFY
    if COMFY and COMFY.poll() is None:
        return
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 8188)) == 0:
            raise RuntimeError("port_8188_owned_by_another_process")
    with (ROOT / "logs" / "comfy.log").open("ab") as log:
        COMFY = subprocess.Popen(
            [
                str(ROOT / "envs/video/bin/python"),
                str(ROOT / "repos/ComfyUI/main.py"),
                "--listen",
                "127.0.0.1",
                "--port",
                "8188",
                "--disable-auto-launch",
            ],
            cwd=ROOT / "repos/ComfyUI",
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    for _ in range(120):
        if COMFY.poll() is not None:
            raise RuntimeError("comfy_start_failed")
        try:
            if (
                httpx.get("http://127.0.0.1:8188/system_stats", timeout=2).status_code
                == 200
            ):
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("comfy_start_timeout")


def frame_count(body):
    if body["model"] == "h3-i2v-int8":
        # Pinned ComfyUI H3 source aligns frames to 5 modulo 17 at 24 fps.
        return 5 + 17 * math.ceil((body["duration"] * 24 - 5) / 17)
    return body["duration"] * 16 + 1


def workflow_name(body):
    if body.get("end_image_sha256"):
        return (
            "h3-flf-api.json" if body["model"] == "h3-i2v-int8" else "wan-flf-api.json"
        )
    return VIDEO_MODELS[body["model"]]["workflow"]


def video_workflow(job, body, image_name, end_image_name=None):
    raw = (ROOT / "workflows" / workflow_name(body)).read_bytes()
    if body.get("workflow_sha256") not in (None, hashlib.sha256(raw).hexdigest()):
        raise RuntimeError("workflow_changed_requires_reconciliation")
    workflow = json.loads(raw)
    seed = int(job[:15], 16) if body["seed"] is None else body["seed"]
    if body["model"] == "h3-i2v-int8":
        workflow["114"]["inputs"]["image"] = image_name
        if body.get("end_image_sha256"):
            if not end_image_name:
                raise RuntimeError("end_frame_missing")
            if (
                workflow["104"]["inputs"].get("last_frame") != ["115", 0]
                or workflow.get("115", {}).get("class_type") != "LoadImage"
            ):
                raise RuntimeError("h3_end_frame_not_connected")
            workflow["115"]["inputs"]["image"] = end_image_name
        workflow["104"]["inputs"].update(
            prompt=body["prompt"],
            width=body["width"],
            height=body["height"],
            length=frame_count(body),
        )
        workflow["15"]["inputs"]["noise_seed"] = seed
        # FL2VA always generates joint audio; never silently disconnect its decoder.
        if workflow["91"]["inputs"].get("audio") != ["23", 0]:
            raise RuntimeError("h3_native_audio_not_connected")
        workflow["91"]["inputs"]["fps"] = 24.0
        output_node = "92"
    else:
        workflow["97"]["inputs"]["image"] = image_name
        if body.get("end_image_sha256"):
            if not end_image_name:
                raise RuntimeError("end_frame_missing")
            if workflow["98"]["class_type"] != "WanFirstLastFrameToVideo" or workflow[
                "98"
            ]["inputs"].get("end_image") != ["99", 0]:
                raise RuntimeError("wan_end_frame_not_connected")
            workflow["99"]["inputs"]["image"] = end_image_name
        workflow["93"]["inputs"]["text"] = body["prompt"]
        workflow["98"]["inputs"].update(
            width=body["width"], height=body["height"], length=frame_count(body)
        )
        workflow["86"]["inputs"]["noise_seed"] = seed
        output_node = "108"
    workflow[output_node]["inputs"]["filename_prefix"] = "arcreel/" + job
    return workflow, output_node


# Runs in the existing video environment, keeping PyAV/numpy out of the gateway env.
VIDEO_PROBE = r"""
import av, json, numpy as np, sys
with av.open(sys.argv[1]) as container:
    if not container.streams.video:
        raise RuntimeError("video_stream_missing")
    stream = container.streams.video[0]
    result = dict(width=stream.width, height=stream.height,
                  fps=float(stream.average_rate or 0),
                  frames=sum(1 for _ in container.decode(stream)),
                  has_audio=bool(container.streams.audio))
result.update(audio_frames=0, audio_samples=0, audio_duration=0.0,
              audio_peak=0.0, audio_finite=True)
if result["has_audio"]:
    with av.open(sys.argv[1]) as container:
        stream = container.streams.audio[0]
        result.update(audio_sample_rate=stream.rate, audio_channels=stream.channels)
        for frame in container.decode(stream):
            samples = frame.to_ndarray()
            values = samples.astype(np.float64)
            if np.issubdtype(samples.dtype, np.integer):
                values /= max(abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max)
            result["audio_frames"] += 1
            result["audio_samples"] += frame.samples
            result["audio_finite"] &= bool(np.isfinite(values).all())
            result["audio_peak"] = max(result["audio_peak"], float(np.max(np.abs(values))))
        result["audio_duration"] = result["audio_samples"] / result["audio_sample_rate"]
print(json.dumps(result, allow_nan=False))
"""


def validate_video_probe(body, probe):
    spec = VIDEO_MODELS[body["model"]]
    if (probe["width"], probe["height"]) != (body["width"], body["height"]):
        raise DefiniteVideoFailure("output_dimensions_differ_from_request")
    if abs(probe["fps"] - spec["fps"]) > 0.001:
        raise DefiniteVideoFailure("output_fps_differ_from_workflow")
    if probe["frames"] != frame_count(body):
        raise DefiniteVideoFailure("output_frame_count_differ_from_workflow")
    if spec["audio"] and not (
        probe["has_audio"]
        and probe["audio_frames"] > 0
        and probe["audio_finite"]
        and probe["audio_peak"] > 1e-5
    ):
        raise DefiniteVideoFailure("h3_native_audio_missing_silent_or_invalid")
    if (
        spec["audio"]
        and abs(probe["audio_duration"] - probe["frames"] / probe["fps"]) > 0.25
    ):
        raise DefiniteVideoFailure("h3_native_audio_duration_does_not_match_video")
    return {
        **probe,
        "actual_duration": probe["frames"] / probe["fps"],
        "audio_mode": "native" if spec["audio"] else "none",
    }


def probe_video(path, body):
    try:
        probe = subprocess.run(
            [str(ROOT / "envs/video/bin/python"), "-c", VIDEO_PROBE, str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        raise DefiniteVideoFailure("output_could_not_be_decoded") from exc
    try:
        return validate_video_probe(body, json.loads(probe.stdout))
    except (ValueError, KeyError, TypeError) as exc:
        raise DefiniteVideoFailure("invalid_output_probe_result") from exc


def run_video(job, body):
    # Validate the persistent workflow before starting or submitting any model work.
    end_path = (
        ROOT / "inputs" / (job + "-end.png") if body.get("end_image_sha256") else None
    )
    if end_path is not None and not end_path.is_file():
        raise RuntimeError("end_frame_file_missing")
    video_workflow(job, body, job + ".png", end_path.name if end_path else None)
    start_comfy()
    with httpx.Client(base_url="http://127.0.0.1:8188", timeout=30) as client:
        with (ROOT / "inputs" / (job + ".png")).open("rb") as f:
            res = client.post(
                "/upload/image", files={"image": (job + ".png", f, "image/png")}
            )
        res.raise_for_status()
        name = res.json()["name"]
        end_name = None
        if end_path is not None:
            with end_path.open("rb") as f:
                res = client.post(
                    "/upload/image", files={"image": (end_path.name, f, "image/png")}
                )
            res.raise_for_status()
            end_name = res.json()["name"]
        workflow, output_node = video_workflow(job, body, name, end_name)
        # The persistent request ID remains in Comfy history if the following write is interrupted.
        res = client.post(
            "/prompt",
            json={
                "prompt": workflow,
                "client_id": "arcreel",
                "extra_data": {"arcreel_job_id": job},
            },
        )
        res.raise_for_status()
        remote_id = res.json()["prompt_id"]
        update(job, remote_id=remote_id)
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            res = client.get("/history/" + remote_id)
            res.raise_for_status()
            history = res.json().get(remote_id)
            if history:
                if history.get("status", {}).get("status_str") == "error":
                    raise DefiniteVideoFailure("comfy_execution_failed")
                output = history.get("outputs", {}).get(output_node, {})
                items = (
                    output.get("images")
                    or output.get("gifs")
                    or output.get("videos")
                    or []
                )
                if items:
                    item = items[0]
                    path = (
                        ROOT
                        / "repos/ComfyUI/output"
                        / item.get("subfolder", "")
                        / item["filename"]
                    ).resolve()
                    if not path.is_relative_to(
                        (ROOT / "repos/ComfyUI/output").resolve()
                    ):
                        raise RuntimeError("invalid_output_path")
                    dest = ROOT / "outputs" / (job + ".mp4")
                    import shutil

                    shutil.copyfile(path, dest.with_suffix(".part"))
                    dest.with_suffix(".part").replace(dest)
                    metadata = probe_video(dest, body)
                    with dest.open("rb") as f:
                        digest = hashlib.file_digest(f, "sha256").hexdigest()
                    update(
                        job,
                        status="succeeded",
                        sha256=digest,
                        metadata=json.dumps(metadata),
                    )
                    return
            time.sleep(3)
    raise RuntimeError("video_timeout_requires_reconciliation")


def worker():
    while not STOP.is_set():
        try:
            job = TASKS.get(timeout=1)
        except queue.Empty:
            continue
        try:
            with GPU:
                row = lookup(job)
                if not row or row["status"] != "queued":
                    continue
                update(job, status="running")
                try:
                    run_video(job, json.loads(row["body"]))
                except DefiniteVideoFailure as exc:
                    logger.exception("Video job %s failed", job)
                    update(
                        job,
                        status="failed",
                        error=type(exc).__name__ + ": " + str(exc)[:160],
                    )
                except Exception as exc:
                    logger.exception("Video job %s requires reconciliation", job)
                    # Conservatively retain unknown: a timed-out submit may already have reached Comfy.
                    update(
                        job,
                        status="unknown",
                        error=type(exc).__name__ + ": " + str(exc)[:160],
                    )
        finally:
            TASKS.task_done()


@asynccontextmanager
async def lifespan(app):
    import fcntl

    guard = (ROOT / "runtime" / "gateway.lock").open("a")
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with connect() as db:
        db.execute(
            "UPDATE jobs SET status='unknown',error='gateway_restarted' WHERE status='running'"
        )
        queued = db.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created"
        ).fetchall()
    if enabled():
        for row in queued:
            TASKS.put_nowait(row["id"])
        threading.Thread(target=worker, daemon=True).start()
    try:
        yield
    finally:
        STOP.set()
        if not GPU.locked():
            stop_comfy()
        guard.close()


app = FastAPI(title="ArcReel cloud execution gateway", lifespan=lifespan)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    weights = (ROOT / "reports" / "models-ready.json").exists()
    preflight = ROOT / "reports" / "preflight.json"
    try:
        prepared = (
            preflight.exists()
            and json.loads(preflight.read_text()).get("prepared") is True
        )
    except (OSError, ValueError):
        prepared = False
    return {
        "ready": enabled() and weights and prepared,
        "gpu_authorized": enabled(),
        "preparation_checked": prepared,
        "video_weights_verified": weights,
        "busy": GPU.locked(),
        "queued": TASKS.qsize(),
    }


def h3_prepared():
    try:
        weights = json.loads((ROOT / "reports/h3-preparation.json").read_text())
        environment = json.loads((ROOT / "reports/h3-environment.json").read_text())
        return (
            weights.get("status") == "download_verified"
            and environment.get("node_schema_passed") is True
            and (ROOT / "manifests/h3-models.json").is_file()
            and (ROOT / "workflows/h3-api.json").is_file()
        )
    except (OSError, ValueError):
        return False


class Video(BaseModel):
    model: str
    prompt: str = Field(min_length=1, max_length=6000)
    image: str = Field(max_length=28_000_000)
    end_image: str | None = Field(default=None, min_length=1, max_length=28_000_000)
    width: int = 640
    height: int = 640
    duration: int = 5
    n: int = 1
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    generate_audio: bool | None = None

    @model_validator(mode="after")
    def check(self):
        if self.model not in VIDEO_MODELS or self.n != 1:
            raise ValueError("unsupported model/batch/duration")
        spec = VIDEO_MODELS[self.model]
        if self.generate_audio is not None and self.generate_audio != spec["audio"]:
            raise ValueError("audio mode is fixed by the selected model")
        if self.model == "h3-i2v-int8":
            if self.duration not in (5, 8, 10, 12):
                raise ValueError(
                    "H3 supports nominal 5, 8, 10, or 12 seconds, aligned to 5 modulo 17 frames at 24 fps"
                )
            if (self.width, self.height) not in (
                (576, 1024),
                (1024, 576),
                (576, 576),
                (864, 1536),
                (1536, 864),
                (864, 864),
            ):
                raise ValueError(
                    "H3 requires an aligned 9:16, 16:9, or square canvas at short edge 576 or 864"
                )
            return self
        if self.duration not in (3, 5):
            raise ValueError("unsupported model/batch/duration")
        if (
            min(self.width, self.height) < 256
            or max(self.width, self.height) > 1280
            or self.width * self.height > 921600
            or self.width % 8
            or self.height % 8
        ):
            raise ValueError("unsupported resolution")
        # ArcReel derives dimensions in multiples of 8; Wan latents require 16.
        divisor = math.gcd(self.width, self.height)
        scale = divisor // 16 * 16
        self.width = self.width // divisor * scale
        self.height = self.height // divisor * scale
        if min(self.width, self.height) < 256:
            raise ValueError("aspect ratio cannot fit aligned resolution")
        return self


@app.post("/v1/video/generations", dependencies=[Depends(auth)])
def submit(body: Video):
    if not ready()["ready"]:
        raise HTTPException(503, "GPU preparation/authorization required")
    if body.model == "h3-i2v-int8" and not h3_prepared():
        raise HTTPException(503, "H3 weight and node preparation required")
    from PIL import Image

    if not body.image.startswith(
        ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
    ):
        raise HTTPException(422, "data_uri_image_required")
    try:
        raw = base64.b64decode(body.image.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > 16_000_000:
                raise ValueError("image too large")
            img.load()
            picture = img.convert("RGB")
    except Exception as exc:
        raise HTTPException(422, "invalid_image") from exc
    if body.model == "h3-i2v-int8" and (
        picture.width * body.height != picture.height * body.width
    ):
        raise HTTPException(
            422, "H3 first frame aspect ratio must match the requested canvas"
        )
    end_picture = None
    end_raw = None
    if body.end_image is not None:
        tail_workflow = (
            "h3-flf-api.json" if body.model == "h3-i2v-int8" else "wan-flf-api.json"
        )
        if not (ROOT / "workflows" / tail_workflow).is_file():
            raise HTTPException(
                503, "selected model first-last workflow is not deployed"
            )
        if not body.end_image.startswith(
            (
                "data:image/png;base64,",
                "data:image/jpeg;base64,",
                "data:image/webp;base64,",
            )
        ):
            raise HTTPException(422, "data_uri_end_image_required")
        try:
            end_raw = base64.b64decode(body.end_image.split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(end_raw)) as img:
                if img.width * img.height > 16_000_000:
                    raise ValueError("end image too large")
                img.load()
                end_picture = img.convert("RGB")
        except Exception as exc:
            raise HTTPException(422, "invalid_end_image") from exc
        if body.model == "h3-i2v-int8":
            if end_picture.width * body.height != end_picture.height * body.width:
                raise HTTPException(
                    422, "H3 last frame aspect ratio must match the requested canvas"
                )
        else:
            for frame in (picture, end_picture):
                if (
                    abs((frame.width / frame.height) / (body.width / body.height) - 1)
                    > 0.01
                ):
                    raise HTTPException(
                        422,
                        "first and last frames must match the requested aspect ratio (1% tolerance)",
                    )
    payload = body.model_dump()
    payload.pop("image")
    payload.pop("end_image")
    if end_raw is not None:
        payload["end_image_sha256"] = hashlib.sha256(end_raw).hexdigest()
    # Fixed per model; omit to retain existing Wan idempotency keys.
    payload.pop("generate_audio")
    if body.model == "h3-i2v-int8":
        from h3_prompt import H3_PROMPT_FORMAT_VERSION, format_h3_prompt

        try:
            prompt_format_version = H3_PROMPT_FORMAT_VERSION
            if end_raw is not None:
                from h3_prompt import H3_FLF_PROMPT_FORMAT_VERSION

                last_frame_time_seconds = (frame_count(payload) - 1) / 24
                compiled_prompt = format_h3_prompt(
                    body.prompt, last_frame_time_seconds=last_frame_time_seconds
                )
                prompt_format_version = H3_FLF_PROMPT_FORMAT_VERSION
                payload["last_frame_time_seconds"] = last_frame_time_seconds
            else:
                compiled_prompt = format_h3_prompt(body.prompt)
        except ValueError as exc:
            raise HTTPException(
                422, "H3 prompt cannot be compiled: " + str(exc)
            ) from exc
        if len(compiled_prompt) > 6000:
            raise HTTPException(422, "compiled H3 prompt exceeds 6000 characters")
        payload.update(
            raw_prompt=body.prompt,
            prompt=compiled_prompt,
            prompt_format_version=prompt_format_version,
        )
    payload["image_sha256"] = hashlib.sha256(raw).hexdigest()
    payload["workflow_sha256"] = hashlib.sha256(
        (ROOT / "workflows" / workflow_name(payload)).read_bytes()
    ).hexdigest()
    payload["source_lock_sha256"] = hashlib.sha256(
        (ROOT / "manifests" / "source-lock.json").read_bytes()
    ).hexdigest()
    if body.model == "h3-i2v-int8":
        payload["model_manifest_sha256"] = hashlib.sha256(
            (ROOT / "manifests" / "h3-models.json").read_bytes()
        ).hexdigest()
    job = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with SUBMIT:
        if lookup(job):
            return {"task_id": job}
        with connect() as db:
            accepted = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
        if TASKS.full() or accepted >= 32:
            raise HTTPException(429, "queue_full")
        picture.save(ROOT / "inputs" / (job + ".png"))
        if end_picture is not None:
            end_picture.save(ROOT / "inputs" / (job + "-end.png"))
        with connect() as db:
            db.execute(
                "INSERT INTO jobs(id,kind,status,body,created) VALUES(?,?,?,?,?)",
                (job, "video", "queued", json.dumps(payload), time.time()),
            )
        TASKS.put_nowait(job)
    return {"task_id": job}


@app.get("/v1/video/generations/{job}", dependencies=[Depends(auth)])
def poll(job: str, request: Request):
    row = lookup(job)
    if not row:
        raise HTTPException(404, "not_found")
    result = {"task_id": job, "status": row["status"], "error": row["error"]}
    if row["kind"] == "video":
        params = json.loads(row["body"])
        result["metadata"] = {k: params[k] for k in ("width", "height", "duration")}
        if params["model"] == "h3-i2v-int8":
            result["metadata"].update(
                model=params["model"],
                requested_duration=params["duration"],
                duration=frame_count(params) / 24,
                fps=24,
                audio_mode="native",
            )
        if row.get("metadata"):
            result["metadata"].update(json.loads(row["metadata"]))
    if row["status"] == "succeeded":
        result.update(
            url=str(request.base_url) + "v1/artifacts/" + job, sha256=row["sha256"]
        )
    return result


@app.get("/v1/artifacts/{job}", dependencies=[Depends(auth)])
def artifact(job: str):
    row = lookup(job)
    if not row or row["status"] != "succeeded":
        raise HTTPException(404, "not_found")
    suffix = ".mp4" if row["kind"] == "video" else ".wav"
    return FileResponse(
        ROOT / "outputs" / (job + suffix),
        headers={"X-Content-SHA256": row["sha256"] or "", "X-Job-ID": job},
    )


class Speech(BaseModel):
    model: str
    input: str = Field(min_length=1, max_length=500)
    voice: str
    response_format: str = "wav"
    speed: float = Field(default=1, ge=0.5, le=2)


@app.post("/v1/audio/speech", dependencies=[Depends(auth)])
def speech(body: Speech):
    if not ready()["ready"]:
        raise HTTPException(503, "GPU preparation/authorization required")
    if body.model != "cosyvoice3" or body.response_format != "wav":
        raise HTTPException(422, "unsupported_model_or_format")
    voices_path = ROOT / "config" / "voices.json"
    voices = json.loads(voices_path.read_text()) if voices_path.exists() else {}
    if body.voice not in voices:
        raise HTTPException(422, "voice_not_configured")
    basis = {"request": body.model_dump(), "voice": voices[body.voice]}
    ref = (ROOT / "inputs" / voices[body.voice]["reference"]).resolve()
    if not ref.is_relative_to((ROOT / "inputs").resolve()) or not ref.is_file():
        raise HTTPException(503, "voice_reference_missing")
    basis["reference_sha256"] = hashlib.sha256(ref.read_bytes()).hexdigest()
    basis["model_manifest_sha256"] = hashlib.sha256(
        (ROOT / "manifests" / "cosyvoice-models.json").read_bytes()
    ).hexdigest()
    job = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()

    def cached():
        row = lookup(job)
        if row:
            if row["status"] == "succeeded":
                return artifact(job)
            raise HTTPException(409, {"task_id": job, "status": row["status"]})
        return None

    result = cached()
    if result:
        return result
    if not GPU.acquire(blocking=False):
        raise HTTPException(429, "GPU_busy", headers={"Retry-After": "15"})
    try:
        result = cached()
        if result:
            return result
        stop_comfy()
        with connect() as db:
            db.execute(
                "INSERT INTO jobs(id,kind,status,body,created) VALUES(?,?,?,?,?)",
                (job, "audio", "running", json.dumps(basis), time.time()),
            )
        output = ROOT / "outputs" / (job + ".wav")
        params = body.model_dump()
        params["output"] = str(output)
        try:
            with (ROOT / "logs" / (job + ".log")).open("ab") as log:
                subprocess.run(
                    [
                        str(ROOT / "envs/tts/bin/python"),
                        str(ROOT / "scripts/cloud_tts.py"),
                    ],
                    input=json.dumps(params).encode(),
                    stdout=log,
                    stderr=log,
                    timeout=900,
                    check=True,
                )
            with output.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            update(job, status="succeeded", sha256=digest)
        except Exception as exc:
            update(job, status="failed", error=type(exc).__name__)
            raise HTTPException(500, {"task_id": job, "error": "tts_failed"}) from exc
        return artifact(job)
    finally:
        GPU.release()

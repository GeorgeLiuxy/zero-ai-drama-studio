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


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    return db


with connect() as db:
    db.execute(
        "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,body TEXT,remote_id TEXT,error TEXT,sha256 TEXT,created REAL)"
    )


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


def run_video(job, body):
    start_comfy()
    workflow = json.loads((ROOT / "workflows" / "wan-api.json").read_text())
    with httpx.Client(base_url="http://127.0.0.1:8188", timeout=30) as client:
        with (ROOT / "inputs" / (job + ".png")).open("rb") as f:
            res = client.post(
                "/upload/image", files={"image": (job + ".png", f, "image/png")}
            )
        res.raise_for_status()
        name = res.json()["name"]
        workflow["97"]["inputs"]["image"] = name
        workflow["93"]["inputs"]["text"] = body["prompt"]
        workflow["98"]["inputs"].update(
            width=body["width"], height=body["height"], length=body["duration"] * 16 + 1
        )
        workflow["86"]["inputs"]["noise_seed"] = (
            int(job[:15], 16) if body["seed"] is None else body["seed"]
        )
        workflow["108"]["inputs"]["filename_prefix"] = "arcreel/" + job
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
                    raise RuntimeError("comfy_execution_failed")
                output = history.get("outputs", {}).get("108", {})
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
                    check = "import av,sys; c=av.open(sys.argv[1]); s=c.streams.video[0]; assert s.width==int(sys.argv[2]) and s.height==int(sys.argv[3]); assert next(c.decode(s),None) is not None; c.close()"
                    subprocess.run(
                        [
                            str(ROOT / "envs/video/bin/python"),
                            "-c",
                            check,
                            str(dest),
                            str(body["width"]),
                            str(body["height"]),
                        ],
                        check=True,
                        timeout=60,
                    )
                    with dest.open("rb") as f:
                        digest = hashlib.file_digest(f, "sha256").hexdigest()
                    update(job, status="succeeded", sha256=digest)
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


class Video(BaseModel):
    model: str
    prompt: str = Field(min_length=1, max_length=6000)
    image: str = Field(max_length=28_000_000)
    width: int = 640
    height: int = 640
    duration: int = 5
    n: int = 1
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def check(self):
        if self.model != "wan22-i2v-fp8" or self.n != 1 or self.duration not in (3, 5):
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
    payload = body.model_dump()
    payload.pop("image")
    payload["image_sha256"] = hashlib.sha256(raw).hexdigest()
    payload["workflow_sha256"] = hashlib.sha256(
        (ROOT / "workflows" / "wan-api.json").read_bytes()
    ).hexdigest()
    payload["source_lock_sha256"] = hashlib.sha256(
        (ROOT / "manifests" / "source-lock.json").read_bytes()
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

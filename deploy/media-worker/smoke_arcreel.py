"""Exercise ArcReel's real adapters over the SSH tunnel, without changing project defaults."""

import argparse
import asyncio
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from local_connection import configure_local_connection

configure_local_connection()

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent / "ArcReel"
sys.path.insert(0, str(ARC))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorize-gpu", action="store_true")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--resolution", choices=["480p", "720p"], default="480p")
    parser.add_argument("--aspect", choices=["16:9", "9:16", "1:1"], default="16:9")
    args = parser.parse_args()
    if not args.authorize_gpu:
        parser.error("Start the GPU instance and gateway, then pass --authorize-gpu")
    if not args.image.is_file():
        parser.error("First-frame image does not exist")
    import httpx
    from lib.audio_backends.base import AudioSynthesisRequest
    from lib.audio_backends.openai import OpenAIAudioBackend
    from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
    from lib.video_backends.base import VideoGenerationRequest

    data = Path(
        json.loads((ROOT / "reports/local-audit.json").read_text(encoding="utf-8"))[
            "data_dir"
        ]
    )
    with sqlite3.connect((data / ".arcreel.db").as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT api_key,base_url FROM custom_provider WHERE base_url=?",
            ("http://127.0.0.1:19000",),
        ).fetchone()
    if not row:
        raise RuntimeError("Prepared cloud provider is missing")
    key, base = row
    health = httpx.get(base + "/health/ready", timeout=10)
    health.raise_for_status()
    if not health.json()["ready"]:
        raise RuntimeError("Cloud gateway is not GPU ready")
    output = ROOT / "outputs" / ("smoke-" + time.strftime("%Y%m%d-%H%M%S"))
    output.mkdir(parents=True)
    report = {
        "scope": "real ArcReel adapters, not project UI/task ledger validation",
        "demo_voice": True,
        "output": str(output),
        "stages": {},
    }
    definition = json.loads(
        (ARC / "lib/custom_provider/builtin_endpoints/newapi-video.json").read_text(
            encoding="utf-8"
        )
    )
    audio = OpenAIAudioBackend(
        api_key=key, base_url=base + "/v1", model="cosyvoice3", provider_name="custom-1"
    )
    video = DeclarativeVideoBackend(
        api_key=key,
        base_url=base,
        model="wan22-i2v-fp8",
        definition=definition,
        provider="custom-1",
    )

    async def run():
        async def response(stage, value):
            # Persist only the job handle; credentials and image data are never logged.
            if isinstance(value, dict) and value.get("task_id"):
                report["remote_video_job"] = value["task_id"]
                (output / "report.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )

        started = time.monotonic()
        await audio.synthesize(
            AudioSynthesisRequest(
                text="这是云端测试。", voice="alloy", output_path=output / "demo.wav"
            )
        )
        report["stages"]["tts_seconds"] = time.monotonic() - started
        started = time.monotonic()
        await video.generate(
            VideoGenerationRequest(
                prompt="A subtle slow camera movement. Preserve the subject and composition of the reference image.",
                output_path=output / "video.mp4",
                start_image=args.image.resolve(),
                duration_seconds=3,
                resolution=args.resolution,
                aspect_ratio=args.aspect,
                seed=20260908,
                generate_audio=False,
                poll_timeout_seconds=3600,
                on_provider_response=response,
            )
        )
        report["stages"]["video_seconds"] = time.monotonic() - started
        started = time.monotonic()
        await audio.synthesize(
            AudioSynthesisRequest(
                text="视频完成，再次测试配音。",
                voice="alloy",
                output_path=output / "demo-after-video.wav",
            )
        )
        report["stages"]["tts_after_video_seconds"] = time.monotonic() - started

    try:
        asyncio.run(run())
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-i",
                str(output / "video.mp4"),
                "-i",
                str(output / "demo.wav"),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-af",
                "apad",
                "-shortest",
                str(output / "demo-preview.mp4"),
            ],
            check=True,
        )
        probe = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(output / "demo-preview.mp4"),
            ],
            text=True,
        )
        report["preview_probe"] = json.loads(probe)
        report["sha256"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir()
            if p.suffix in (".wav", ".mp4")
        }
        report["passed"] = True
    except Exception as exc:
        report["passed"] = False
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"passed": report.get("passed"), "report": str(output / "report.json")}
            )
        )


if __name__ == "__main__":
    main()

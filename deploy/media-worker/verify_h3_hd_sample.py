"""Validate formal native HD output, presentation bundle and a labelled 1080 export."""

import hashlib
import json
import math
import shutil
import subprocess
import zipfile
from datetime import datetime
from fractions import Fraction

import httpx
from dotenv import dotenv_values
from h3_hd_sample import ARC, PROJECT, REPORT, ROOT

OUTPUT = ROOT / "outputs/h3-hd-E2S05"
VALIDATION = ROOT / "reports/h3-hd-validation.json"


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(argv, *, cwd=None):
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=180,
    )


def probe(path):
    return json.loads(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        ).stdout
    )


def inspect_video(path, dimensions):
    info = probe(path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert [video["width"], video["height"]] == dimensions
    assert Fraction(video["avg_frame_rate"]) == 24
    assert int(video["nb_read_frames"]) == 124
    assert math.isclose(float(video["duration"]), 124 / 24, abs_tol=0.01)
    assert int(audio["sample_rate"]) == 32000 and audio["channels"] == 2
    assert abs(float(audio["duration"]) - 124 / 24) < 0.25
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"])
    return info


def audio_hash(path):
    return run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ]
    ).stdout.strip()


def main():
    record = json.loads(REPORT.read_text(encoding="utf-8"))
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(
        base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=60
    ) as api:
        token = api.post(
            "/auth/token",
            data={
                "username": cfg.get("AUTH_USERNAME") or "admin",
                "password": cfg.get("AUTH_PASSWORD") or "",
            },
        )
        token.raise_for_status()
        api.headers["Authorization"] = "Bearer " + token.json()["access_token"]

        def get(path):
            response = api.get(path)
            response.raise_for_status()
            return response

        task = get("/tasks/" + record["task_id"]).json()["task"]
        assert task["status"] == "succeeded", task["status"]
        project_dir = (ARC / "deploy/projects" / PROJECT).resolve()
        script = get(f"/projects/{PROJECT}/scripts/episode_2.json").json()["script"]
        item = next(s for s in script["scenes"] if s["scene_id"] == "E2S05")
        assert item["utterances"] == record["source_utterances"]
        first = (project_dir / item["generated_assets"]["storyboard_image"]).resolve()
        assert (
            first.is_relative_to(project_dir)
            and digest(first) == record["first_frame_sha256"]
        )
        video = (project_dir / item["generated_assets"]["video_clip"]).resolve()
        assert video.is_relative_to(project_dir)
        versions = json.loads(
            (project_dir / "versions/versions.json").read_text(encoding="utf-8")
        )["videos"]["E2S05"]
        selected = next(
            v
            for v in versions["versions"]
            if v["version"] == versions["current_version"]
        )
        assert selected["execution_task_id"] == record["task_id"]
        assert selected["execution_provider_model_id"] == "h3-i2v-int8"
        old = [
            v
            for v in versions["versions"]
            if v["version"] != versions["current_version"]
        ]
        assert any(
            digest(project_dir / v["file"]) == record["prior_video_sha256"] for v in old
        )
        native_probe = inspect_video(video, [864, 1536])
        route = f"/projects/{PROJECT}/presentations/videos/E2S05"
        presentation = get(route + "?variant=post_production").json()
        assert (
            presentation["currency"] == "current"
            and presentation["provenance"] == "verified"
        )
        assert presentation["speech_mode"] == "character_speech"
        assert (
            presentation["video"]["audio_enabled"] is True
            and presentation["narration_audio"] is None
        )
        assert [cue["text"] for cue in presentation["subtitles"]] == [
            u["text"] for u in item["utterances"]
        ]
        assert presentation["timing"] == "mechanical"
        OUTPUT.mkdir(parents=True, exist_ok=True)
        bundle = OUTPUT / "E2S05_presentation.zip"
        bundle.write_bytes(get(route + "/bundle?variant=post_production").content)
        with zipfile.ZipFile(bundle) as archive:
            assert archive.testzip() is None
            assert hashlib.sha256(
                archive.read("media/video.mp4")
            ).hexdigest() == digest(video)
            (OUTPUT / "subtitles.vtt").write_bytes(archive.read("subtitles.vtt"))
        native = OUTPUT / "E2S05_native_864x1536.mp4"
        shutil.copyfile(video, native)
        (OUTPUT / "presentation.json").write_text(
            json.dumps(presentation, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        final = OUTPUT / "E2S05_export_1080x1920_subtitled.mp4"
        if not final.exists():
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-n",
                    "-i",
                    native.name,
                    "-vf",
                    "scale=1080:1920:flags=lanczos,subtitles=subtitles.vtt:force_style='FontName=Microsoft YaHei,FontSize=18,Outline=1,MarginV=28'",
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    final.name,
                ],
                cwd=OUTPUT,
            )
        final_probe = inspect_video(final, [1080, 1920])
        assert audio_hash(native) == audio_hash(final)
        for index, seconds in enumerate([0.5, 2.5, 4.5]):
            frame = OUTPUT / f"preview-{index + 1}.jpg"
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(seconds),
                    "-i",
                    str(final),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame),
                ]
            )
        volume = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(native),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ]
        ).stderr
        report = {
            "checked_at": datetime.now().astimezone().isoformat(),
            "technical_passed": True,
            "task_id": record["task_id"],
            "provider_job_id": task["provider_job_id"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
            "native_video": str(native),
            "native_probe": native_probe,
            "native_sha256": digest(native),
            "export_video": str(final),
            "export_probe": final_probe,
            "export_sha256": digest(final),
            "export_processing": "Lanczos resize from native 864x1536 plus burned script subtitles; not native 1080p or AI super-resolution",
            "native_audio_preserved": True,
            "decoded_audio_hash": audio_hash(native),
            "audio_volume": [
                line
                for line in volume.splitlines()
                if "mean_volume" in line or "max_volume" in line
            ],
            "old_video_preserved": True,
            "current_version": versions["current_version"],
            "bundle": str(bundle),
            "presentation": presentation,
            "scope": "Single character-dialogue shot. Subtitles use script timing, not ASR. Exact dialogue, lip sync and visual quality need audiovisual review. No new narration synthesis.",
        }
        VALIDATION.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "technical_passed",
                        "native_video",
                        "export_video",
                        "native_audio_preserved",
                        "audio_volume",
                        "current_version",
                    )
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()

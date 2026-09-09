"""Validate one formal long HD sample and prepare local review media."""

import json
import shutil
from fractions import Fraction

import httpx
from dotenv import dotenv_values
from run_episode2_long_sample import ARC, PROJECT, REPORT, ROOT
from verify_h3_hd_sample import digest, probe, run


def main():
    record = json.loads(REPORT.read_text(encoding="utf-8"))
    cfg = dotenv_values(ARC / ".env")
    with httpx.Client(
        base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=45
    ) as api:
        r = api.post(
            "/auth/token",
            data={
                "username": cfg.get("AUTH_USERNAME") or "admin",
                "password": cfg.get("AUTH_PASSWORD") or "",
            },
        )
        r.raise_for_status()
        api.headers["Authorization"] = "Bearer " + r.json()["access_token"]

        def get(path):
            r = api.get(path)
            r.raise_for_status()
            return r.json()

        task = get("/tasks/" + record["task_id"])["task"]
        assert task["status"] == "succeeded", task["status"]
        presentation = get(
            f"/projects/{PROJECT}/presentations/videos/E2S11?variant=post_production"
        )
        assert (
            presentation["currency"] == "current"
            and presentation["provenance"] == "verified"
        )
        assert presentation["speech_mode"] == "character_speech"
        assert (
            presentation["video"]["audio_enabled"]
            and presentation["narration_audio"] is None
        )
    project = (ARC / "deploy/projects" / PROJECT).resolve()
    source = (project / presentation["video"]["artifact_path"]).resolve()
    assert source.is_relative_to(project)
    assert digest(source) == presentation["video"]["content_digest"].removeprefix(
        "sha256-v1:"
    )
    info = probe(source)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (864, 1536)
    assert (
        Fraction(video["avg_frame_rate"]) == 24 and int(video["nb_read_frames"]) == 294
    )
    assert abs(float(video["duration"]) - 12.25) < 0.02
    assert int(audio["sample_rate"]) == 32000 and audio["channels"] == 2
    assert abs(float(audio["duration"]) - 12.25) < 0.25
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(source), "-f", "null", "-"])
    directory = ROOT / "outputs/episode2-long-sample"
    directory.mkdir(parents=True, exist_ok=True)
    native = directory / "E2S11_native_864x1536.mp4"
    shutil.copyfile(source, native)
    (directory / "subtitles.vtt").write_text(
        presentation["subtitles_webvtt"], encoding="utf-8"
    )
    (directory / "presentation.json").write_text(
        json.dumps(presentation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(native),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(directory / "dialogue.wav"),
        ]
    )
    for seconds in (1, 4, 8, 11):
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                str(seconds),
                "-i",
                str(native),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(directory / f"preview-{seconds}.jpg"),
            ]
        )
    volume = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(native),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
    )
    report = {
        "technical_passed": True,
        "task": task,
        "probe": info,
        "presentation": presentation,
        "native_file": str(native),
        "sha256": digest(native),
        "volume": volume.stderr,
        "spoken_content_review": "pending",
        "lip_sync_review": "pending",
        "subtitle_timing": "script-derived mechanical timing, not speech recognition",
    }
    (ROOT / "reports/episode2-long-validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "technical_passed": True,
                "native_file": str(native),
                "duration": video["duration"],
                "spoken_content_review": "pending",
                "lip_sync_review": "pending",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

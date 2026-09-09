"""CPU-only narration/dialogue composition demo from verified project artifacts."""

import hashlib
import json
import math

import httpx
from dotenv import dotenv_values
from h3_hd_sample import ARC, PROJECT, ROOT
from verify_h3_hd_sample import OUTPUT, probe, run


def main():
    cfg = dotenv_values(ARC / ".env")
    project = (ARC / "deploy/projects" / PROJECT).resolve()
    with httpx.Client(
        base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=30
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
        response = api.get(
            f"/projects/{PROJECT}/presentations/videos/E2S01?variant=use_tts"
        )
        response.raise_for_status()
        presentation = response.json()
    assert (
        presentation["currency"] == "current"
        and presentation["provenance"] == "verified"
    )
    assert presentation["speech_mode"] == "narrator_voiceover"
    assert presentation["video"]["audio_enabled"] is False
    assert presentation["narration_audio"]["gain"] == 1.0
    sources = []
    for key in ("video", "narration_audio"):
        source = (project / presentation[key]["artifact_path"]).resolve()
        assert source.is_relative_to(project) and source.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == presentation[key][
            "content_digest"
        ].removeprefix("sha256-v1:")
        sources.append(source)
    original_seconds = presentation["video"]["actual_duration_seconds"]
    seconds = math.ceil(original_seconds * 24) / 24
    assert presentation["narration_audio"]["actual_duration_seconds"] < original_seconds
    directory = ROOT / "outputs/hd-flow-demo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "narration.vtt").write_text(
        presentation["subtitles_webvtt"], encoding="utf-8"
    )
    intro = directory / "narration_1080_export.mp4"
    if not intro.exists():
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-i",
                str(sources[0]),
                "-i",
                str(sources[1]),
                "-filter_complex",
                f"[0:v]fps=24,scale=1080:1920:flags=lanczos,tpad=stop_mode=clone:stop_duration=0.1,trim=duration={seconds},subtitles=narration.vtt:force_style='FontName=Microsoft YaHei,FontSize=18,Outline=1,MarginV=28'[v];[1:a]apad,atrim=duration={seconds}[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "32000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                intro.name,
            ],
            cwd=directory,
        )
    dialogue = OUTPUT / "E2S05_export_1080x1920_subtitled.mp4"
    if not dialogue.is_file():
        print("Narration export prepared; awaiting the single HD dialogue sample.")
        return
    combined = directory / "flow_demo_1080x1920.mp4"
    if not combined.exists():
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-i",
                str(intro),
                "-i",
                str(dialogue),
                "-filter_complex",
                "[0:v]setsar=1,setpts=PTS-STARTPTS[v0];[1:v]setsar=1,setpts=PTS-STARTPTS[v1];[0:a]asetpts=PTS-STARTPTS[a0];[1:a]asetpts=PTS-STARTPTS[a1];[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "32000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(combined),
            ]
        )
    info = probe(combined)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert abs(float(video["duration"]) - (seconds + 124 / 24)) < 0.1
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(combined), "-f", "null", "-"])
    report = {
        "technical_passed": True,
        "output": str(combined),
        "probe": info,
        "parts": [
            "E2S01 existing Wan video with its separately synthesized narration",
            "E2S05 new native 864x1536 H3 dialogue video",
        ],
        "scope": "Two selected shots for pipeline validation, not a complete episode or narrative continuity acceptance. Both exports use Lanczos resizing to 1080x1920.",
        "narration_original_video_seconds": original_seconds,
        "narration_export_seconds": seconds,
        "narration_presentation": presentation,
        "new_gpu_video_jobs": 0,
        "new_tts_jobs": 0,
    }
    (directory / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "technical_passed": True,
                "output": str(combined),
                "duration": video["duration"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

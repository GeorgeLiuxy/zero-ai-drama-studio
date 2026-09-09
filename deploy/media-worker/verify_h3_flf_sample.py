"""Verify one completed formal H3 sample without submitting inference or TTS."""

import hashlib
import json
import math
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from fractions import Fraction

import httpx
from dotenv import dotenv_values
from run_h3_flf_sample import ARC, PROJECT, ROOT

TASK_ID = "444a8553434644c182e63acde7de90ed"
PROVIDER_JOB_ID = "67212d83910ad8390494f167bcbbd737375ef2a716069aa85cd42be6acaf1d2c"
REPORT = ROOT / "reports/h3-flf-validation.json"
OUTPUT = ROOT / "outputs/h3-flf-E1S01"
EXPECTED_SECONDS = 124 / 24


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save_report(report):
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(REPORT)


def local_file(project_dir, relative):
    require(isinstance(relative, str) and bool(relative), "Missing project artifact path")
    result = (project_dir / relative).resolve()
    require(result.is_relative_to(project_dir) and result.is_file(), "Invalid or missing project artifact")
    return result


def elapsed(task):
    def parse(value):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)

    started = task.get("started_at")
    finished = task.get("finished_at")
    return (parse(finished) - parse(started)).total_seconds() if started and finished else None


def validate_media(video_file):
    for executable in ("ffprobe", "ffmpeg"):
        require(shutil.which(executable) is not None, f"{executable} is unavailable")
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video_file),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    info = json.loads(probe.stdout)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    require(len(videos) == 1 and len(audios) == 1, "Expected one video and one native audio stream")
    video, audio = videos[0], audios[0]
    require((video["width"], video["height"]) == (576, 1024), "Video canvas is not 576x1024")
    require(Fraction(video["avg_frame_rate"]) == Fraction(24), "Video frame rate is not 24 fps")
    require(int(video["nb_read_frames"]) == 124, "Video does not decode to 124 frames")
    require(math.isclose(float(video["duration"]), EXPECTED_SECONDS, abs_tol=0.00001), "Video duration mismatch")
    require(int(audio.get("nb_read_frames", "0")) > 0, "No decoded audio frames")
    require(int(audio["sample_rate"]) > 0 and audio["channels"] > 0, "Invalid audio format")
    require(abs(float(audio["duration"]) - EXPECTED_SECONDS) < 0.25, "Audio and video duration mismatch")
    decode = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-i",
            str(video_file),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    require(not decode.stderr.strip(), "Full audio/video decode reported errors")
    return {
        "ffprobe": info,
        "full_audio_video_decode_passed": True,
        "actual_video_seconds": float(video["duration"]),
    }


def validate_presentation(presentation, item):
    require(presentation["unit_id"] == "E1S01", "Unexpected presentation unit")
    require(presentation["variant"] == "post_production", "Unexpected presentation variant")
    require(presentation["currency"] == "current", "Presentation is stale")
    require(presentation["provenance"] == "verified", "Presentation provenance is not verified")
    require(presentation["speech_mode"] == "narrator_voiceover", "Unexpected speech mode")
    require(presentation["video"]["audio_enabled"] is True, "Native environmental audio is disabled")
    require(presentation["video"]["gain"] == 1, "Native environmental audio gain changed")
    require(presentation["narration_audio"] is None, "Unexpected separately synthesized narration")
    require(presentation["timing"] == "mechanical", "Subtitle source is not the script timing projection")
    require(presentation["subtitles_adjustable"] is True, "Subtitles are not editable")
    expected = [u["text"] for u in item["utterances"]]
    require([cue["text"] for cue in presentation["subtitles"]] == expected, "Subtitles differ from script utterances")
    require(all(cue["speaker"] is None for cue in presentation["subtitles"]), "Voiceover has a character speaker")
    seconds = presentation["video"]["duration_microseconds"] / 1_000_000
    require(math.isclose(seconds, EXPECTED_SECONDS, abs_tol=0.00001), "Presentation trims the measured video")


def main():
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "task_id": TASK_ID,
        "provider_job_id": PROVIDER_JOB_ID,
        "project": PROJECT,
        "scene": "E1S01",
        "status": "checking",
        "gpu_requests_submitted": 0,
        "tts_requests_submitted": 0,
    }
    try:
        cfg = dotenv_values(ARC / ".env")
        with httpx.Client(base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=60) as api:
            token = api.post(
                "/auth/token",
                data={"username": cfg.get("AUTH_USERNAME") or "admin", "password": cfg.get("AUTH_PASSWORD") or ""},
            )
            token.raise_for_status()
            api.headers["Authorization"] = "Bearer " + token.json()["access_token"]

            def get(path):
                response = api.get(path)
                require(response.is_success, f"GET {path} returned HTTP {response.status_code}")
                return response

            task = get("/tasks/" + TASK_ID).json()["task"]
            report["task_status"] = task["status"]
            if task["status"] in ("queued", "running", "cancelling"):
                report["status"] = "not_ready"
                save_report(report)
                print(json.dumps(report, ensure_ascii=False))
                return 3
            require(task["status"] == "succeeded", "Formal task did not succeed")
            require(task["provider_job_id"] == PROVIDER_JOB_ID, "Provider task identity changed")
            report["task_elapsed_seconds"] = elapsed(task)
            report["task_started_at"] = task.get("started_at")
            report["task_finished_at"] = task.get("finished_at")
            project_dir = (ARC / "deploy/projects" / PROJECT).resolve()
            script = get(f"/projects/{PROJECT}/scripts/episode_1.json").json()["script"]
            item = next(s for s in script["scenes"] if s["scene_id"] == "E1S01")
            require(all(u["kind"] == "voiceover" for u in item["utterances"]), "Script is no longer voiceover-only")
            require(not item["generated_assets"].get("narration_audio"), "Unexpected formal narration audio binding")
            video_file = local_file(project_dir, item["generated_assets"]["video_clip"])
            submission = json.loads((ROOT / "reports/h3-flf-sample.json").read_text(encoding="utf-8"))
            require(submission["task_id"] == TASK_ID, "Submission report task mismatch")
            input_hashes = {
                name: digest(local_file(project_dir, path)) for name, path in submission["input_paths"].items()
            }
            require(input_hashes == submission["input_hashes"], "Current frames changed since submission")
            report.update(input_hashes=input_hashes, input_paths=submission["input_paths"], video_path=str(video_file))
            versions = json.loads((project_dir / "versions/versions.json").read_text(encoding="utf-8"))["videos"][
                "E1S01"
            ]
            selected = next(v for v in versions["versions"] if v["version"] == versions["current_version"])
            require(selected["execution_task_id"] == TASK_ID, "Current selected video belongs to another task")
            require(selected["execution_provider_model_id"] == "h3-i2v-int8", "Selected video is not H3")
            require(selected["execution_generate_audio"] is True, "Selected video did not request native audio")
            report["selected_video_version"] = versions["current_version"]
            report["media"] = validate_media(video_file)
            route = f"/projects/{PROJECT}/presentations/videos/E1S01"
            presentation = get(route + "?variant=post_production").json()
            validate_presentation(presentation, item)
            require(
                presentation["video"]["version"] == versions["current_version"],
                "Presentation selected another video",
            )
            OUTPUT.mkdir(parents=True, exist_ok=True)
            bundle = OUTPUT / "E1S01_presentation.zip"
            bundle.write_bytes(get(route + "/bundle?variant=post_production").content)
            video_sha = digest(video_file)
            with zipfile.ZipFile(bundle) as archive:
                require(archive.testzip() is None, "Presentation ZIP CRC failed")
                with archive.open("media/video.mp4") as zipped_video:
                    bundle_video_sha = hashlib.file_digest(zipped_video, "sha256").hexdigest()
                require(bundle_video_sha == video_sha, "Bundled video differs from formal project video")
                require(
                    not any(n.startswith("media/narration") for n in archive.namelist()),
                    "Bundle contains narration",
                )
                validate_presentation(json.loads(archive.read("presentation.json")), item)
                subtitle = json.loads(archive.read("subtitles.json"))
                require(subtitle["timing"] == "mechanical", "Bundled subtitles do not use script timing")
                require(
                    [c["text"] for c in subtitle["cues"]] == [u["text"] for u in item["utterances"]],
                    "Bundled subtitles differ from the script",
                )
                members = archive.namelist()
            sample = OUTPUT / "sample.mp4"
            shutil.copyfile(video_file, sample)
            (OUTPUT / "presentation.json").write_text(
                json.dumps(presentation, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            report.update(
                status="passed",
                video_sha256=video_sha,
                bundle_video_sha256=bundle_video_sha,
                bundle_sha256=digest(bundle),
                bundle_path=str(bundle),
                bundle_members=members,
                sample_path=str(sample),
                presentation=presentation,
                audio_scope="Native H3 environmental audio; no separate narrator synthesis was requested.",
                subtitles_source="Script utterances with mechanical timing, not ASR or evidence of audible narration.",
                content_review="Lip behavior, environmental sound quality, and first/last frame visual alignment require separate inspection.",
                known_content_conflict=submission.get("known_content_conflict"),
            )
        save_report(report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "task_elapsed_seconds": report["task_elapsed_seconds"],
                    "sample_path": report["sample_path"],
                    "bundle_path": report["bundle_path"],
                    "report": str(REPORT),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        StopIteration,
        subprocess.SubprocessError,
        httpx.HTTPError,
        zipfile.BadZipFile,
    ) as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        save_report(report)
        print(json.dumps({"status": "failed", "error": report["error"], "report": str(REPORT)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Compose all 19 current episode presentations locally, preserving complete speech."""

import hashlib
import json
import math
import re

import httpx
from dotenv import dotenv_values
from run_episode2_long_sample import ARC, PROJECT, ROOT
from verify_h3_hd_sample import digest, probe, run

from lib.artifact_currency import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter


def subtitle_phrases(cues):
    """Preserve script text while assigning short phrases proportional cue times."""
    result = []
    for cue in cues:
        text = cue["text"]
        phrases = []
        pending = ""
        for clause in re.findall(r"[^，。！？；、,!?;]+[，。！？；、,!?;]*|[，。！？；、,!?;]+", text):
            while len(clause) > 18:
                if pending:
                    phrases.append(pending)
                    pending = ""
                end = 16
                while end < len(clause) and clause[end] in "，。！？；、,!?;：:）)》”’":
                    end += 1
                phrases.append(clause[:end])
                clause = clause[end:]
            if pending and len(pending + clause) > 18:
                phrases.append(pending)
                pending = ""
            pending += clause
        if pending:
            phrases.append(pending)
        assert "".join(phrases) == text
        assert phrases
        used = 0
        for phrase in phrases:
            start = round(cue["duration_microseconds"] * used / len(text))
            used += len(phrase)
            end = round(cue["duration_microseconds"] * used / len(text))
            result.append(
                {
                    **cue,
                    "text": phrase,
                    "start_microseconds": cue["start_microseconds"] + start,
                    "duration_microseconds": end - start,
                }
            )
    return result


def ass_subtitles(cues):
    def stamp(us):
        seconds, cs = divmod(round(us / 10000), 100)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02}:{seconds:02}.{cs:02}"

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,52,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,145,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        assert not any(x in c["text"] for x in ("{", "}", "\\", "\n"))
        lines.append(
            f"Dialogue: 0,{stamp(c['start_microseconds'])},{stamp(c['start_microseconds'] + c['duration_microseconds'])},Default,,0,0,0,,{c['text']}"
        )
    return header + "\n".join(lines) + "\n"


def local_tail_hold_presentation(api, scene, project_dir):
    """Assemble a local edit from verified sources without changing project claims."""
    sid = scene["scene_id"]
    assert sid in {"E2S07", "E2S13"}
    assert len(scene["utterances"]) == 1
    assert scene["utterances"][0]["kind"] == "voiceover"
    r = api.get(f"/projects/{PROJECT}/presentations/videos/{sid}?variant=post_production")
    r.raise_for_status()
    p = r.json()
    assert p["currency"] == "current"
    assert p["provenance"] == "verified"
    r = api.get(f"/projects/{PROJECT}/versions/audio/{sid}")
    r.raise_for_status()
    versions = r.json()
    selected = next(v for v in versions["versions"] if v["version"] == versions["current_version"])
    relative = scene["generated_assets"]["narration_audio"]
    key = ArtifactKey.episode_audio(2, sid)
    comparison = ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=relative)
    assert str(comparison.status) == "current"
    entry = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
    assert entry is not None
    assert entry.basis_digest == selected["artifact_audio_basis"]["digest"]
    path = (project_dir / selected["file"]).resolve()
    assert path.is_relative_to(project_dir)
    assert digest(path) == digest(project_dir / relative)
    assert selected["prompt"] == scene["utterances"][0]["text"]
    assert selected["artifact_episode"] == 2
    assert selected["execution_script_file"] == "episode_2.json"
    info = probe(path)
    stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
    seconds = float(stream.get("duration", info["format"]["duration"]))
    original_seconds = p["video"]["actual_duration_seconds"]
    assert 0 < seconds - original_seconds <= 0.3
    p["narration_audio"] = {
        "artifact_path": selected["file"],
        "version": selected["version"],
        "selection": "current",
        "currency": "current",
        "basis": selected["artifact_audio_basis"],
        "content_digest": "sha256-v1:" + digest(path),
        "actual_duration_seconds": seconds,
        "start_microseconds": 0,
        "duration_microseconds": round(seconds * 1_000_000),
        "gain": 1.0,
    }
    p["source_variant"] = p["variant"]
    p["variant"] = "use_tts"
    p["provenance"] = "local_composite_from_verified_assets"
    p["export_tail_hold_seconds"] = math.ceil(seconds * 24) / 24 - original_seconds
    p["source_presentation_basis"] = p.pop("presentation_basis")
    p["source_subtitle_basis"] = p.pop("subtitle_basis")
    p["subtitle_artifact_path"] = None
    p["presentation_artifact_path"] = None
    p["persisted"] = False
    p["subtitles"] = [
        {
            **p["subtitles"][0],
            "start_microseconds": 0,
            "duration_microseconds": round(seconds * 1_000_000),
        }
    ]
    p["subtitles_webvtt"] = f"WEBVTT\n\n1\n00:00:00.000 --> 00:00:{seconds:06.3f}\n{scene['utterances'][0]['text']}\n"
    return p


def main():
    project_dir = (ARC / "deploy/projects" / PROJECT).resolve()
    script_path = project_dir / "scripts/episode_2.json"
    snapshot = script_path.read_bytes()
    scenes = json.loads(snapshot)["scenes"]
    assert len(scenes) == 19
    cfg = dotenv_values(ARC / ".env")
    presentations = []
    with httpx.Client(base_url="http://127.0.0.1:1241/api/v1", trust_env=False, timeout=60) as api:
        r = api.post(
            "/auth/token",
            data={
                "username": cfg.get("AUTH_USERNAME") or "admin",
                "password": cfg.get("AUTH_PASSWORD") or "",
            },
        )
        r.raise_for_status()
        api.headers["Authorization"] = "Bearer " + r.json()["access_token"]
        for scene in scenes:
            variant = "use_tts" if any(u["kind"] == "voiceover" for u in scene["utterances"]) else "post_production"
            r = api.get(f"/projects/{PROJECT}/presentations/videos/{scene['scene_id']}?variant={variant}")
            if r.status_code == 422 and variant == "use_tts" and scene["scene_id"] in {"E2S07", "E2S13"}:
                p = local_tail_hold_presentation(api, scene, project_dir)
            else:
                r.raise_for_status()
                p = r.json()
            assert p["provenance"] in {"verified", "local_composite_from_verified_assets"}
            assert p["currency"] == "current"
            assert [c["text"] for c in p["subtitles"]] == [u["text"] for u in scene["utterances"]]
            for key in ("video", "narration_audio"):
                if p.get(key):
                    path = (project_dir / p[key]["artifact_path"]).resolve()
                    assert path.is_relative_to(project_dir)
                    assert path.is_file()
                    assert digest(path) == p[key]["content_digest"].removeprefix("sha256-v1:")
            if p.get("narration_audio"):
                narration_seconds = p["narration_audio"]["actual_duration_seconds"]
                if (
                    narration_seconds
                    > p["video"]["actual_duration_seconds"] + p.get("export_tail_hold_seconds", 0) + 1e-6
                ):
                    raise RuntimeError(
                        f"{scene['scene_id']}: narration {narration_seconds:.3f}s exceeds video; do not truncate speech"
                    )
            presentations.append(p)
    assert script_path.read_bytes() == snapshot, "Episode changed during export preparation"
    basis = hashlib.sha256(
        json.dumps(
            {"renderer": "short-phrase-ass-1080-v1", "presentations": presentations},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:12]
    directory = ROOT / "outputs" / ("episode2-complete-" + basis)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "presentations.json").write_text(
        json.dumps(presentations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rendered = []
    total_frames = 0
    all_cues = []
    for p in presentations:
        sid = p["unit_id"]
        source = project_dir / p["video"]["artifact_path"]
        frames = math.ceil((p["video"]["actual_duration_seconds"] + p.get("export_tail_hold_seconds", 0)) * 24 - 1e-5)
        seconds = frames / 24
        cues = subtitle_phrases(p["subtitles"])
        subtitle = directory / (sid + ".ass")
        subtitle.write_text(ass_subtitles(cues), encoding="utf-8")
        part = directory / (sid + ".mp4")
        argv = ["ffmpeg", "-v", "error", "-n", "-i", str(source)]
        filters = [
            f"[0:v]fps=24,scale=1080:1920:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=0.5,trim=duration={seconds},ass={subtitle.name}[v]"
        ]
        tracks = []
        if p["video"]["audio_enabled"]:
            gain = p["video"]["gain"]
            filters.append(f"[0:a]volume={gain},apad,atrim=duration={seconds}[native]")
            tracks.append("[native]")
        if p.get("narration_audio"):
            argv += ["-i", str(project_dir / p["narration_audio"]["artifact_path"])]
            gain = p["narration_audio"]["gain"]
            filters.append(f"[1:a]volume={gain},apad,atrim=duration={seconds}[narrator]")
            tracks.append("[narrator]")
        if not tracks:
            raise RuntimeError(f"{sid}: no audio delivery track; refuse silent fallback")
        if len(tracks) == 2:
            filters.append(
                "".join(tracks) + "amix=inputs=2:normalize=0,alimiter=limit=0.95:level=false:latency=true[a]"
            )
        else:
            filters.append(tracks[0] + "anull[a]")
        argv += [
            "-filter_complex",
            ";".join(filters),
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
            part.name,
        ]
        if not part.exists():
            run(argv, cwd=directory)
        info = probe(part)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert int(video["nb_read_frames"]) == frames
        all_cues.extend(
            {
                **cue,
                "start_microseconds": cue["start_microseconds"] + round(total_frames / 24 * 1_000_000),
                "scene_id": sid,
            }
            for cue in cues
        )
        total_frames += frames
        rendered.append(part)
        print(
            json.dumps({"rendered": sid, "parts": len(rendered), "total": 19}),
            flush=True,
        )

    def srt_time(microseconds):
        millis = round(microseconds / 1000)
        seconds, millis = divmod(millis, 1000)
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"

    (directory / "episode_2.srt").write_text(
        "\n\n".join(
            f"{index}\n{srt_time(cue['start_microseconds'])} --> "
            f"{srt_time(cue['start_microseconds'] + cue['duration_microseconds'])}\n{cue['text']}"
            for index, cue in enumerate(all_cues, 1)
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = directory / "concat.txt"
    manifest.write_text("".join(f"file '{p.name}'\n" for p in rendered), encoding="utf-8")
    final = directory / "episode_2_1080x1920_subtitled.mp4"
    if not final.exists():
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                manifest.name,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                final.name,
            ],
            cwd=directory,
        )
    run(["ffmpeg", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"])
    info = probe(final)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
    assert int(video["nb_read_frames"]) == total_frames
    assert abs(float(video["duration"]) - total_frames / 24) < 0.15
    assert script_path.read_bytes() == snapshot, "Episode changed; export snapshot is historical"
    report = {
        "technical_passed": True,
        "scene_count": 19,
        "output": str(final),
        "sha256": digest(final),
        "probe": info,
        "subtitles": all_cues,
        "presentations": presentations,
        "export_tail_holds": {
            p["unit_id"]: p["export_tail_hold_seconds"] for p in presentations if p.get("export_tail_hold_seconds")
        },
        "limitations": [
            "Mixed native resolutions, Lanczos upscaled to 1080x1920.",
            "Script-derived subtitle timing, not ASR alignment.",
            "Native speech accuracy and lip sync require viewing.",
            "Native ambience and narration mixed with a peak limiter; original media retained.",
            "E2S07 and E2S13 use short tail holds in this local export only; the app's strict use_tts unit preview still rejects their unedited sources.",
        ],
    }
    (directory / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"complete": True, "output": str(final), "seconds": video["duration"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

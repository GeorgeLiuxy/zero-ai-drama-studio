"""Prepare a revision-bound episode plan locally; never submit or mutate a project."""

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
ARC = ROOT.parent.parent if ROOT.parent.name == "deploy" else ROOT.parent / "ArcReel"
sys.path.insert(0, str(ARC))
from h3_prompt import format_h3_prompt
from lib.prompt_utils import render_storyboard_video_prompt
from lib.script_batch_edit import ScriptBatchEditCommand, script_revision
from lib.speech_rate import estimate_spoken_seconds, project_speech_rate_override

PROJECT = "proj-8fbe5aa8"
TIERS = (5, 8, 10, 12)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    project_dir = ARC / "deploy/projects" / PROJECT
    project_bytes = (project_dir / "project.json").read_bytes()
    script_bytes = (project_dir / "scripts/episode_2.json").read_bytes()
    project = json.loads(project_bytes)
    script = json.loads(script_bytes)
    if project["aspect_ratio"] != "9:16" or len(script["scenes"]) != 19:
        raise RuntimeError("Episode changed; re-evaluate the preparation target")
    rows = []
    operations = []
    for scene in script["scenes"]:
        scene_id = scene["scene_id"]
        assets = scene.get("generated_assets") or {}
        verified_assets = {}
        for key in ("storyboard_image", "video_clip", "narration_audio"):
            relative = assets.get(key)
            if not relative:
                continue
            path = (project_dir / relative).resolve()
            if not path.is_relative_to(project_dir.resolve()) or not path.is_file():
                raise RuntimeError(f"{scene_id}: missing or invalid {key}")
            verified_assets[key] = {"path": relative, "sha256": sha(path.read_bytes())}
            if key == "storyboard_image":
                with Image.open(path) as image:
                    image.load()
                    if image.width * 16 != image.height * 9:
                        raise RuntimeError(f"{scene_id}: first frame is not 9:16")
                    verified_assets[key]["size"] = list(image.size)
        if "storyboard_image" not in verified_assets:
            raise RuntimeError(f"{scene_id}: missing first frame")
        speech = scene.get("utterances") or []
        kinds = {s["kind"] for s in speech}
        if len(kinds) > 1:
            raise RuntimeError(f"{scene_id}: mixed speech requires explicit planning")
        spoken = sum(
            estimate_spoken_seconds(
                s["text"],
                project.get("source_language"),
                project_speech_rate_override(project),
            )
            for s in speech
        )
        budget = spoken + (0.8 + 0.2 * (len(speech) - 1) if speech else 0)
        target = next((t for t in TIERS if t + 1e-9 >= budget), None)
        if target is None:
            raise RuntimeError(
                f"{scene_id}: speech exceeds the candidate duration tiers"
            )
        if "video_clip" in verified_assets:
            if scene_id not in {"E2S01", "E2S05"}:
                raise RuntimeError(
                    "A new video exists; reconcile it before changing the plan"
                )
            target = scene["duration_seconds"]
            if target < budget or target not in TIERS:
                raise RuntimeError(
                    f"{scene_id}: existing clip cannot fit the speech estimate"
                )
        prompt = render_storyboard_video_prompt(
            scene["video_prompt"],
            scene,
            content_mode="drama",
            voice_characters=project.get("characters") or {},
        )
        compiled = format_h3_prompt(prompt)
        if len(prompt) > 6000:
            raise RuntimeError(f"{scene_id}: prompt exceeds gateway input limit")
        if target != scene["duration_seconds"]:
            operations.append(
                {"op": "update", "id": scene_id, "fields": {"duration_seconds": target}}
            )
        rows.append(
            {
                "scene_id": scene_id,
                "current_duration_seconds": scene["duration_seconds"],
                "proposed_duration_seconds": target,
                "estimated_speech_seconds": round(spoken, 3),
                "speech_budget_seconds": round(budget, 3),
                "utterances": speech,
                "speech_kind": next(iter(kinds), "silent"),
                "video_action": "reuse_candidate"
                if "video_clip" in verified_assets
                else "generate",
                "narration_action": (
                    "reuse_candidate"
                    if "narration_audio" in verified_assets
                    else "generate_cosyvoice"
                    if "voiceover" in kinds
                    else "native_h3_dialogue"
                ),
                "assets": verified_assets,
                "canonical_prompt_sha256": sha(prompt.encode()),
                "compiled_prompt_sha256": sha(compiled.encode()),
            }
        )
    # Detect concurrent edits before recording the snapshot. A future apply must
    # also check revisions and asset hashes through the shared transactional editor.
    if (project_dir / "project.json").read_bytes() != project_bytes or (
        project_dir / "scripts/episode_2.json"
    ).read_bytes() != script_bytes:
        raise RuntimeError(
            "Project changed during preparation; retry the read-only scan"
        )
    command = ScriptBatchEditCommand.model_validate(
        {
            "episode": 2,
            "expected_script_file": "episode_2.json",
            "expected_revision": script_revision(script),
            "operations": operations,
        }
    )
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    output = ROOT / "reports" / ("episode2-preparation-" + stamp)
    output.mkdir(parents=True, exist_ok=False)
    (output / "episode_2.before.json").write_bytes(script_bytes)
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "local_preparation_only",
        "project": PROJECT,
        "episode": 2,
        "project_sha256": sha(project_bytes),
        "script_sha256": sha(script_bytes),
        "model": "custom-1/h3-i2v-int8",
        "native_target": [864, 1536],
        "candidate_durations": list(TIERS),
        "gpu_verified_durations_at_864": [5],
        "gpu_submissions": 0,
        "project_changes_applied": False,
        "estimated_nominal_total_seconds": sum(
            r["proposed_duration_seconds"] for r in rows
        ),
        "first_gpu_validation_scene": "E2S11",
        "remaining_video_count": sum(r["video_action"] == "generate" for r in rows),
        "remaining_narration_count": sum(
            r["narration_action"] == "generate_cosyvoice" for r in rows
        ),
        "duration_edit_command": command.model_dump(mode="json", exclude_none=True),
        "scenes": rows,
        "execution_order": [
            "After the user enables GPU, reconcile app/cloud queues and existing asset currency.",
            "Back up and deploy the tested gateway; then register durations without resetting other model settings.",
            "Recheck project/script revisions and asset hashes; apply duration edits transactionally and reconcile script plan.",
            "Submit E2S11 once with durable task intent; validate 12-second native HD, complete dialogue, audio and resource use.",
            "Only after validation, generate remaining missing video clips sequentially, reconciling uncertain jobs before retry.",
            "Generate independent CosyVoice narration; measure its real duration before composition, preserving all speech.",
            "Compose the full episode, subtitles and export; inspect boundaries and decode the complete result.",
        ],
        "limitations": [
            "Long-duration HD is CPU-contract-tested, not GPU-validated.",
            "Speech duration is an estimate, not a TTS measurement.",
            "Asset existence/hash does not prove visual, spoken-content or provenance correctness.",
            "E2S01 is an existing lower-resolution clip; reusing it makes source resolution mixed.",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "report": str(output / "manifest.json"),
                "scenes": len(rows),
                "duration_edits_prepared": len(operations),
                "estimated_seconds": manifest["estimated_nominal_total_seconds"],
                "remaining_videos": manifest["remaining_video_count"],
                "remaining_narrations": manifest["remaining_narration_count"],
                "gpu_submissions": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

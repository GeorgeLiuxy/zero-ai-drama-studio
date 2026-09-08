"""Composite completion and reviewed first-frame readiness are different facts."""

import json

import pytest
from PIL import Image

from lib.artifact_activation import register_current_resource_artifact
from lib.grid.models import GridGeneration
from lib.grid.review import GRID_REVIEW_VERSION
from lib.grid_manager import GridManager
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.version_manager import VersionManager
from server.services.grid_readiness import describe_grid_readiness


@pytest.fixture
def readiness_project(tmp_path):
    for name in ("grids", "scripts", "storyboards"):
        (tmp_path / name).mkdir()
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "aspect_ratio": "9:16",
        "style": "realistic",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
    }
    (tmp_path / "project.json").write_text(json.dumps(project), encoding="utf-8")
    script = {
        "episode": 1,
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": f"S{i}",
                "segment_break": True,
                "image_prompt": f"Scene {i}",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "generated_assets": {"storyboard_image": f"storyboards/scene_S{i}.png"},
            }
            for i in range(1, 13)
        ],
    }
    (tmp_path / "scripts/episode_1.json").write_text(json.dumps(script), encoding="utf-8")
    return tmp_path


def _grid(project, scene_ids, *, rows=2, cols=2, size=(720, 1280)):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=scene_ids,
        rows=rows,
        cols=cols,
        grid_size=f"grid_{rows * cols}",
        provider="codex",
        model="gpt-image-2",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    Image.new("RGB", size, (50, 80, 110)).save(project / grid.grid_image_path)
    GridManager(project).save(grid)
    return grid


def _frame(project, scene_id, *, reviewed=True, source="grid_split"):
    path = project / f"storyboards/scene_{scene_id}.png"
    Image.new("RGB", (576, 1024), (50, 80, 110)).save(path)
    metadata = (
        {
            "grid_review_version": GRID_REVIEW_VERSION,
            "grid_review_token": "confirmed-preview",
            "first_frame_size": [576, 1024],
        }
        if reviewed
        else {}
    )
    VersionManager(project).add_version("storyboards", scene_id, "", source_file=path, source=source, **metadata)
    assert register_current_resource_artifact(project, resource_type="storyboards", resource_id=scene_id)


def test_twelve_scenes_report_nine_ready_and_invalid_second_composite(readiness_project):
    p = readiness_project
    first = _grid(p, [f"S{i}" for i in range(1, 10)], rows=3, cols=3)
    second = _grid(p, ["S10", "S11", "S12"], size=(1024, 1536))
    for scene_id in first.scene_ids:
        _frame(p, scene_id)

    results = describe_grid_readiness(p, [first, second])

    assert results[0]["readiness"] == {
        "image_exists": True,
        "geometry_valid": True,
        "issues": [],
        "ready_scene_ids": first.scene_ids,
    }
    assert results[1]["status"] == "completed"
    assert results[1]["readiness"] == {
        "image_exists": True,
        "geometry_valid": False,
        "issues": ["grid_aspect_ratio_mismatch"],
        "ready_scene_ids": [],
    }


def test_old_split_timestamp_and_files_do_not_certify_review(readiness_project):
    p = readiness_project
    grid = _grid(p, ["S1", "S2"])
    grid.split_at = grid.created_at
    _frame(p, "S1", reviewed=False)
    Image.new("RGB", (576, 1024)).save(p / "storyboards/scene_S2.png")

    state = describe_grid_readiness(p, [grid])[0]["readiness"]

    assert state["geometry_valid"] is True
    assert state["ready_scene_ids"] == []


def test_replacing_composite_refreshes_geometry_without_changing_first_frames(readiness_project):
    p = readiness_project
    grid = _grid(p, ["S1", "S2", "S3"], size=(1024, 1536))
    _frame(p, "S1")
    assert describe_grid_readiness(p, [grid])[0]["readiness"]["geometry_valid"] is False

    Image.new("RGB", (720, 1280), (50, 80, 110)).save(p / grid.grid_image_path)
    state = describe_grid_readiness(p, [grid])[0]["readiness"]

    assert state["geometry_valid"] is True
    assert state["ready_scene_ids"] == ["S1"]


def test_composite_replacement_preserves_independently_selected_first_frame(readiness_project):
    p = readiness_project
    grid = _grid(p, ["S1", "S2"], size=(1024, 1536))
    _frame(p, "S1", reviewed=False, source="manual_upload")
    _frame(p, "S2")
    grid.status = "pending"
    grid.split_at = None

    state = describe_grid_readiness(p, [grid])[0]["readiness"]

    assert state["geometry_valid"] is False
    assert state["ready_scene_ids"] == ["S1", "S2"]


@pytest.mark.parametrize("change", ["delete", "corrupt", "ratio", "unbind", "unregister", "project_ratio"])
def test_first_frame_readiness_tracks_current_files_and_bindings(readiness_project, change):
    p = readiness_project
    grid = _grid(p, ["S1"])
    _frame(p, "S1")
    assert describe_grid_readiness(p, [grid])[0]["readiness"]["ready_scene_ids"] == ["S1"]
    path = p / "storyboards/scene_S1.png"
    if change == "delete":
        path.unlink()
    elif change == "corrupt":
        path.write_bytes(b"invalid png")
    elif change == "ratio":
        Image.new("RGB", (512, 768)).save(path)
    elif change == "unbind":
        script_path = p / "scripts/episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["segments"][0]["generated_assets"] = {}
        script_path.write_text(json.dumps(script), encoding="utf-8")
    elif change == "unregister":
        from lib.artifact_activation import register_artifact_entries_atomically
        from lib.artifact_manifest import ArtifactKey

        register_artifact_entries_atomically(p, {ArtifactKey.episode_storyboard(1, "S1"): None})
    elif change == "project_ratio":
        project_path = p / "project.json"
        project = json.loads(project_path.read_text(encoding="utf-8"))
        project["aspect_ratio"] = "16:9"
        project_path.write_text(json.dumps(project), encoding="utf-8")

    assert describe_grid_readiness(p, [grid])[0]["readiness"]["ready_scene_ids"] == []


@pytest.mark.parametrize("change", ["missing", "corrupt", "unsafe_path", "mapping"])
def test_completed_composite_still_requires_real_image_checks(readiness_project, change):
    p = readiness_project
    grid = _grid(p, ["S1"])
    path = p / grid.grid_image_path
    if change == "missing":
        path.unlink()
    elif change == "corrupt":
        path.write_bytes(b"invalid png")
    elif change == "unsafe_path":
        grid.grid_image_path = "../outside.png"
    elif change == "mapping":
        grid.frame_chain[0].next_scene_id = "S2"

    state = describe_grid_readiness(p, [grid])[0]["readiness"]

    assert state["geometry_valid"] is not True
    assert state["issues"]
    assert state["ready_scene_ids"] == []

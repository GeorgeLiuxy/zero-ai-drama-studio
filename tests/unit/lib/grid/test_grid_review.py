"""Review evidence binds exact crops to the source image and video input."""

import base64
import io

import pytest
from PIL import Image

from lib.grid.models import GridGeneration
from lib.grid.review import (
    GRID_REVIEW_VERSION,
    build_grid_split_preview,
    first_frame_size,
    grid_review_token,
    require_reviewed_grid_first_frame,
)
from lib.storyboard_sequence import StoryboardImageUnavailable
from lib.version_manager import VersionManager


@pytest.fixture
def review_grid():
    return GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["S1", "S2", "S3"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="codex",
        model="gpt-image-2",
        video_aspect_ratio="9:16",
    )


def _source(color="red", size=(720, 1280)):
    image = Image.new("RGB", size, color)
    image.paste("blue", (size[0] // 2, 0, size[0], size[1] // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(("ratio", "expected"), [("9:16", (576, 1024)), ("16:9", (1024, 576)), ("4:3", (1024, 768))])
def test_first_frame_spec_preserves_project_ratio(ratio, expected):
    assert first_frame_size(ratio) == expected


def test_preview_shows_real_crops_and_placeholder_mapping(review_grid):
    preview = build_grid_split_preview(_source(), review_grid, "9:16")
    assert preview["valid"] is True
    assert preview["issues"] == []
    assert (preview["output_width"], preview["output_height"]) == (576, 1024)
    assert [cell["scene_id"] for cell in preview["cells"]] == ["S1", "S2", "S3", None]
    assert preview["cells"][-1]["placeholder"] is True
    first = Image.open(io.BytesIO(base64.b64decode(preview["cells"][0]["image"].split(",")[1])))
    second = Image.open(io.BytesIO(base64.b64decode(preview["cells"][1]["image"].split(",")[1])))
    assert first.size == second.size == (288, 512)
    assert first.getpixel((100, 100)) == (255, 0, 0)
    assert second.getpixel((100, 100)) == (0, 0, 255)


def test_review_expires_on_source_or_scene_mapping_change(review_grid):
    original = grid_review_token(_source(), review_grid, "9:16")
    assert grid_review_token(_source("green"), review_grid, "9:16") != original
    assert grid_review_token(_source(), review_grid, "16:9") != original
    review_grid.frame_chain[0].next_scene_id = "S4"
    assert grid_review_token(_source(), review_grid, "9:16") != original
    assert "scene_mapping_mismatch" in build_grid_split_preview(_source(), review_grid, "9:16")["issues"]


def test_changed_project_ratio_cannot_be_confirmed(review_grid):
    preview = build_grid_split_preview(_source(), review_grid, "16:9")
    assert preview["valid"] is False
    assert "project_aspect_changed" in preview["issues"]


def test_malformed_canvas_remains_visible_but_cannot_be_confirmed(review_grid):
    preview = build_grid_split_preview(_source(size=(720, 720)), review_grid, "9:16")
    assert preview["valid"] is False
    assert "grid_aspect_ratio_mismatch" in preview["issues"]
    assert len(preview["cells"]) == 4


def test_grid_video_input_requires_review_and_current_project_dimensions(tmp_path):
    source = tmp_path / "frame.png"
    Image.new("RGB", (576, 1024), "red").save(source)
    versions = VersionManager(tmp_path)
    versions.add_version("storyboards", "S1", "", source_file=source, source="grid_split")
    with pytest.raises(StoryboardImageUnavailable, match="预览确认"):
        require_reviewed_grid_first_frame(tmp_path, "S1", source, "9:16")
    versions.add_version(
        "storyboards",
        "S1",
        "",
        source_file=source,
        source="grid_split",
        grid_review_version=GRID_REVIEW_VERSION,
        grid_review_token="reviewed-content",
        first_frame_size=[576, 1024],
    )
    require_reviewed_grid_first_frame(tmp_path, "S1", source, "9:16")
    with pytest.raises(StoryboardImageUnavailable):
        require_reviewed_grid_first_frame(tmp_path, "S1", source, "16:9")
    Image.new("RGB", (400, 400)).save(source)
    with pytest.raises(StoryboardImageUnavailable, match="尺寸"):
        require_reviewed_grid_first_frame(tmp_path, "S1", source, "9:16")


def test_independent_replacement_does_not_inherit_old_grid_review_requirement(tmp_path):
    source = tmp_path / "frame.png"
    Image.new("RGB", (576, 1024)).save(source)
    versions = VersionManager(tmp_path)
    versions.add_version("storyboards", "S1", "", source_file=source, source="grid_split")
    versions.add_version("storyboards", "S1", "", source_file=source, source="manual_upload")
    require_reviewed_grid_first_frame(tmp_path, "S1", source, "9:16")
    assert versions.get_current_version("storyboards", "S1") == 2

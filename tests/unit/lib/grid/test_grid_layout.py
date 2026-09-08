"""Tests for grid layout calculator."""

import pytest

from lib.grid.layout import (
    GRID_FALLBACK_RESOLUTION,
    calculate_grid_layout,
    grid_aspect_ratio_for,
    large_grid_allowed,
    max_cell_count,
    plan_grid_chunks,
    video_aspect_ratio_of,
)
from lib.grid.models import GridGeneration, build_frame_chain


class TestCalculateGridLayout:
    @pytest.mark.parametrize(
        ("count", "aspect_ratio", "large", "rows", "cols", "canvas", "placeholders"),
        [
            (1, "9:16", False, 2, 2, "9:16", 3),
            (2, "9:16", False, 2, 2, "9:16", 2),
            (3, "9:16", False, 2, 2, "9:16", 1),
            (4, "9:16", False, 2, 2, "9:16", 0),
            (1, "16:9", True, 2, 2, "16:9", 3),
            (2, "1:1", False, 2, 2, "1:1", 2),
            (3, "8:6", True, 2, 2, "4:3", 1),
            (4, "3:1", False, 2, 2, "3:1", 0),
            (5, "9:16", False, 1, 5, "45:16", 0),
            (6, "9:16", False, 2, 3, "27:32", 0),
            (7, "9:16", False, 2, 4, "9:8", 1),
            (9, "9:16", False, 3, 3, "9:16", 0),
            (12, "9:16", False, 3, 3, "9:16", 0),
            (12, "9:16", True, 3, 4, "3:4", 0),
            (12, "16:9", True, 4, 3, "4:3", 0),
            (10, "9:16", True, 2, 5, "45:32", 0),
            (16, "9:16", True, 4, 4, "9:16", 0),
            (17, "9:16", True, 4, 5, "45:64", 3),
            (25, "9:16", True, 5, 5, "9:16", 0),
            (40, "9:16", True, 5, 5, "9:16", 0),
            (4, "4:3", False, 2, 2, "4:3", 0),
            (6, "3:4", False, 2, 3, "9:8", 0),
            (6, "1:1", False, 2, 3, "3:2", 0),
            (4, "8:6", False, 2, 2, "4:3", 0),
        ],
    )
    def test_count_and_video_ratio_choose_layout(self, count, aspect_ratio, large, rows, cols, canvas, placeholders):
        layout = calculate_grid_layout(count, aspect_ratio, allow_large_grid=large)

        assert layout is not None
        assert (layout.rows, layout.cols) == (rows, cols)
        assert layout.grid_aspect_ratio == canvas
        assert layout.grid_size == f"grid_{rows * cols}"
        assert layout.cell_count == rows * cols
        assert layout.placeholder_count == placeholders

    @pytest.mark.parametrize("count", [0, -1])
    def test_no_scenes_returns_none(self, count):
        assert calculate_grid_layout(count, "16:9") is None

    @pytest.mark.parametrize("aspect_ratio", ["", "portrait", "0:16", "9:-16", "1.5:1", "1:2:3"])
    def test_invalid_video_ratio_is_not_replaced_by_orientation(self, aspect_ratio):
        with pytest.raises(ValueError, match="比例"):
            calculate_grid_layout(4, aspect_ratio)

    @pytest.mark.parametrize(("count", "aspect_ratio"), [(1, "1:4"), (3, "4:1"), (9, "100:1")])
    def test_impossible_canvas_fails_without_changing_frame_ratio(self, count, aspect_ratio):
        with pytest.raises(ValueError, match="逐张生成"):
            calculate_grid_layout(count, aspect_ratio)

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 9, 10, 12, 17, 25])
    @pytest.mark.parametrize("portrait", ["9:16", "3:4", "2:3"])
    def test_reciprocal_video_ratios_transpose_layout(self, count, portrait):
        width, height = portrait.split(":")
        vertical = calculate_grid_layout(count, portrait, allow_large_grid=True)
        horizontal = calculate_grid_layout(count, f"{height}:{width}", allow_large_grid=True)

        assert vertical is not None
        assert horizontal is not None
        assert (horizontal.rows, horizontal.cols) == (vertical.cols, vertical.rows)
        vw, vh = vertical.grid_aspect_ratio.split(":")
        assert horizontal.grid_aspect_ratio == f"{vh}:{vw}"


class TestPlanGridChunks:
    def test_empty_group_returns_no_plans(self):
        assert plan_grid_chunks([], "16:9") == []

    @pytest.mark.parametrize(
        ("count", "large", "expected"),
        [
            (7, False, [(7, 2, 4, 1)]),
            (12, False, [(9, 3, 3, 0), (3, 2, 2, 1)]),
            (12, True, [(12, 3, 4, 0)]),
            (19, False, [(9, 3, 3, 0), (9, 3, 3, 0), (1, 2, 2, 3)]),
            (30, True, [(25, 5, 5, 0), (5, 1, 5, 0)]),
        ],
    )
    def test_chunks_preserve_scene_order_without_placeholder_scenes(self, count, large, expected):
        scenes = [f"S{i:02d}" for i in range(count)]
        plans = plan_grid_chunks(scenes, "9:16", allow_large_grid=large)

        assert [(len(chunk), layout.rows, layout.cols, layout.placeholder_count) for chunk, layout in plans] == expected
        assert [scene for chunk, _ in plans for scene in chunk] == scenes
        assert all(len(chunk) + layout.placeholder_count == layout.cell_count for chunk, layout in plans)


class TestLargeGridGate:
    @pytest.mark.parametrize("resolution", ["4K", "4k", " 4K "])
    def test_4k_allows_large_grid(self, resolution: str):
        assert large_grid_allowed(resolution) is True

    @pytest.mark.parametrize("resolution", ["2K", "1K", "1080p", "", None])
    def test_below_4k_and_unset_block_large_grid(self, resolution: str | None):
        assert large_grid_allowed(resolution) is False

    def test_fallback_resolution_is_not_4k(self):
        # 未配置分辨率时保底档即渲染实际下发的档位，门控须按它判定
        assert large_grid_allowed(GRID_FALLBACK_RESOLUTION) is False

    def test_max_cell_count(self):
        assert max_cell_count(allow_large_grid=True) == 25
        assert max_cell_count(allow_large_grid=False) == 9


class TestGridAspectRatioFor:
    @pytest.mark.parametrize(
        ("rows", "cols", "aspect", "expected"),
        [
            (4, 3, "9:16", "27:64"),
            (3, 4, "9:16", "3:4"),
            (4, 3, "16:9", "4:3"),
            (3, 2, "16:9", "32:27"),
            (2, 3, "9:16", "27:32"),
            (2, 4, "16:9", "32:9"),
            (3, 3, "4:3", "4:3"),
            (3, 3, "8:6", "4:3"),
        ],
    )
    def test_canvas_uses_exact_cell_geometry(self, rows, cols, aspect, expected):
        assert grid_aspect_ratio_for(rows, cols, aspect) == expected

    @pytest.mark.parametrize(("rows", "cols"), [(0, 3), (3, -1)])
    def test_invalid_grid_dimensions_are_rejected(self, rows, cols):
        with pytest.raises(ValueError, match="正整数"):
            grid_aspect_ratio_for(rows, cols, "16:9")


class TestGridLayoutPixelDimensions:
    @pytest.mark.parametrize("aspect", ["16:9", "9:16", "4:3", "1:1", "37:29"])
    @pytest.mark.parametrize("count", [1, 3, 6, 7, 9, 12, 17, 25])
    def test_canvas_and_each_cell_have_exact_integer_geometry(self, count, aspect):
        layout = calculate_grid_layout(count, aspect, allow_large_grid=True)
        assert layout is not None
        width, height = layout.pixel_dimensions()
        target_width, target_height = (int(part) for part in aspect.split(":"))

        assert width > 0
        assert height > 0
        assert width % layout.cols == 0
        assert height % layout.rows == 0
        assert (width // layout.cols) * target_height == (height // layout.rows) * target_width


class TestBuildFrameChain:
    def test_4_scenes_grid_4(self):
        chain = build_frame_chain(["E1S01", "E1S02", "E1S03", "E1S04"], rows=2, cols=2)
        assert len(chain) == 4
        assert chain[0].frame_type == "first"
        assert chain[0].next_scene_id == "E1S01"
        assert chain[1].frame_type == "transition"
        assert chain[1].prev_scene_id == "E1S01"
        assert chain[1].next_scene_id == "E1S02"
        assert chain[3].frame_type == "transition"
        assert chain[3].prev_scene_id == "E1S03"
        assert chain[3].next_scene_id == "E1S04"

    def test_remaining_cells_become_placeholders(self):
        chain = build_frame_chain(["S1", "S2", "S3", "S4", "S5"], rows=3, cols=3)
        assert len(chain) == 9
        assert [c.frame_type for c in chain[5:]] == ["placeholder"] * 4

    def test_row_col_assignment(self):
        chain = build_frame_chain(["A", "B", "C", "D"], rows=2, cols=2)
        assert (chain[0].row, chain[0].col) == (0, 0)
        assert (chain[1].row, chain[1].col) == (0, 1)
        assert (chain[2].row, chain[2].col) == (1, 0)
        assert (chain[3].row, chain[3].col) == (1, 1)


class TestGridGeneration:
    def test_create(self):
        grid = GridGeneration.create(
            episode=1,
            script_file="ep1.json",
            scene_ids=["E1S01", "E1S02", "E1S03", "E1S04"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="test",
            model="test-m",
            video_aspect_ratio="9:16",
        )
        assert grid.status == "pending"
        assert grid.cell_count == 4
        assert len(grid.frame_chain) == 4
        assert grid.id.startswith("grid_")


class TestVideoAspectRatioOf:
    def test_reads_project_value(self):
        assert video_aspect_ratio_of({"aspect_ratio": "16:9"}) == "16:9"

    def test_missing_key_falls_back(self):
        assert video_aspect_ratio_of({}) == "9:16"

    def test_explicit_null_falls_back(self):
        # project.json 允许把 aspect_ratio 显式写为 null，dict.get 的默认值对此无效；
        # None 漏下去会流进画布几何、prompt 与记录上冻结的比例
        assert video_aspect_ratio_of({"aspect_ratio": None}) == "9:16"

    @pytest.mark.parametrize(("mode", "expected"), [("drama", "16:9"), ("narration", "9:16"), ("ad", "9:16")])
    def test_missing_ratio_matches_actual_video_generation_default(self, mode, expected):
        assert video_aspect_ratio_of({"content_mode": mode, "aspect_ratio": None}) == expected

    def test_legacy_resource_mapping_uses_video_ratio(self):
        project = {"aspect_ratio": {"storyboards": "1:1", "videos": "8:6"}, "content_mode": "drama"}
        assert video_aspect_ratio_of(project) == "4:3"

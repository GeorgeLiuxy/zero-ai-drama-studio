"""Tests for lib/grid/splitter.py."""

import pytest
from PIL import Image, ImageDraw

from lib.grid.splitter import center_crop_to_ratio, inspect_grid_geometry, is_placeholder_cell, split_grid_image


class TestCenterCropToRatio:
    def test_no_crop_needed(self):
        img = Image.new("RGB", (160, 90))
        result = center_crop_to_ratio(img, "16:9")
        # 比例已匹配时原图直接返回，不重新裁出一张同尺寸副本
        assert result is img

    @pytest.mark.parametrize(
        ("source_size", "ratio", "expected_size"),
        [
            ((200, 100), "16:9", (176, 99)),
            ((100, 200), "9:16", (99, 176)),
            ((301, 535), "9:16", (297, 528)),
            ((401, 301), "4:3", (400, 300)),
            ((200, 100), "32:18", (176, 99)),
        ],
    )
    def test_crop_has_exact_integer_ratio(self, source_size, ratio, expected_size):
        result = center_crop_to_ratio(Image.new("RGB", source_size), ratio)
        assert result.size == expected_size

    @pytest.mark.parametrize("ratio", ["0:16", "9:-1", "9:0", "16", "bad:ratio"])
    def test_invalid_ratio_is_rejected(self, ratio):
        with pytest.raises((ValueError, ZeroDivisionError)):
            center_crop_to_ratio(Image.new("RGB", (160, 90)), ratio)


class TestSplitGridImage:
    def test_split_2x2(self):
        grid = Image.new("RGB", (320, 180))
        colors = [(180, 50, 40), (50, 180, 40), (40, 50, 180), (180, 50, 180)]
        for color, box in zip(
            colors, [(0, 0, 160, 90), (160, 0, 320, 90), (0, 90, 160, 180), (160, 90, 320, 180)], strict=True
        ):
            grid.paste(color, box)
        cells = split_grid_image(grid, 2, 2, "16:9")
        assert len(cells) == 4
        assert [cell.size for cell in cells] == [(160, 90)] * 4
        assert [cell.getpixel((0, 0)) for cell in cells] == colors
        assert [cell.getpixel((159, 89)) for cell in cells] == colors

    def test_split_with_margin(self):
        grid = Image.new("RGB", (400, 400), color=(128, 128, 128))
        cells = split_grid_image(grid, 2, 2, "16:9", edge_margin=0.02)
        for cell in cells:
            assert cell.size[0] < 200

    @pytest.mark.parametrize(("ratio", "size"), [("9:16", (576, 1024)), ("16:9", (1024, 576)), ("4:3", (800, 600))])
    def test_normalized_output_is_common_across_remainder_cells(self, ratio, size):
        grid = Image.new("RGB", (943, 1673), color=(100, 150, 200))
        cells = split_grid_image(grid, 3, 3, ratio, output_size=size)
        assert [cell.size for cell in cells] == [size] * 9
        assert {cell.getpixel((size[0] // 2, size[1] // 2)) for cell in cells} == {(100, 150, 200)}

    def test_default_output_is_uniform_without_resizing(self):
        cells = split_grid_image(Image.new("RGB", (323, 183)), 2, 2, "16:9")
        assert [cell.size for cell in cells] == [(160, 90)] * 4

    def test_output_normalization_preserves_the_previewed_crop(self):
        grid = Image.new("RGB", (95, 57))
        grid.putdata([(x * 2, y * 4, 0) for y in range(57) for x in range(95)])
        previewed = split_grid_image(grid, 2, 2, "16:9")
        published = split_grid_image(grid, 2, 2, "16:9", output_size=(512, 288))
        assert [cell.tobytes() for cell in published] == [
            cell.resize((512, 288), Image.Resampling.LANCZOS).tobytes() for cell in previewed
        ]

    @pytest.mark.parametrize("size", [(301, 535), (0, 16), (9, -16)])
    def test_output_size_must_have_exact_positive_ratio(self, size):
        with pytest.raises(ValueError, match="output_size"):
            split_grid_image(Image.new("RGB", (900, 1600)), 2, 2, "9:16", output_size=size)

    @pytest.mark.parametrize(("rows", "cols", "margin"), [(0, 2, 0), (2, -1, 0), (2, 2, 0.5), (2, 2, -0.1)])
    def test_invalid_grid_or_margin_is_rejected(self, rows, cols, margin):
        with pytest.raises(ValueError, match=r"grid dimensions|edge_margin"):
            split_grid_image(Image.new("RGB", (320, 180)), rows, cols, "16:9", margin)


class TestInspectGridGeometry:
    def test_remainder_boundaries_cover_the_entire_canvas(self):
        report = inspect_grid_geometry(Image.new("RGB", (323, 183), (100, 150, 200)), 2, 2, "16:9")
        assert report["valid"]
        assert report["issues"] == []
        assert report["cell_boxes"] == [[0, 0, 161, 91], [161, 0, 323, 91], [0, 91, 161, 183], [161, 91, 323, 183]]

    @pytest.mark.parametrize(
        ("size", "rows", "cols", "ratio"),
        [((1200, 1600), 3, 4, "9:16"), ((1600, 1200), 4, 3, "16:9"), ((800, 600), 2, 2, "4:3")],
    )
    def test_canvas_matches_the_per_frame_ratio(self, size, rows, cols, ratio):
        report = inspect_grid_geometry(Image.new("RGB", size, (80, 100, 140)), rows, cols, ratio)
        assert report["valid"]
        assert len(report["cell_boxes"]) == rows * cols
        assert report["separator_positions"] == {"horizontal": [], "vertical": []}

    def test_portrait_canvas_does_not_fit_rectangular_portrait_cells(self):
        report = inspect_grid_geometry(Image.new("RGB", (900, 1600)), 3, 4, "9:16")
        assert not report["valid"]
        assert report["issues"] == ["grid_aspect_ratio_mismatch"]

    @pytest.mark.parametrize(
        ("rows", "cols", "ratio", "issue"),
        [
            (0, 2, "9:16", "invalid_grid_dimensions"),
            (2, 2000, "9:16", "invalid_grid_dimensions"),
            (2, 2, "0:16", "invalid_aspect_ratio"),
        ],
    )
    def test_invalid_geometry_returns_a_reviewable_report(self, rows, cols, ratio, issue):
        report = inspect_grid_geometry(Image.new("RGB", (900, 1600)), rows, cols, ratio)
        assert not report["valid"]
        assert report["issues"] == [issue]

    def test_off_grid_white_lines_detect_unequal_rows(self):
        grid = Image.new("RGB", (941, 1672), (45, 30, 20))
        draw = ImageDraw.Draw(grid)
        draw.line((0, 493, 940, 493), fill=(234, 227, 224), width=1)
        draw.line((0, 971, 940, 971), fill=(212, 203, 197), width=1)
        report = inspect_grid_geometry(grid, 3, 3, "9:16")
        assert not report["valid"]
        assert report["issues"] == ["unexpected_horizontal_separator"]
        assert report["separator_positions"]["horizontal"] == [493, 971]

    @pytest.mark.parametrize(
        ("axis", "color", "background", "position", "expected_issue"),
        [
            ("horizontal", (255, 255, 255), (80, 100, 120), 160, "visible_horizontal_separator"),
            ("vertical", (0, 0, 0), (140, 160, 180), 90, "visible_vertical_separator"),
            ("vertical", (255, 255, 255), (80, 100, 120), 120, "unexpected_vertical_separator"),
        ],
    )
    def test_thin_separator_strips_require_a_new_gutter_free_image(
        self, axis, color, background, position, expected_issue
    ):
        grid = Image.new("RGB", (180, 320), background)
        line = (0, position, 179, position) if axis == "horizontal" else (position, 0, position, 319)
        ImageDraw.Draw(grid).line(line, fill=color, width=1)
        report = inspect_grid_geometry(grid, 2, 2, "9:16")
        assert not report["valid"]
        assert report["issues"] == [expected_issue]
        assert report["separator_positions"][axis] == [position]

    def test_black_scene_and_partial_bright_object_are_not_grid_separators(self):
        grid = Image.new("RGB", (180, 320), (0, 0, 0))
        draw = ImageDraw.Draw(grid)
        draw.rectangle((0, 80, 179, 150), fill=(100, 120, 140))
        draw.line((30, 200, 100, 200), fill=(255, 255, 255), width=1)
        report = inspect_grid_geometry(grid, 2, 2, "9:16")
        assert report["valid"]
        assert report["separator_positions"] == {"horizontal": [], "vertical": []}


class TestPlaceholderDetection:
    def test_black_is_placeholder(self):
        assert is_placeholder_cell(Image.new("RGB", (100, 100), (10, 10, 10)))

    def test_gray_is_placeholder(self):
        assert is_placeholder_cell(Image.new("RGB", (100, 100), (128, 128, 128)))

    def test_colorful_is_not(self):
        assert not is_placeholder_cell(Image.new("RGB", (100, 100), (200, 100, 50)))

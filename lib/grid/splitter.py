"""Grid image splitter utilities."""

from __future__ import annotations

from fractions import Fraction
from typing import TypedDict

import numpy as np
from PIL import Image


class GridGeometryReport(TypedDict):
    """Measurable canvas and separator checks, without semantic panel recognition."""

    valid: bool
    issues: list[str]
    width: int
    height: int
    cell_width: int
    cell_height: int
    cell_boxes: list[list[int]]
    separator_positions: dict[str, list[int]]


def _ratio_units(target_ratio: str) -> tuple[int, int]:
    width, height = (Fraction(part.strip()) for part in target_ratio.split(":"))
    if width <= 0 or height <= 0:
        raise ValueError("aspect ratio must contain positive numbers")
    ratio = width / height
    return ratio.numerator, ratio.denominator


def _cell_boxes(width: int, height: int, rows: int, cols: int) -> list[list[int]]:
    if rows <= 0 or cols <= 0 or width < cols or height < rows:
        raise ValueError("grid dimensions must give every cell a positive area")
    return [
        [col * width // cols, row * height // rows, (col + 1) * width // cols, (row + 1) * height // rows]
        for row in range(rows)
        for col in range(cols)
    ]


def _separator_strips(image: Image.Image, axis: int) -> list[tuple[int, int]]:
    """Find thin neutral strips that contrast with both adjacent image regions.

    Only near-full-span strips count. Broad dark backgrounds, picture edges and
    ordinary changes between colored panels do not establish a separator.
    """
    pixels = np.asarray(image.convert("RGB"))
    brightness = pixels.mean(axis=2, dtype=np.float32)
    neutral = np.ptp(pixels, axis=2) <= 40
    white = (pixels.min(axis=2) >= 180) & neutral
    black = (pixels.max(axis=2) <= 35) & neutral
    span_axis = 1 - axis
    line_count = pixels.shape[axis]
    if pixels.shape[span_axis] < 16:
        return []
    candidates = (white.mean(axis=span_axis) >= 0.9) | (black.mean(axis=span_axis) >= 0.95)
    max_thickness = max(2, min(12, line_count // 200))
    profile = brightness if axis == 0 else brightness.T
    starts = np.flatnonzero(np.diff(np.r_[False, candidates, False].astype(np.int8)) == 1)
    ends = np.flatnonzero(np.diff(np.r_[False, candidates, False].astype(np.int8)) == -1)
    strips: list[tuple[int, int]] = []
    for start_raw, end_raw in zip(starts, ends, strict=True):
        start, end = int(start_raw), int(end_raw)
        if end - start > max_thickness or start < 3 or end + 2 >= line_count:
            continue
        stripe = profile[start:end].mean(axis=0)
        before = profile[start - 3 : start - 1].mean(axis=0)
        after = profile[end + 1 : end + 3].mean(axis=0)
        bright_contrast = ((stripe - before >= 40) & (stripe - after >= 40)).mean()
        dark_contrast = ((before - stripe >= 40) & (after - stripe >= 40)).mean()
        if max(float(bright_contrast), float(dark_contrast)) >= 0.9:
            strips.append((start, end))
    return strips


def inspect_grid_geometry(image: Image.Image, rows: int, cols: int, video_aspect_ratio: str) -> GridGeometryReport:
    """Inspect a composite against its frozen equal-cell, gutter-free layout.

    Passing these checks cannot prove the number or contents of visual panels;
    a crop preview still needs visual review before assignment to storyboards.
    """
    width, height = image.size
    report: GridGeometryReport = {
        "valid": False,
        "issues": [],
        "width": width,
        "height": height,
        "cell_width": 0,
        "cell_height": 0,
        "cell_boxes": [],
        "separator_positions": {"horizontal": [], "vertical": []},
    }
    try:
        report["cell_boxes"] = _cell_boxes(width, height, rows, cols)
    except ValueError:
        report["issues"].append("invalid_grid_dimensions")
        return report
    report["cell_width"] = width // cols
    report["cell_height"] = height // rows
    try:
        ratio_width, ratio_height = _ratio_units(video_aspect_ratio)
    except (ValueError, ZeroDivisionError):
        report["issues"].append("invalid_aspect_ratio")
        return report
    if report["cell_width"] < ratio_width or report["cell_height"] < ratio_height:
        report["issues"].append("invalid_cell_dimensions")
    expected_ratio = cols * ratio_width / (rows * ratio_height)
    relative_error = abs(width / height / expected_ratio - 1)
    if relative_error > max(0.01, 1 / width, 1 / height):
        report["issues"].append("grid_aspect_ratio_mismatch")
    for axis, name, count, length in ((0, "horizontal", rows, height), (1, "vertical", cols, width)):
        strips = _separator_strips(image, axis)
        positions = [(start + end - 1) // 2 for start, end in strips]
        report["separator_positions"][name] = positions
        boundaries = [index * length // count for index in range(1, count)]
        if any(not any(abs(position - boundary) <= 2 for boundary in boundaries) for position in positions):
            report["issues"].append(f"unexpected_{name}_separator")
        elif positions:
            report["issues"].append(f"visible_{name}_separator")
    report["valid"] = not report["issues"]
    return report


def center_crop_to_ratio(img: Image.Image, target_ratio: str) -> Image.Image:
    """Crop image from center to match the target aspect ratio.

    Args:
        img: Source PIL image.
        target_ratio: Aspect ratio string like "16:9" or "9:16".

    Returns:
        Cropped image with the desired aspect ratio.
    """
    w_ratio, h_ratio = _ratio_units(target_ratio)
    w, h = img.size
    scale = min(w // w_ratio, h // h_ratio)
    if scale < 1:
        raise ValueError("image is too small for an exact integer aspect ratio crop")
    new_w, new_h = w_ratio * scale, h_ratio * scale
    if (new_w, new_h) == img.size:
        return img
    left = (w - new_w) // 2
    top = (h - new_h) // 2
    return img.crop((left, top, left + new_w, top + new_h))


def is_placeholder_cell(img: Image.Image) -> bool:
    """Detect whether an image cell is a placeholder (solid gray/black).

    Samples the center 50% of the image and checks if it has low variance
    and low mean brightness.

    Args:
        img: PIL image to check.

    Returns:
        True if the cell appears to be a placeholder.
    """
    w, h = img.size
    left = w // 4
    top = h // 4
    right = w - w // 4
    bottom = h - h // 4
    center = img.crop((left, top, right, bottom))

    arr = np.array(center, dtype=np.float32)
    std = float(arr.std())
    mean = float(arr.mean())

    return std < 15 and mean < 160


def split_grid_image(
    grid_image: Image.Image,
    rows: int,
    cols: int,
    video_aspect_ratio: str,
    edge_margin: float = 0.0,
    *,
    output_size: tuple[int, int] | None = None,
) -> list[Image.Image]:
    """Split a grid image into individual cell images.

    Cells are returned in row-major order (left-to-right, top-to-bottom).
    Proportional boundaries include the canvas remainder pixels. All output
    cells have the same exact aspect ratio and pixel dimensions. The caller
    must inspect and review the source before publishing the resulting cells.

    Args:
        grid_image: Source grid PIL image.
        rows: Number of rows in the grid.
        cols: Number of columns in the grid.
        video_aspect_ratio: Target aspect ratio string, e.g. "16:9".
        edge_margin: Fraction of cell size to trim from each edge (0.0–0.5).
        output_size: Optional common raster dimensions at the exact target ratio.

    Returns:
        List of cropped cell images.
    """
    if not 0 <= edge_margin < 0.5:
        raise ValueError("edge_margin must be at least zero and less than 0.5")
    ratio_width, ratio_height = _ratio_units(video_aspect_ratio)
    if output_size is not None and (
        output_size[0] <= 0 or output_size[1] <= 0 or output_size[0] * ratio_height != output_size[1] * ratio_width
    ):
        raise ValueError("output_size must have the exact video aspect ratio")
    boxes = _cell_boxes(*grid_image.size, rows, cols)
    cells: list[Image.Image] = []
    for box in boxes:
        cell = grid_image.crop((box[0], box[1], box[2], box[3]))
        width, height = cell.size
        margin_x, margin_y = int(width * edge_margin), int(height * edge_margin)
        if margin_x or margin_y:
            cell = cell.crop((margin_x, margin_y, width - margin_x, height - margin_y))
        cells.append(center_crop_to_ratio(cell, video_aspect_ratio))
    common_width = min(cell.width for cell in cells)
    common_height = min(cell.height for cell in cells)
    common_cells = [
        cell.crop(
            (
                (cell.width - common_width) // 2,
                (cell.height - common_height) // 2,
                (cell.width - common_width) // 2 + common_width,
                (cell.height - common_height) // 2 + common_height,
            )
        )
        for cell in cells
    ]
    if output_size is not None:
        return [cell.resize(output_size, Image.Resampling.LANCZOS) for cell in common_cells]
    return common_cells

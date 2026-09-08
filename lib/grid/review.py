"""Content-bound review of grid crops before they become video first frames."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from math import gcd
from pathlib import Path
from typing import Any

from PIL import Image

from lib.api_errors import BadRequestError
from lib.grid.models import GridGeneration
from lib.grid.splitter import inspect_grid_geometry, split_grid_image
from lib.storyboard_sequence import StoryboardImageUnavailable
from lib.version_manager import VersionManager

GRID_REVIEW_VERSION = 1


class GridSplitReviewError(BadRequestError):
    """A composite needs review or does not meet the frozen geometry."""


def normalized_ratio(aspect_ratio: str) -> tuple[int, int]:
    width, height = (int(part) for part in aspect_ratio.split(":"))
    if width <= 0 or height <= 0:
        raise ValueError("aspect ratio must contain positive integers")
    divisor = gcd(width, height)
    return width // divisor, height // divisor


def first_frame_size(aspect_ratio: str) -> tuple[int, int]:
    """Use a common exact-ratio raster with a long edge at most 1024 pixels.

    This is an input raster, not a claim about the generated video's resolution.
    The multiplier is even so both dimensions are suitable for video tooling.
    """
    width, height = normalized_ratio(aspect_ratio)
    scale = (1024 // max(width, height)) // 2 * 2
    if scale < 2:
        raise ValueError("aspect ratio cannot fit the first-frame raster")
    return width * scale, height * scale


def grid_review_token(source: bytes, grid: GridGeneration, project_aspect_ratio: str) -> str:
    """Bind confirmation to image bytes, layout, scene mapping and project ratio."""
    context = json.dumps(
        {
            "version": GRID_REVIEW_VERSION,
            "grid": grid.to_dict(),
            "project_aspect_ratio": normalized_ratio(project_aspect_ratio),
            "output_size": first_frame_size(project_aspect_ratio),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(context + b"\0" + source).hexdigest()


def inspect_grid_source(image: Image.Image, grid: GridGeneration, project_aspect_ratio: str) -> dict[str, Any]:
    """Inspect measurable geometry; panel semantics still require visual review."""
    aspect = grid.video_aspect_ratio or project_aspect_ratio
    report = inspect_grid_geometry(image, grid.rows, grid.cols, aspect)
    issues = list(report["issues"])
    if normalized_ratio(aspect) != normalized_ratio(project_aspect_ratio):
        issues.append("project_aspect_changed")
    content_ids = [frame.next_scene_id for frame in grid.frame_chain if frame.frame_type != "placeholder"]
    if (
        len(grid.frame_chain) != grid.rows * grid.cols
        or grid.cell_count != grid.rows * grid.cols
        or content_ids != grid.scene_ids
        or len(set(grid.scene_ids)) != len(grid.scene_ids)
        or any(
            frame.index != index or (frame.row, frame.col) != divmod(index, grid.cols)
            for index, frame in enumerate(grid.frame_chain)
        )
    ):
        issues.append("scene_mapping_mismatch")
    report.update(valid=not issues, issues=issues)
    return dict(report)


def build_grid_split_preview(source: bytes, grid: GridGeneration, project_aspect_ratio: str) -> dict[str, Any]:
    """Return actual crop thumbnails without writing any project artifacts."""
    aspect = grid.video_aspect_ratio or project_aspect_ratio
    output_width, output_height = first_frame_size(project_aspect_ratio)
    with Image.open(io.BytesIO(source)) as image:
        image.load()
        report = inspect_grid_source(image, grid, project_aspect_ratio)
        unpreviewable = {"invalid_grid_dimensions", "invalid_cell_dimensions", "invalid_aspect_ratio"}
        cells = (
            []
            if unpreviewable.intersection(report["issues"])
            else split_grid_image(image, grid.rows, grid.cols, aspect)
        )
    previews = []
    for index, (cell, frame) in enumerate(zip(cells, grid.frame_chain, strict=False)):
        # Thumbnails show the same crop at a smaller, exact aspect ratio.
        thumb_size = first_frame_size(aspect)
        thumb = cell.resize((thumb_size[0] // 2, thumb_size[1] // 2), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        thumb.convert("RGB").save(buffer, format="PNG")
        previews.append(
            {
                "index": index,
                "row": frame.row,
                "col": frame.col,
                "scene_id": frame.next_scene_id,
                "placeholder": frame.frame_type == "placeholder",
                "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            }
        )
    return {
        **report,
        "review_token": grid_review_token(source, grid, project_aspect_ratio),
        "output_width": output_width,
        "output_height": output_height,
        "video_aspect_ratio": aspect,
        "cells": previews,
    }


def require_reviewed_grid_first_frame(
    project_path: Path, resource_id: str, image_path: Path, project_aspect_ratio: str
) -> None:
    """Reject unreviewed grid-derived inputs at both video admission and execution.

    Independently regenerated or uploaded first frames have their own selected
    version source; stale grid IDs in script metadata cannot misclassify them.
    """
    versions = VersionManager(project_path).get_versions("storyboards", resource_id)
    selected = next((record for record in versions["versions"] if record["is_current"]), None)
    if selected is None or selected.get("source") != "grid_split":
        return
    expected_size = first_frame_size(project_aspect_ratio)
    if (
        selected.get("grid_review_version") != GRID_REVIEW_VERSION
        or not selected.get("grid_review_token")
        or selected.get("first_frame_size") != list(expected_size)
    ):
        raise StoryboardImageUnavailable("多宫格首帧尚未通过切分预览确认，请检查并确认落格后再生成视频")
    try:
        with Image.open(image_path) as image:
            if image.size != expected_size:
                raise StoryboardImageUnavailable("多宫格首帧尺寸与当前项目比例不一致，请重新规划或生成首帧")
    except OSError as exc:
        raise StoryboardImageUnavailable("多宫格首帧文件无法读取，请重新生成或上传首帧") from exc

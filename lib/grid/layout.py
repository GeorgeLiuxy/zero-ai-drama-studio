"""Grid layout calculator for grid-image-to-video feature."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm
from typing import TYPE_CHECKING, Any

from lib.video_visual_provenance import resolve_video_aspect_ratio

if TYPE_CHECKING:
    from collections.abc import Sequence

_BASE_WIDTH = 1920
_MAX_GRID_SIDE = 5
_MAX_CANVAS_STRETCH = 3


def _parse_aspect_ratio(aspect_ratio: str) -> tuple[int, int]:
    """Read a positive integer width:height without substituting its orientation."""
    try:
        width, height = (int(part) for part in aspect_ratio.split(":"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"无效的画面比例 {aspect_ratio!r}，应为正整数宽:高") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"无效的画面比例 {aspect_ratio!r}，宽和高必须为正整数")
    divisor = gcd(width, height)
    return width // divisor, height // divisor


@dataclass(frozen=True)
class GridLayout:
    """Describes the layout of a grid composed of multiple scene images."""

    grid_size: str
    rows: int
    cols: int
    grid_aspect_ratio: str
    cell_count: int
    placeholder_count: int

    def pixel_dimensions(self) -> tuple[int, int]:
        """Return an exact-ratio canvas divisible into identical integer cells.

        The largest edge stays at or below the base reference when the minimum
        exact canvas fits it. Large irreducible ratios use that minimum canvas.
        These requested dimensions do not certify a provider's returned image.
        """
        if self.rows <= 0 or self.cols <= 0:
            raise ValueError("宫格行数和列数必须为正整数")
        width, height = _parse_aspect_ratio(self.grid_aspect_ratio)
        factor = lcm(self.cols // gcd(width, self.cols), self.rows // gcd(height, self.rows))
        unit_width, unit_height = width * factor, height * factor
        scale = max(1, _BASE_WIDTH // max(unit_width, unit_height))
        return unit_width * scale, unit_height * scale


# 未配置分辨率档时宫格渲染实际下发的档位（与 ``execute_grid_task`` 同源）
GRID_FALLBACK_RESOLUTION = "2K"

_LARGE_GRID_RESOLUTION = "4K"
_GATED_MAX_CELL_COUNT = 9
_MAX_CELL_COUNT = 25


def large_grid_allowed(image_resolution: str | None) -> bool:
    """Only the effective 4K resolution tier permits more than nine cells.

    This capacity guard does not guarantee sufficient pixels in a returned cell;
    the returned image still needs validation before it becomes a video input.
    """
    return (image_resolution or GRID_FALLBACK_RESOLUTION).strip().upper() == _LARGE_GRID_RESOLUTION


def max_cell_count(*, allow_large_grid: bool) -> int:
    """单张宫格的格数上限，不限定宫格必须为正方形。"""
    return _MAX_CELL_COUNT if allow_large_grid else _GATED_MAX_CELL_COUNT


def video_aspect_ratio_of(project: dict[str, Any]) -> str:
    """Return the project's exact video ratio, defaulting only when it is unset."""
    width, height = _parse_aspect_ratio(resolve_video_aspect_ratio(project))
    return f"{width}:{height}"


def grid_aspect_ratio_for(rows: int, cols: int, aspect_ratio: str) -> str:
    """Compute canvas width:height from equal cells without borders or gutters."""
    if rows <= 0 or cols <= 0:
        raise ValueError("宫格行数和列数必须为正整数")
    cell_width, cell_height = _parse_aspect_ratio(aspect_ratio)
    width, height = cols * cell_width, rows * cell_height
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def calculate_grid_layout(num_scenes: int, aspect_ratio: str, *, allow_large_grid: bool = False) -> GridLayout | None:
    """Choose a bounded rectangular layout while keeping every cell's ratio.

    Candidates have at most five rows/columns and canvas ratios from 1:3 to 3:1.
    Fewer placeholders wins first, followed by a canvas closer to square.
    Reciprocal video ratios produce transposed layouts. Counts over the capacity
    describe the first full chunk; use plan_grid_chunks to retain every scene.
    """
    if num_scenes < 1:
        return None

    cell_width, cell_height = _parse_aspect_ratio(aspect_ratio)
    cap = max_cell_count(allow_large_grid=allow_large_grid)
    effective = min(num_scenes, cap)
    candidates: list[tuple[tuple[int, Fraction, int, int, int], int, int]] = []
    for rows in range(1, _MAX_GRID_SIDE + 1):
        for cols in range(1, _MAX_GRID_SIDE + 1):
            count = rows * cols
            if not effective <= count <= cap:
                continue
            width, height = cols * cell_width, rows * cell_height
            stretch = Fraction(max(width, height), min(width, height))
            if stretch > _MAX_CANVAS_STRETCH:
                continue
            oriented = cols >= rows if cell_width <= cell_height else rows >= cols
            leading, trailing = (rows, cols) if cell_width <= cell_height else (cols, rows)
            score = (count - effective, stretch, int(not oriented), leading, trailing)
            candidates.append((score, rows, cols))

    if not candidates:
        raise ValueError(
            f"视频比例 {aspect_ratio} 无法在当前单张上限 {cap} 格内为 {effective} 个分镜安排可靠宫格；"
            "请减少同组分镜数量或逐张生成首帧"
        )
    _, rows, cols = min(candidates)
    count = rows * cols
    return GridLayout(
        grid_size=f"grid_{count}",
        rows=rows,
        cols=cols,
        grid_aspect_ratio=grid_aspect_ratio_for(rows, cols, aspect_ratio),
        cell_count=count,
        placeholder_count=count - effective,
    )


def plan_grid_chunks[T](
    group: Sequence[T],
    aspect_ratio: str,
    *,
    allow_large_grid: bool = False,
) -> list[tuple[list[T], GridLayout]]:
    """Split a scene group in order and choose the exact layout for every chunk.

    Generation, previews and cost estimates consume this same plan. Placeholders
    belong to a canvas only and never enter the ordered scene list.
    """
    cap = max_cell_count(allow_large_grid=allow_large_grid)
    plans: list[tuple[list[T], GridLayout]] = []
    for offset in range(0, len(group), cap):
        chunk = list(group[offset : offset + cap])
        layout = calculate_grid_layout(len(chunk), aspect_ratio, allow_large_grid=allow_large_grid)
        if layout is not None:
            plans.append((chunk, layout))
    return plans

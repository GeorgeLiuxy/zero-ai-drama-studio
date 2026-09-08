"""Grid prompt builder for grid-image-to-video feature."""

from __future__ import annotations

from collections.abc import Mapping
from math import gcd

from lib.grid.layout import grid_aspect_ratio_for


def project_grid_image_prompt(image_prompt: object) -> str | dict[str, object]:
    """Project grid image semantics into the canonical provider/basis shape."""

    if not isinstance(image_prompt, Mapping):
        return str(image_prompt)
    scene = image_prompt.get("scene")
    if scene is None:
        scene = ""
    if not isinstance(scene, str):
        raise ValueError("grid image_prompt.scene must be a string")
    raw_composition = image_prompt.get("composition")
    if raw_composition is None:
        raw_composition = {}
    if not isinstance(raw_composition, Mapping):
        raise ValueError("grid image_prompt.composition must be an object")
    if any(not isinstance(key, str) for key in raw_composition):
        raise ValueError("grid image_prompt.composition keys must be strings")
    composition: dict[str, str] = {}
    for key in sorted(raw_composition):
        raw_value = raw_composition[key]
        if raw_value is None:
            continue
        value = str(raw_value)
        if value:
            composition[key] = value
    return {"scene": scene, "composition": composition}


def _extract_image_desc(scene: dict) -> str:
    """Extract image description from a scene.

    If image_prompt is a dict, join scene + composition fields.
    If string, return as-is.
    """
    image_prompt = project_grid_image_prompt(scene.get("image_prompt", ""))
    if isinstance(image_prompt, str):
        return image_prompt
    parts: list[str] = []
    scene_text = image_prompt["scene"]
    if scene_text:
        parts.append(str(scene_text))
    composition = image_prompt["composition"]
    if isinstance(composition, Mapping):
        comp_parts = [f"{key}: {value}" for key, value in composition.items()]
        if comp_parts:
            parts.append("，".join(comp_parts))
    return "；".join(parts) if parts else ""


def _compute_panel_aspect(grid_aspect_ratio: str, rows: int, cols: int) -> str:
    """从整体宫格比例推算单格比例。

    例：grid 4:3, 3行2列 → panel (4/2):(3/3) = 2:1
    """
    gw, gh = (int(x) for x in grid_aspect_ratio_for(1, 1, grid_aspect_ratio).split(":"))
    if rows <= 0 or cols <= 0:
        raise ValueError("宫格行数和列数必须为正整数")
    pw = gw * rows  # 交叉相乘避免浮点
    ph = gh * cols
    g = gcd(pw, ph)
    return f"{pw // g}:{ph // g}"


def build_grid_prompt(
    *,
    scenes: list[dict],
    id_field: str,
    rows: int,
    cols: int,
    style: str,
    aspect_ratio: str = "16:9",
    grid_aspect_ratio: str | None = None,
    reference_image_mapping: dict[str, str] | None = None,
) -> str:
    """Assemble one static first-frame image per scene in a fixed equal-cell grid.

    Args:
        scenes: List of scene dicts with image_prompt and video_prompt fields.
        id_field: Key in each scene dict for the scene ID.
        rows: Number of rows in the grid.
        cols: Number of columns in the grid.
        style: Style description for the grid.
        aspect_ratio: Aspect ratio for each cell (default "16:9").
        reference_image_mapping: Optional mapping of image labels to character names.

    Returns:
        Assembled prompt string.
    """
    effective_grid_ar = grid_aspect_ratio_for(rows, cols, aspect_ratio)
    if grid_aspect_ratio is not None and grid_aspect_ratio_for(1, 1, grid_aspect_ratio) != effective_grid_ar:
        raise ValueError(
            f"宫格整体比例 {grid_aspect_ratio} 与 {rows} 行 {cols} 列、单格比例 {aspect_ratio} 不一致，"
            f"整体比例应为 {effective_grid_ar}"
        )
    panel_ar = _compute_panel_aspect(effective_grid_ar, rows, cols)
    total = rows * cols
    n_scenes = len(scenes)
    if n_scenes > total:
        # 超员场景在成图中没有对应画格，切格回填会按位置错配——调用方应先按
        # max_cell_count 切块（见 lib.grid.layout.plan_grid_chunks），此处 fail loud。
        raise ValueError(f"分镜数 {n_scenes} 超过 {rows}×{cols} 宫格的画格数 {total}，分组应先切块再构建 prompt")

    lines: list[str] = []

    # Header
    lines.append(
        f"你是一位专业的分镜画师。请严格按照 {rows}×{cols} 宫格布局生成一张包含恰好 {total} 个等大画格的联合图。"
    )
    lines.append("")

    # Layout requirements
    lines.append("【布局要求】")
    lines.append(f"- 恰好 {rows} 行 {cols} 列，共 {total} 个画格，阅读顺序：从左到右，从上到下")
    lines.append(f"- 整体图片比例：{effective_grid_ar}")
    lines.append(f"- 每个画格比例：{panel_ar}，所有画格大小完全相同")
    lines.append(f"- 所有行严格等高，所有列严格等宽：每格宽为整图宽的 1/{cols}，高为整图高的 1/{rows}")
    lines.append("- 画格之间无边框、无间隙、无留白，紧密排列")
    lines.append("- 不得合并画格、不得遗漏画格、不得错位排列")
    lines.append("- 所有画格保持一致的角色外观、光线和色彩风格")
    lines.append("")

    # Each destination scene receives one complete video first frame.
    lines.append("【首帧要求】")
    lines.append("- 每个内容格仅绘制对应分镜开始时的一张完整静态画面，用作该分镜的视频首帧")
    lines.append("- 每格只有一个镜头和一个时间点，不在同一格内展示动作过程、前后对比或多个场景")
    lines.append("- 相邻格保持角色和场景的一致性，每个镜头的构图分别完整地容纳在自己的画格内")
    lines.append("")

    # Reference images (optional)
    if reference_image_mapping:
        lines.append("【参考图说明】")
        for label, character in reference_image_mapping.items():
            lines.append(f"- {label}：{character}")
        lines.append("")

    # Cell contents
    lines.append("【各格内容】")

    for cell_idx in range(total):
        row_num = cell_idx // cols + 1
        col_num = cell_idx % cols + 1
        position = f"row{row_num} col{col_num}"

        if cell_idx < n_scenes:
            scene = scenes[cell_idx]
            scene_id = scene.get(id_field, "")
            image_desc = _extract_image_desc(scene)
            lines.append(f"格{cell_idx}（{position}）— {scene_id}首帧：")
            lines.append(f"  {image_desc}")

        else:
            # Placeholder
            lines.append(f"格{cell_idx}（{position}）— 空占位：纯灰色背景，无任何内容")

    lines.append("")

    # Style requirements
    lines.append("【风格要求】")
    lines.append(style)
    lines.append("")

    # Negative constraints
    lines.append("【负面约束】")
    lines.append("禁止出现以下任何元素：")
    lines.append("- 文字、字幕、标签、标题、数字编号、时间戳")
    lines.append("- 水印、logo、签名")
    lines.append("- 白色边框、黑色边框、粗边框、装饰性边框")
    lines.append("- 分隔线、间隙、间距、留白、padding、margin")
    lines.append("- 白色背景、纯色背景条")
    lines.append("- 合并的画格、缺失的画格、错位的画格")
    lines.append("- 跨格的连续全景图、单格内再次分屏、画中画、同格多镜头或多个时间点")
    lines.append("- 模糊、低画质、噪点")
    lines.append("- 拼贴感、蒙太奇拼接感")
    lines.append("- 画格大小不一致、画格比例不一致")

    return "\n".join(lines)

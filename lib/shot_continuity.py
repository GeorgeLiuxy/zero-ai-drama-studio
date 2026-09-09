"""Shot state contracts and read-only context shared by planning and generation.

World positions and camera-relative screen positions are separate facts. A cut
may change the latter without moving furniture or teleporting a character.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def strict_motion_review_enabled() -> bool:
    """Optional visual admission; prompt continuity remains available independently."""
    return os.environ.get("ARCREEL_STRICT_MOTION_REVIEW", "").strip().lower() in {"1", "true", "yes"}


class ContinuityFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, description="角色、道具或固定场景锚点的稳定名称；相邻镜沿用同名")
    attribute: Literal["pose", "world_position", "facing", "holding", "condition", "layout", "screen_position"]
    value: str = Field(
        min_length=1, description="简短状态值；未变化时逐字沿用，无法确定填 unknown；世界位置不使用画面左右"
    )


class ShotContinuity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_group: str = Field(min_length=1, description="同一时空段落的稳定 ID，与宫格分批无关")
    incoming_transition: Literal["scene_start", "continuous_action", "same_scene_cut", "time_jump", "scene_change"]
    transition_reason: str = Field(default="", description="换时空、跨轴线或改变机位时的叙事与空间依据")
    camera_side: str = Field(min_length=1, description="相对于固定场景轴线的机位侧；不确定填 unknown")
    start_state: list[ContinuityFact] = Field(min_length=1, description="本镜首帧时的角色姿态、空间与道具状态")
    end_state: list[ContinuityFact] = Field(min_length=1, description="本镜动作结束后的状态，下镜据此承接")
    action_beats: list[str] = Field(min_length=1, description="按发生顺序写可观察动作；静止镜头明确写保持状态")
    uncertainties: list[str] = Field(default_factory=list, description="无法从剧本确定且影响衔接的问题，不能编造答案")

    @model_validator(mode="after")
    def unique_facts(self) -> ShotContinuity:
        for facts in (self.start_state, self.end_state):
            keys = [(fact.subject, fact.attribute) for fact in facts]
            if len(keys) != len(set(keys)):
                raise ValueError("continuity state contains duplicate subject/attribute facts")
        if any(not beat.strip() for beat in self.action_beats):
            raise ValueError("continuity action beats must not be blank")
        return self


CONTINUITY_PLANNING_RULES = """## 跨镜状态与空间连续性
为每个分镜填写 continuity；先确定同一时空段落，再设计各镜头，宫格数量不决定剧情分段。
start_state 是首帧，action_beats 是本镜实际发生的动作，end_state 是动作结果。
相邻同场景镜头的物理状态按上一镜 end_state 承接；未变化的事实沿用同一 subject/attribute/value。
只记录影响叙事的姿态、世界位置、朝向、持物与固定布置，无法确定的事实填 unknown 并说明 uncertainties。
world_position/layout 以门、床、墙等固定锚点为基准；screen_position 仅表示当前画面左右，二者不能混用。
camera_side 以同一条场景轴线命名。同场景可换景别或正反打，但无依据跨轴或镜像场景要提出问题。
连续动作使用 continuous_action；正常剪辑使用 same_scene_cut，不把下一镜首帧一律当成本镜尾帧。
换场景/跳时分别使用 scene_change/time_jump，解释 transition_reason；新场景同步 segment_break。
例如「蹲在床旁→站稳→走到床边→坐下」应按可用时长安排动作，不把坐下结果提前画进起身镜头的首帧。
每镜动作量服从实际模型时长；若动作放不下，规划阶段拆镜，不删台词、不任意提高秒数。
"""


def continuity_issues(items: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Compare explicit physical facts; do not infer motion from screen left/right."""
    issues: list[dict[str, str]] = []
    previous: ShotContinuity | None = None
    previous_id = ""
    for index, item in enumerate(items):
        unit_id = str(item.get("scene_id") or item.get("segment_id") or index)
        raw = item.get("continuity")
        current = ShotContinuity.model_validate(raw) if raw is not None else None
        if current is None:
            previous, previous_id = None, unit_id
            continue
        if index == 0 and current.incoming_transition in {"continuous_action", "same_scene_cut"}:
            issues.append(
                {
                    "unit_id": unit_id,
                    "code": "missing_predecessor",
                    "detail": "First shot cannot inherit an absent predecessor",
                }
            )
        if current.incoming_transition in {"time_jump", "scene_change"} and not current.transition_reason.strip():
            issues.append(
                {
                    "unit_id": unit_id,
                    "code": "missing_transition_reason",
                    "detail": "A time or scene change needs an explanation",
                }
            )
        if previous is not None and current.incoming_transition in {"continuous_action", "same_scene_cut"}:
            if previous.scene_group != current.scene_group:
                issues.append(
                    {
                        "unit_id": unit_id,
                        "code": "scene_group_conflict",
                        "detail": f"{previous_id} and {unit_id} claim the same scene but use different scene groups",
                    }
                )
            old = {(f.subject, f.attribute): f.value for f in previous.end_state if f.attribute != "screen_position"}
            for fact in current.start_state:
                prior = old.get((fact.subject, fact.attribute))
                if prior is not None and "unknown" not in {prior, fact.value} and prior != fact.value:
                    issues.append(
                        {
                            "unit_id": unit_id,
                            "code": "state_gap",
                            "detail": f"{previous_id} -> {unit_id}: {fact.subject}.{fact.attribute}: {prior} -> {fact.value}",
                        }
                    )
            if (
                previous.camera_side != current.camera_side
                and "unknown" not in {previous.camera_side, current.camera_side}
                and not current.transition_reason.strip()
            ):
                issues.append(
                    {
                        "unit_id": unit_id,
                        "code": "unexplained_camera_side",
                        "detail": f"{previous_id} -> {unit_id}: camera side changed without a spatial explanation",
                    }
                )
        previous, previous_id = current, unit_id
    return issues


def require_continuity(items: Sequence[Mapping[str, Any]]) -> None:
    issues = continuity_issues(items)
    if issues:
        raise ValueError("shot_continuity_conflict: " + "; ".join(f"{i['unit_id']}: {i['detail']}" for i in issues[:8]))


def continuity_context(items: Sequence[Mapping[str, Any]], target_ids: Sequence[str]) -> str:
    """Include scene-group state plus immediate neighbors without expanding write scope."""
    targets = set(target_ids)
    selected = {i for i, item in enumerate(items) if item.get("scene_id") in targets}
    groups = {
        str(items[i].get("continuity", {}).get("scene_group"))
        for i in selected
        if isinstance(items[i].get("continuity"), dict)
    }
    context_indices = {j for i in selected for j in (i - 1, i, i + 1) if 0 <= j < len(items)}
    for i, item in enumerate(items):
        plan = item.get("continuity")
        if isinstance(plan, dict) and str(plan.get("scene_group")) in groups:
            context_indices.add(i)
    keys = (
        "scene_id",
        "segment_break",
        "continuity",
        "scene_description",
        "image_prompt",
        "video_prompt",
        "characters_in_scene",
        "scenes",
        "props",
    )
    context = [{k: items[i][k] for k in keys if k in items[i]} for i in sorted(context_indices)]
    if not context:
        return ""
    data = json.dumps(context, ensure_ascii=False, indent=2).replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        "\n<continuity_context>\n以下仅为衔接参考数据，不扩大本次输出/改写范围。"
        "未选中镜头保持原样；图片只表示各镜开始状态，不等于上一镜结束状态。\n" + data + "\n</continuity_context>\n"
    )


def grid_continuity_context(items: Sequence[Mapping[str, Any]], target_ids: Sequence[str]) -> str:
    """Legacy grids retain their basis; explicitly planned grids share cross-page context."""
    if not any(item.get("scene_id") in target_ids and item.get("continuity") for item in items):
        return ""
    return continuity_context(items, target_ids)

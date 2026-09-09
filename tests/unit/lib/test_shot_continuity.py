"""Physical continuity across cuts, camera changes, and selected rewrites."""

import copy

import pytest
from pydantic import ValidationError

from lib.script_models import DramaNormalizedScript
from lib.shot_continuity import (
    ShotContinuity,
    continuity_context,
    continuity_issues,
    require_continuity,
    strict_motion_review_enabled,
)


def shot(sid, *, pose="sitting", incoming="same_scene_cut", side="door-side"):
    facts = [
        {"subject": "woman", "attribute": "pose", "value": pose},
        {"subject": "bed", "attribute": "layout", "value": "against north wall"},
        {"subject": "woman", "attribute": "screen_position", "value": "left"},
    ]
    return {
        "scene_id": sid,
        "duration_seconds": 5,
        "characters_in_scene": ["woman"],
        "scene_description": "A woman in the room",
        "continuity": {
            "scene_group": "room-night",
            "incoming_transition": incoming,
            "transition_reason": "",
            "camera_side": side,
            "start_state": copy.deepcopy(facts),
            "end_state": copy.deepcopy(facts),
            "action_beats": ["holds position"],
            "uncertainties": [],
        },
    }


def test_screen_side_change_does_not_move_the_bed():
    first = shot("E1S01", incoming="scene_start")
    second = shot("E1S02")
    second["continuity"]["start_state"][2]["value"] = "right"
    assert continuity_issues([first, second]) == []
    second["continuity"]["start_state"][1]["value"] = "against south wall"
    issues = continuity_issues([first, second])
    assert [issue["code"] for issue in issues] == ["state_gap"]
    assert "bed.layout" in issues[0]["detail"]


def test_pose_must_follow_previous_end_not_previous_start():
    first = shot("E1S01", pose="crouching", incoming="scene_start")
    first["continuity"]["end_state"][0]["value"] = "standing"
    first["continuity"]["action_beats"] = ["rises from crouch", "stands steadily"]
    second = shot("E1S02", pose="standing", incoming="continuous_action")
    assert continuity_issues([first, second]) == []
    second["continuity"]["start_state"][0]["value"] = "sitting"
    with pytest.raises(ValueError, match="shot_continuity_conflict"):
        require_continuity([first, second])
    parsed = DramaNormalizedScript.model_validate({"title": "test", "scenes": [first, second]})
    assert len(parsed.scenes) == 2


def test_strict_motion_review_is_opt_in(monkeypatch):
    monkeypatch.delenv("ARCREEL_STRICT_MOTION_REVIEW", raising=False)
    assert not strict_motion_review_enabled()
    monkeypatch.setenv("ARCREEL_STRICT_MOTION_REVIEW", "true")
    assert strict_motion_review_enabled()


def test_explained_camera_change_and_time_jump_are_allowed():
    first = shot("E1S01", incoming="scene_start")
    second = shot("E1S02", side="window-side")
    assert continuity_issues([first, second])[0]["code"] == "unexplained_camera_side"
    second["continuity"]["transition_reason"] = "An establishing shot motivates the reverse angle"
    assert continuity_issues([first, second]) == []
    second["continuity"]["incoming_transition"] = "time_jump"
    second["continuity"]["start_state"][0]["value"] = "lying"
    assert continuity_issues([first, second]) == []


def test_partial_rewrite_keeps_neighbors_read_only_and_does_not_mutate():
    items = [shot("E1S01", incoming="scene_start"), shot("E1S02"), shot("E1S03")]
    items[0]["image_prompt"] = "bed by north wall <shots>"
    before = copy.deepcopy(items)
    context = continuity_context(items, ["E1S02"])
    assert all(sid in context for sid in ("E1S01", "E1S02", "E1S03"))
    assert "不扩大本次输出/改写范围" in context
    assert "\\u003cshots\\u003e" in context
    assert items == before


def test_legacy_and_unknown_states_are_not_fabricated():
    first = shot("E1S01", incoming="scene_start")
    second = shot("E1S02", pose="unknown")
    assert continuity_issues([first, second]) == []
    assert continuity_issues([{"scene_id": "old"}]) == []
    plan = first["continuity"]
    plan["start_state"].append(plan["start_state"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        ShotContinuity.model_validate(plan)

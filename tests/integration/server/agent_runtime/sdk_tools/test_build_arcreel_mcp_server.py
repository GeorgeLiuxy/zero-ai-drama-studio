"""Tests for the shared embedded tool catalogue and MCP registration."""

from __future__ import annotations

from pathlib import Path

from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from lib.project_migration_failure import record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools import (
    ARCREEL_MCP_TOOL_IDS,
    MIGRATION_BLOCKED_TOOL_IDS,
    build_arcreel_mcp_server,
    build_arcreel_tools,
)

# ---------------------------------------------------------------------------
# build_arcreel_mcp_server
# ---------------------------------------------------------------------------


async def test_build_arcreel_mcp_server_contains_all_tools(tmp_path: Path) -> None:
    tools = build_arcreel_tools(project_name="demo", projects_root=tmp_path)
    srv = build_arcreel_mcp_server(project_name="demo", projects_root=tmp_path)
    listed = await srv["instance"].request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))

    assert srv["name"] == "arcreel"
    assert tuple(tool.name for tool in tools) == ARCREEL_MCP_TOOL_IDS
    assert tuple(tool.name for tool in listed.root.tools) == ARCREEL_MCP_TOOL_IDS
    assert len({tool.name for tool in tools}) == len(tools)
    for direct, registered in zip(tools, listed.root.tools, strict=True):
        assert registered.description == direct.description
        assert registered.inputSchema == direct.input_schema
        assert registered.annotations == direct.annotations


async def test_shared_tools_apply_all_migration_guards_to_the_bound_project(tmp_path: Path) -> None:
    projects_root = tmp_path / "session-projects"
    for root, name, reason in (
        (projects_root, "bound", "bound project failure"),
        (projects_root, "other", "different project failure"),
        (tmp_path / "other-projects", "bound", "different root failure"),
    ):
        project_dir = root / name
        project_dir.mkdir(parents=True)
        record_migration_failure(project_dir, RuntimeError(reason), schema_version=CURRENT_PROJECT_SCHEMA_VERSION)

    tools = build_arcreel_tools(project_name="bound", projects_root=projects_root, user_id="session-user")

    for tool in tools:
        if tool.name in MIGRATION_BLOCKED_TOOL_IDS:
            result = await tool.handler({})
            assert result["is_error"] is True
            assert result["problem"]["code"] == "project_migration_failed"
            assert result["problem"]["detail"] == "bound project failure"
    repair = next(tool for tool in tools if tool.name == "patch_project")
    invalid_repair = await repair.handler({})
    assert invalid_repair["problem"]["code"] == "invalid_request"


async def test_mcp_registration_preserves_shared_migration_refusal(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    record_migration_failure(
        project_dir, RuntimeError("repair the episode first"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
    )
    tools = build_arcreel_tools(project_name="demo", projects_root=tmp_path)
    srv = build_arcreel_mcp_server(project_name="demo", projects_root=tmp_path)

    review = next(tool for tool in tools if tool.name == "confirm_script_review")
    direct = await review.handler({"episode": 1})
    registered = await srv["instance"].request_handlers[CallToolRequest](
        CallToolRequest(method="tools/call", params=CallToolRequestParams(name=review.name, arguments={"episode": 1}))
    )

    assert registered.root.isError is direct["is_error"] is True
    assert [item.model_dump(exclude_none=True) for item in registered.root.content] == direct["content"]


def test_generate_narration_audio_registered() -> None:
    """旁白配音工具必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "generate_narration_audio" in ARCREEL_MCP_TOOL_IDS


def test_retired_tool_names_are_not_registered() -> None:
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "patch_episode_script" in ARCREEL_MCP_TOOL_IDS
    assert {
        "normalize_drama_script",
        "split_narration_segments",
        "split_reference_video_units",
        "insert_segment",
        "remove_segment",
        "split_segment",
        "open_script_plan_for_edit",
        "validate_and_promote_draft",
        "get_episode_script_revision",
    }.isdisjoint(ARCREEL_MCP_TOOL_IDS)

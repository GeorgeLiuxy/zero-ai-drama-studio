"""Codex memory adapters use the real, caller-bound memory store without network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import SdkMcpTool

from lib.agent_memory_index import INDEX_MAX_LINES
from lib.agent_memory_paths import project_memory_dir, user_memory_dir
from lib.agent_memory_store import MAX_FILE_BYTES, AgentMemoryStore
from server.agent_runtime.codex_support_tools import build_codex_support_tools, load_memory_context


def _tools(root: Path, project: str = "demo", user: str = "alice") -> dict[str, SdkMcpTool[Any]]:
    (root / project).mkdir(parents=True, exist_ok=True)
    return {
        entry.name: entry for entry in build_codex_support_tools(project_name=project, projects_root=root, user_id=user)
    }


def _payload(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["content"][0]["text"])


@pytest.mark.parametrize("scope", ["project", "user"])
async def test_memory_round_trip_uses_existing_store_and_scopes(tmp_path: Path, scope: str) -> None:
    tools = _tools(tmp_path)
    empty = _payload(await tools["list_agent_memory"].handler({"scope": scope}))["memory"]
    assert empty["files"] == []
    assert empty["index"]["exists"] is False
    assert "path" not in empty

    directory = project_memory_dir(tmp_path / "demo") if scope == "project" else user_memory_dir(tmp_path, "alice")
    assert not directory.exists()
    saved = await tools["write_agent_memory"].handler({"scope": scope, "filename": "style.md", "content": "使用暖色。"})
    assert _payload(saved)["memory"]["saved"] is True
    assert AgentMemoryStore(directory).read("style.md").decode("utf-8") == "使用暖色。"
    await tools["write_agent_memory"].handler({"scope": scope, "filename": "MEMORY.md", "content": "[style](style.md)"})
    listed = _payload(await tools["list_agent_memory"].handler({"scope": scope}))["memory"]
    assert listed["index"]["exists"] is True
    assert [item["name"] for item in listed["files"]] == ["style.md"]
    read = _payload(await tools["read_agent_memory"].handler({"scope": scope, "filename": "style.md"}))["memory"]
    assert read == {"scope": scope, "filename": "style.md", "content": "使用暖色。"}

    second = _tools(tmp_path, project="other", user="alice")
    third = _tools(tmp_path, project="demo", user="bob")
    same_user = await second["read_agent_memory"].handler({"scope": scope, "filename": "style.md"})
    same_project = await third["read_agent_memory"].handler({"scope": scope, "filename": "style.md"})
    assert (same_user.get("is_error") is True) == (scope == "project")
    assert (same_project.get("is_error") is True) == (scope == "user")


@pytest.mark.parametrize("extra", [{"user_id": "bob"}, {"project_name": "other"}, {"path": "../outside.md"}])
async def test_arguments_cannot_change_bound_identity(tmp_path: Path, extra: dict[str, str]) -> None:
    tools = _tools(tmp_path)
    result = await tools["write_agent_memory"].handler(
        {"scope": "user", "filename": "note.md", "content": "must not be written", **extra}
    )

    assert result.get("is_error") is True
    assert _payload(result)["problem"]["code"] == "invalid_request"
    assert not user_memory_dir(tmp_path, "alice").exists()
    assert not user_memory_dir(tmp_path, "bob").exists()


@pytest.mark.parametrize("filename", ["../outside.md", "..\\outside.md", ".env", "nested/note.md", "note.txt"])
async def test_memory_filename_restrictions_survive_tool_adapter(tmp_path: Path, filename: str) -> None:
    tools = _tools(tmp_path)
    result = await tools["write_agent_memory"].handler({"scope": "project", "filename": filename, "content": "denied"})

    assert result.get("is_error") is True
    assert _payload(result)["problem"]["code"] == "memory_invalid_filename"
    assert not project_memory_dir(tmp_path / "demo").exists()
    assert not (tmp_path / "outside.md").exists()


async def test_oversize_and_missing_memory_report_store_errors(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    missing = await tools["read_agent_memory"].handler({"scope": "user", "filename": "missing.md"})
    oversized = await tools["write_agent_memory"].handler(
        {"scope": "user", "filename": "huge.md", "content": "x" * (MAX_FILE_BYTES + 1)}
    )

    assert _payload(missing)["problem"]["code"] == "memory_file_not_found"
    assert _payload(oversized)["problem"]["code"] == "memory_file_too_large"
    assert not (user_memory_dir(tmp_path, "alice") / "huge.md").exists()


async def test_memory_context_reads_only_bounded_indexes_without_creating_directories(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    args = {"project_name": "demo", "projects_root": tmp_path, "user_id": "alice"}
    assert await load_memory_context(**args) == ""
    assert not project_memory_dir(tmp_path / "demo").exists()
    assert not user_memory_dir(tmp_path, "alice").exists()

    project_store = AgentMemoryStore(project_memory_dir(tmp_path / "demo"))
    project_store.write("MEMORY.md", ("project preference\n" * INDEX_MAX_LINES + "OMITTED PROJECT TAIL").encode())
    project_store.write("details.md", b"TOPICAL DETAIL SHOULD NOT BE LOADED")
    user_store = AgentMemoryStore(user_memory_dir(tmp_path, "alice"))
    user_store.write("MEMORY.md", b"[brief replies](brief.md)")
    context = await load_memory_context(**args)

    assert "project MEMORY.md:" in context
    assert "user MEMORY.md:" in context
    assert "[brief replies](brief.md)" in context
    assert "OMITTED PROJECT TAIL" not in context
    assert "TOPICAL DETAIL SHOULD NOT BE LOADED" not in context
    assert "200" in context


async def test_unreadable_index_does_not_hide_other_scope(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    AgentMemoryStore(project_memory_dir(tmp_path / "demo")).write("MEMORY.md", b"\xff")
    AgentMemoryStore(user_memory_dir(tmp_path, "alice")).write("MEMORY.md", b"remember user preference")

    context = await load_memory_context(project_name="demo", projects_root=tmp_path, user_id="alice")

    assert "project MEMORY.md" not in context
    assert "user MEMORY.md:\nremember user preference" in context

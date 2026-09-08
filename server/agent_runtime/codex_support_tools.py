"""Project/user-bound memory tools for the Codex runtime (ADR 0074)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import SdkMcpTool, tool
from pydantic import BaseModel, ConfigDict, ValidationError

from lib.agent_memory_index import INDEX_FILENAME, truncate_memory_index
from lib.agent_memory_paths import project_memory_dir, user_memory_dir
from lib.agent_memory_store import AgentMemoryStore
from lib.api_errors import ApiError
from lib.db.base import DEFAULT_USER_ID
from lib.path_safety import safe_join
from lib.project_manager import ProjectManager

CODEX_SUPPORT_TOOL_IDS = ("list_agent_memory", "read_agent_memory", "write_agent_memory")


class _MemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["project", "user"]


class _ReadMemoryRequest(_MemoryRequest):
    filename: str


class _WriteMemoryRequest(_ReadMemoryRequest):
    content: str


def _store(*, project_name: str, projects_root: Path, user_id: str, scope: str) -> AgentMemoryStore:
    if scope == "project":
        project = safe_join(projects_root, ProjectManager.normalize_project_name(project_name), must_exist=True)
        directory = safe_join(project, project_memory_dir(project))
    elif scope == "user":
        directory = safe_join(projects_root, user_memory_dir(projects_root, user_id))
    else:
        raise ValueError("invalid memory scope")
    return AgentMemoryStore(directory)


def _response(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
    if is_error:
        result["is_error"] = True
    return result


async def _run(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return _response({"memory": await asyncio.to_thread(operation)})
    except ApiError as exc:
        return _response({"problem": {"code": exc.key, "params": exc.params}}, is_error=True)
    except (ValidationError, ValueError, OSError):
        return _response(
            {"problem": {"code": "invalid_request", "detail": "Memory request could not be read or saved."}},
            is_error=True,
        )


def build_codex_support_tools(
    *, project_name: str, projects_root: Path, user_id: str = DEFAULT_USER_ID
) -> list[SdkMcpTool[Any]]:
    """Build tools whose arguments cannot select another project or user."""
    root = projects_root.resolve()

    def store_for(request: _MemoryRequest) -> AgentMemoryStore:
        return _store(project_name=project_name, projects_root=root, user_id=user_id, scope=request.scope)

    @tool(
        "list_agent_memory",
        "List this project's or this user's memory index metadata and topical files.",
        _MemoryRequest.model_json_schema(),
    )
    async def list_memory(args: dict[str, Any]) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            request = _MemoryRequest.model_validate(args)
            overview = store_for(request).overview()
            overview.pop("path", None)
            return {"scope": request.scope, **overview}

        return await _run(operation)

    @tool(
        "read_agent_memory",
        "Read one relevant top-level .md memory file, including MEMORY.md.",
        _ReadMemoryRequest.model_json_schema(),
    )
    async def read_memory(args: dict[str, Any]) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            request = _ReadMemoryRequest.model_validate(args)
            content = store_for(request).read(request.filename).decode("utf-8")
            return {"scope": request.scope, "filename": request.filename, "content": content}

        return await _run(operation)

    @tool(
        "write_agent_memory",
        "Save complete UTF-8 content of one top-level .md memory file. Read before replacing; "
        "keep durable preferences in the correct scope and maintain MEMORY.md links.",
        _WriteMemoryRequest.model_json_schema(),
    )
    async def write_memory(args: dict[str, Any]) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            request = _WriteMemoryRequest.model_validate(args)
            content = request.content.encode("utf-8")
            store_for(request).write(request.filename, content)
            return {"scope": request.scope, "filename": request.filename, "saved": True, "byte_size": len(content)}

        return await _run(operation)

    return [list_memory, read_memory, write_memory]


async def load_memory_context(*, project_name: str, projects_root: Path, user_id: str = DEFAULT_USER_ID) -> str:
    """Read only the two bounded indexes, without creating missing directories."""

    def read_indexes() -> str:
        indexes: list[str] = []
        for scope in ("project", "user"):
            try:
                memory = _store(
                    project_name=project_name, projects_root=projects_root.resolve(), user_id=user_id, scope=scope
                )
                content = memory.read(INDEX_FILENAME).decode("utf-8")
            except (ApiError, ValueError, OSError):
                continue
            if content.strip():
                indexes.append(f"{scope} MEMORY.md:\n{truncate_memory_index(content)}")
        return "\n\n".join(indexes)

    return await asyncio.to_thread(read_indexes)

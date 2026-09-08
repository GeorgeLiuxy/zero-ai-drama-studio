"""Codex transcript mirroring and prefix forks through the real SessionStore."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from claude_agent_sdk import get_session_messages_from_store

from lib.agent_session_store import make_project_key
from lib.agent_session_store.prefix_fork import copy_session_prefix
from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.codex_history import CodexHistory, restoration, session_runtime_overrides


def _marker(session_id: str, turn_id: str | None = "turn-1", *, completed: bool = False) -> dict:
    return {
        "session_id": session_id,
        "thread_id": "codex-thread",
        "model": "account-model",
        "effort": "medium",
        "account_hash": "account-hash",
        "turn_id": turn_id,
        "turn_completed": completed,
    }


async def test_normalized_history_round_trips_through_sdk_reader(session_factory, tmp_path: Path):
    store = DbSessionStore(session_factory)
    session_id = str(uuid4())
    history = CodexHistory(store, tmp_path, session_id)
    initial = _marker(session_id)
    messages = [
        {"type": "system", "uuid": "init", "subtype": "init"},
        {"type": "user", "uuid": "user-1", "content": "Read the source"},
        {
            "type": "assistant",
            "uuid": "tool-call",
            "model": "account-model",
            "content": [{"type": "tool_use", "id": "call-1", "name": "get_source_text", "input": {"path": "a.txt"}}],
        },
        {
            "type": "user",
            "uuid": "tool-result",
            "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "source text"}],
        },
        {"type": "assistant", "uuid": "answer-1", "content": [{"type": "text", "text": "I read it."}]},
    ]
    for message in messages:
        await history.append(message, initial)
    await history.append({"type": "stream_event", "uuid": "delta", "event": {}}, initial)
    await history.append(
        {"type": "result", "uuid": "done-1", "result": "I read it."}, _marker(session_id, completed=True)
    )

    history = CodexHistory(store, tmp_path, session_id)
    await history.append({"type": "user", "uuid": "user-2", "content": "Continue"}, _marker(session_id, "turn-2"))
    await history.append({"type": "assistant", "uuid": "answer-2", "content": "Done"}, _marker(session_id, "turn-2"))
    # Duplicate delivery must not create a self-parent or detach the next message.
    await history.append({"type": "user", "uuid": "user-2", "content": "Continue"}, _marker(session_id, "turn-2"))

    entries = await history.load()
    assert [entry["uuid"] for entry in entries] == [
        "init",
        "user-1",
        "tool-call",
        "tool-result",
        "answer-1",
        "done-1",
        "user-2",
        "answer-2",
    ]
    assert entries[-2]["parentUuid"] == "answer-1"
    assert entries[-1]["parentUuid"] == "user-2"
    assert all(entry["sessionId"] == session_id and entry["timestamp"] for entry in entries)
    assert entries[2]["message"]["model"] == "account-model"
    assert entries[5]["codex"]["turn_completed"] is True
    assert "message" not in messages[1]

    restored = await get_session_messages_from_store(store, session_id, directory=str(tmp_path))
    assert [message.uuid for message in restored] == [
        "user-1",
        "tool-call",
        "tool-result",
        "answer-1",
        "user-2",
        "answer-2",
    ]
    assert restored[1].message["content"] == messages[2]["content"]
    assert restored[2].message["content"] == messages[3]["content"]
    assert restoration(entries, session_id) == {
        "thread_id": "codex-thread",
        "fork": False,
        "last_turn_id": "turn-2",
        "model": "account-model",
        "effort": "medium",
        "account_hash": "account-hash",
    }


async def test_prefix_fork_excludes_anchor_and_later_turns(session_factory, tmp_path: Path):
    store = DbSessionStore(session_factory)
    source, branch, empty_branch = str(uuid4()), str(uuid4()), str(uuid4())
    history = CodexHistory(store, tmp_path, source)
    for turn in (1, 2):
        marker = _marker(source, f"turn-{turn}")
        await history.append({"type": "user", "uuid": f"user-{turn}", "content": f"request {turn}"}, marker)
        await history.append({"type": "assistant", "uuid": f"answer-{turn}", "content": f"answer {turn}"}, marker)
        await history.append(
            {"type": "result", "uuid": f"done-{turn}"}, _marker(source, f"turn-{turn}", completed=True)
        )

    copied = await copy_session_prefix(
        store,
        project_key=make_project_key(tmp_path),
        session_id=source,
        anchor_uuid="user-2",
        new_session_id=branch,
    )
    branch_history = CodexHistory(store, tmp_path, branch)
    entries = await branch_history.load()
    assert copied.entries_copied == 3
    assert [entry["uuid"] for entry in entries] == ["user-1", "answer-1", "done-1"]
    assert all(entry["sessionId"] == branch and entry["codex"]["session_id"] == source for entry in entries)
    plan = restoration(entries, branch)
    assert plan["thread_id"] == "codex-thread"
    assert plan["fork"] is True
    assert plan["last_turn_id"] == "turn-1"
    visible = await get_session_messages_from_store(store, branch, directory=str(tmp_path))
    assert [message.uuid for message in visible] == ["user-1", "answer-1"]
    assert await session_runtime_overrides(store, tmp_path, branch) == {
        "ARCREEL_AGENT_BACKEND": "codex",
        "ARCREEL_CODEX_AGENT_MODEL": "account-model",
        "ARCREEL_CODEX_AGENT_EFFORT": "medium",
    }

    new_marker = {**_marker(source, "turn-new"), "thread_id": "forked-thread"}
    await branch_history.append({"type": "user", "uuid": "edited", "content": "Edited request"}, new_marker)
    continued = await branch_history.load()
    assert continued[-1]["parentUuid"] == "answer-1"
    assert continued[-1]["codex"]["session_id"] == branch
    assert restoration(continued, branch)["thread_id"] == "forked-thread"
    assert restoration(continued, branch)["fork"] is False

    copied_empty = await copy_session_prefix(
        store,
        project_key=make_project_key(tmp_path),
        session_id=source,
        anchor_uuid="user-1",
        new_session_id=empty_branch,
    )
    assert copied_empty.entries_copied == 0
    assert await CodexHistory(store, tmp_path, empty_branch).load() == []
    assert await session_runtime_overrides(store, tmp_path, empty_branch) == {}


@pytest.mark.parametrize("previous_completed", [False, True])
async def test_incomplete_prefix_only_forks_at_an_earlier_completed_turn(
    session_factory, tmp_path: Path, previous_completed: bool
):
    store = DbSessionStore(session_factory)
    source, branch = str(uuid4()), str(uuid4())
    history = CodexHistory(store, tmp_path, source)
    if previous_completed:
        await history.append({"type": "user", "uuid": "previous-user", "content": "before"}, _marker(source, "turn-0"))
        await history.append(
            {"type": "assistant", "uuid": "previous-answer", "content": "done"}, _marker(source, "turn-0")
        )
        await history.append({"type": "result", "uuid": "previous-result"}, _marker(source, "turn-0", completed=True))
    for uuid, kind in (("first", "user"), ("partial", "assistant"), ("anchor", "user")):
        await history.append({"type": kind, "uuid": uuid, "content": uuid}, _marker(source))
    await copy_session_prefix(
        store,
        project_key=make_project_key(tmp_path),
        session_id=source,
        anchor_uuid="anchor",
        new_session_id=branch,
    )

    entries = await CodexHistory(store, tmp_path, branch).load()
    plan = restoration(entries, branch)
    assert plan["thread_id"] == ("codex-thread" if previous_completed else None)
    assert plan["last_turn_id"] == ("turn-0" if previous_completed else None)
    assert plan["fork"] is previous_completed
    assert plan["model"] == "account-model"
    assert restoration(await history.load(), source)["thread_id"] == "codex-thread"


async def test_runtime_selection_distinguishes_claude_messages_from_empty_metadata(session_factory, tmp_path: Path):
    store = DbSessionStore(session_factory)
    session_id = str(uuid4())
    key = {"project_key": make_project_key(tmp_path), "session_id": session_id}
    await store.append(key, [{"type": "system", "uuid": "init", "subtype": "init"}])
    assert await session_runtime_overrides(store, tmp_path, session_id) == {}
    await store.append(key, [{"type": "user", "uuid": "legacy", "message": {"role": "user", "content": "hello"}}])
    assert await session_runtime_overrides(store, tmp_path, session_id) == {"ARCREEL_AGENT_BACKEND": "claude"}
    assert await session_runtime_overrides(None, tmp_path, session_id) == {}

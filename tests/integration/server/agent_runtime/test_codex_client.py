"""Codex SDK transport boundary with real ArcReel tools and transcript storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from claude_agent_sdk import PermissionResultAllow, get_session_messages_from_store
from openai_codex.models import UnknownNotification

from lib.agent_session_store import make_project_key
from lib.agent_session_store.prefix_fork import copy_session_prefix
from lib.agent_session_store.store import DbSessionStore
from lib.codex_runtime import CodexError
from lib.project_migration_failure import record_migration_failure
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.codex_client import CodexAgentClient
from server.agent_runtime.codex_history import CodexHistory
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

ACCOUNT_EMAIL = "agent@example.test"
ACCOUNT_HASH = hashlib.sha256(ACCOUNT_EMAIL.encode()).hexdigest()


class FakeSDK:
    """Synchronous SDK surface; notifications cross the real worker-thread boundary."""

    def __init__(self):
        self.config: Any = None
        self.approval_handler: Any = None
        self.operations: list[tuple[str, Any]] = []
        self.turns: dict[str, Queue] = {}
        self.turn_id: str | None = None
        self.thread_id = "new-thread"
        self.closed = False
        self.account = {"type": "chatgpt", "email": ACCOUNT_EMAIL}
        self.shared_config = {"mcp_servers": {"unrelated": {"enabled": True}}}

    def __call__(self, *, config, approval_handler):
        self.config = config
        self.approval_handler = approval_handler
        return self

    def start(self):
        self.operations.append(("start", None))

    def initialize(self):
        self.operations.append(("initialize", None))

    def account_read(self):
        return {"account": self.account}

    def request(self, method, params, *, response_model):
        if method != "config/read":
            raise NotImplementedError(method)
        self.operations.append((method, params))
        return SimpleNamespace(config=SimpleNamespace(model_extra=self.shared_config))

    def thread_start(self, params):
        self.operations.append(("thread_start", params))
        return {"thread": {"id": self.thread_id}}

    def thread_resume(self, thread_id, params):
        self.thread_id = thread_id
        self.operations.append(("thread_resume", {"thread_id": thread_id, **params}))
        return {"thread": {"id": thread_id}}

    def thread_fork(self, thread_id, params):
        self.thread_id = "forked-thread"
        self.operations.append(("thread_fork", {"thread_id": thread_id, **params}))
        return {"thread": {"id": self.thread_id}}

    def turn_start(self, thread_id, inputs, params):
        turn_id = f"turn-{len(self.turns) + 1}"
        self.turns[turn_id] = Queue()
        self.turn_id = turn_id
        self.operations.append(("turn_start", {"thread_id": thread_id, "input": inputs, **params}))
        return {"turn": {"id": turn_id}}

    def push(self, method: str, payload: Any, *, turn_id: str | None = None):
        self.turns[turn_id or self.turn_id].put(SimpleNamespace(method=method, payload=payload))

    def fail(self, error: Exception):
        self.turns[self.turn_id].put(error)

    def next_turn_notification(self, turn_id):
        item = self.turns[turn_id].get()
        if isinstance(item, BaseException):
            raise item
        return item

    def unregister_turn_notifications(self, turn_id):
        self.operations.append(("unregister", turn_id))

    def turn_interrupt(self, thread_id, turn_id):
        self.operations.append(("turn_interrupt", {"thread_id": thread_id, "turn_id": turn_id}))
        self.push("turn/completed", {"turn": {"id": turn_id, "status": "interrupted"}}, turn_id=turn_id)

    def close(self):
        self.closed = True
        for notifications in self.turns.values():
            notifications.put(RuntimeError("SDK closed"))

    def complete(self, text: str = "Done"):
        item_id = f"answer-{self.turn_id}"
        self.push("item/agentMessage/delta", {"itemId": item_id, "delta": text})
        self.push("item/completed", {"item": {"id": item_id, "type": "agentMessage", "text": text}})
        self.push("thread/tokenUsage/updated", {"tokenUsage": {"last": {"inputTokens": 10, "outputTokens": 4}}})
        self.push("turn/completed", {"turn": {"id": self.turn_id, "status": "completed"}})

    def call_tool(self, name: str, arguments: dict, *, thread_id: str | None = None, call_id: str | None = None):
        return self.approval_handler(
            "item/tool/call",
            {
                "threadId": thread_id or self.thread_id,
                "turnId": self.turn_id,
                "callId": call_id or str(uuid4()),
                "tool": name,
                "arguments": arguments,
            },
        )


@dataclass
class ClientEnvironment:
    store: DbSessionStore
    root: Path
    cwd: Path
    state: dict

    def client(self, sdk: FakeSDK, *, session_id: str | None = None, resume: str | None = None, can_use_tool=None):
        options = SimpleNamespace(
            cwd=str(self.cwd),
            env={"ARCREEL_CODEX_AGENT_MODEL": "account-model", "ARCREEL_CODEX_AGENT_EFFORT": "low"},
            resume=resume,
            session_id=session_id or str(uuid4()),
            session_store=self.store,
            max_turns=64,
            can_use_tool=can_use_tool,
        )
        return CodexAgentClient(
            options=options,
            project_name="demo",
            projects_root=self.root,
            sdk_factory=sdk,
            state_reader=lambda: self.state,
        )


@pytest.fixture
async def client_environment(session_factory, tmp_path: Path):
    root = tmp_path / "projects"
    cwd = root / "demo"
    cwd.mkdir(parents=True)
    (cwd / "project.json").write_text(
        json.dumps(
            {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION, "title": "Fixture project", "content_mode": "narration"}
        ),
        encoding="utf-8",
    )
    return ClientEnvironment(
        store=DbSessionStore(session_factory),
        root=root,
        cwd=cwd,
        state={
            "enabled": True,
            "connected": True,
            "account_hash": ACCOUNT_HASH,
            "models": [{"id": "account-model", "model": "account-model", "isDefault": True}],
        },
    )


async def _receive(client: CodexAgentClient) -> list[dict]:
    async with asyncio.timeout(10):
        return [message async for message in client.receive_response()]


def _marker(session_id: str, turn_id: str = "old-turn", *, completed: bool = False) -> dict:
    return {
        "session_id": session_id,
        "thread_id": "saved-thread",
        "turn_id": turn_id,
        "turn_completed": completed,
        "model": "account-model",
        "effort": "medium",
        "account_hash": ACCOUNT_HASH,
    }


async def test_start_stream_result_and_multiple_turns_use_one_durable_session(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    async with client:
        await client.query("First request")
        sdk.push("future/extension", UnknownNotification(params={"extra": "ignored"}))
        sdk.complete("First answer")
        first = await _receive(client)
        await client.query("Second request")
        sdk.complete("Second answer")
        second = await _receive(client)

    assert sdk.closed
    assert first[0]["type"] == "system"
    assert first[0]["subtype"] == "init"
    assert first[0]["session_id"] == client.session_id
    stream = [message["event"] for message in first if message["type"] == "stream_event"]
    assert [event["type"] for event in stream[:3]] == ["message_start", "content_block_start", "content_block_delta"]
    assert stream[2]["delta"] == {"type": "text_delta", "text": "First answer"}
    assert next(message for message in first if message["type"] == "assistant")["model"] == "account-model"
    for messages in (first, second):
        result = messages[-1]
        assert (result["type"], result["subtype"], result["session_status"]) == ("result", "success", "completed")
        assert (result["provider"], result["model"], result["total_cost_usd"]) == ("codex", "account-model", 0.0)
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 4
    starts = [params for operation, params in sdk.operations if operation == "thread_start"]
    assert len(starts) == 1
    assert starts[0]["approvalPolicy"] == "never"
    assert starts[0]["cwd"] == str(client_environment.cwd)
    assert starts[0]["modelProvider"] == "arcreel_openai_http"
    configuration = tomllib.loads("\n".join(sdk.config.config_overrides))
    assert configuration["forced_login_method"] == "chatgpt"
    assert configuration["model_provider"] == "arcreel_openai_http"
    http_provider = configuration["model_providers"]["arcreel_openai_http"]
    assert http_provider["requires_openai_auth"] is True
    assert http_provider["supports_websockets"] is False
    assert http_provider["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert http_provider["wire_api"] == "responses"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        assert sdk.config.env[key] == ""
    assert starts[0]["config"]["mcp_servers"]["unrelated"]["enabled"] is False
    assert sdk.shared_config == {"mcp_servers": {"unrelated": {"enabled": True}}}
    assert next(params for operation, params in sdk.operations if operation == "config/read") == {
        "cwd": str(client_environment.cwd),
        "includeLayers": False,
    }
    assert {tool["name"] for tool in starts[0]["dynamicTools"]} == {
        *(f"arcreel_{name}" for name in ARCREEL_MCP_TOOL_IDS),
        "arcreel_list_agent_memory",
        "arcreel_read_agent_memory",
        "arcreel_write_agent_memory",
        "AskUserQuestion",
    }
    turns = [params for operation, params in sdk.operations if operation == "turn_start"]
    assert [turn["input"] for turn in turns] == [
        [{"type": "text", "text": "First request"}],
        [{"type": "text", "text": "Second request"}],
    ]
    assert all(turn["thread_id"] == "new-thread" and turn["effort"] == "low" for turn in turns)
    entries = await client.history.load()
    assert not any(entry["type"] == "stream_event" for entry in entries)
    assert entries[-1]["codex"]["turn_completed"] is True
    visible = await get_session_messages_from_store(
        client_environment.store, client.session_id, directory=str(client.cwd)
    )
    assert [message.message["content"] for message in visible] == [
        [{"type": "text", "text": "First request"}],
        [{"type": "text", "text": "First answer"}],
        [{"type": "text", "text": "Second request"}],
        [{"type": "text", "text": "Second answer"}],
    ]


async def test_real_project_tool_and_refusals_cross_the_sdk_callback(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    async with client:
        await client.query("Read the project")
        name = "arcreel_get_project_content"
        sdk.push("item/started", {"item": {"type": "dynamicToolCall", "id": "tool-1", "tool": name, "arguments": {}}})
        response = await asyncio.to_thread(sdk.call_tool, name, {})
        sdk.push("item/completed", {"item": {"type": "dynamicToolCall", "id": "tool-1", "tool": name, **response}})
        unknown = await asyncio.to_thread(sdk.call_tool, "Bash", {"command": "echo forbidden"})
        reserved_name = await asyncio.to_thread(sdk.call_tool, "mcp__arcreel__get_project_content", {})
        wrong = await asyncio.to_thread(sdk.call_tool, name, {}, thread_id="another-thread")
        record_migration_failure(
            client.cwd, RuntimeError("repair needed"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
        )
        refused = await asyncio.to_thread(sdk.call_tool, "arcreel_confirm_script_review", {"episode": 1})
        sdk.complete()
        messages = await _receive(client)

    assert response["success"] is True
    project = json.loads(response["contentItems"][0]["text"])["project_content"]
    assert project["project"]["title"] == "Fixture project"
    assert project["revision"]
    assert unknown["success"] is wrong["success"] is refused["success"] is False
    assert reserved_name["success"] is False
    assert "repair needed" in refused["contentItems"][0]["text"]
    blocks = [block for message in messages for block in message.get("content", [])]
    call = next(block for block in blocks if block["type"] == "tool_use")
    result = next(block for block in blocks if block["type"] == "tool_result")
    assert call["name"] == "mcp__arcreel__get_project_content"
    assert call["id"] == result["tool_use_id"] == "tool-1"
    assert result["is_error"] is False
    assert "Fixture project" in result["content"][0]["text"]


async def test_cold_resume_uses_saved_runtime_identity(client_environment):
    session_id = str(uuid4())
    history = CodexHistory(client_environment.store, client_environment.cwd, session_id)
    await history.append({"type": "user", "uuid": "old-user", "content": "Before restart"}, _marker(session_id))
    await history.append({"type": "assistant", "uuid": "old-answer", "content": "Saved"}, _marker(session_id))
    await history.append({"type": "result"}, _marker(session_id, completed=True))
    sdk = FakeSDK()
    client = client_environment.client(sdk, resume=session_id)
    async with client:
        await client.query("After restart")
        sdk.complete("Resumed")
        messages = await _receive(client)

    resume = next(params for operation, params in sdk.operations if operation == "thread_resume")
    assert resume["thread_id"] == "saved-thread"
    assert not any(operation in {"thread_start", "thread_fork"} for operation, _ in sdk.operations)
    assert client.session_id == messages[-1]["session_id"] == session_id
    assert messages[-1]["subtype"] == "success"
    entries = await client.history.load()
    assert entries[-1]["codex"]["effort"] == "medium"
    assert entries[-1]["codex"]["thread_id"] == "saved-thread"


async def test_invalid_arguments_report_fields_and_allow_a_corrected_call(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    async with client:
        await client.query("Inspect the script")
        rejected = await asyncio.to_thread(sdk.call_tool, "arcreel_confirm_script_review", {"episode": "private-value"})
        problem = json.loads(rejected["contentItems"][0]["text"])["problem"]
        accepted = await asyncio.to_thread(sdk.call_tool, "arcreel_get_project_content", {})
        sdk.complete()
        messages = await _receive(client)

    assert rejected["success"] is False
    assert problem["code"] == "invalid_tool_arguments"
    assert problem["params"]["field"] == ["episode"]
    assert problem["params"]["rule"] == "type"
    assert "private-value" not in rejected["contentItems"][0]["text"]
    assert accepted["success"] is True
    assert messages[-1]["session_status"] == "completed"


async def test_repeated_call_id_does_not_repeat_a_project_write(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    arguments = {"filename": "story.txt", "content": "Original source", "on_conflict": "rename"}
    async with client:
        await client.query("Import the source")
        first = await asyncio.to_thread(sdk.call_tool, "arcreel_upload_source", arguments, call_id="upload-once")
        repeated = await asyncio.to_thread(sdk.call_tool, "arcreel_upload_source", arguments, call_id="upload-once")
        conflict = await asyncio.to_thread(
            sdk.call_tool, "arcreel_upload_source", {**arguments, "content": "Changed source"}, call_id="upload-once"
        )
        stale = await asyncio.to_thread(
            sdk.approval_handler,
            "item/tool/call",
            {
                "threadId": sdk.thread_id,
                "turnId": "previous-turn",
                "callId": "stale-upload",
                "tool": "arcreel_upload_source",
                "arguments": arguments,
            },
        )
        sdk.complete()
        await _receive(client)

    assert first["success"] is True
    assert repeated == first
    assert json.loads(conflict["contentItems"][0]["text"])["problem"]["code"] == "call_id_conflict"
    assert json.loads(stale["contentItems"][0]["text"])["problem"]["code"] == "stale_turn"
    assert [path.read_text(encoding="utf-8") for path in (client.cwd / "source").glob("*.txt")] == ["Original source"]


@pytest.mark.parametrize("completed_prefix", [True, False])
async def test_branch_uses_only_the_copied_completed_turn(client_environment, completed_prefix: bool):
    source, branch = str(uuid4()), str(uuid4())
    history = CodexHistory(client_environment.store, client_environment.cwd, source)
    await history.append({"type": "user", "uuid": "before", "content": "Original"}, _marker(source))
    await history.append({"type": "assistant", "uuid": "answer", "content": "Original answer"}, _marker(source))
    if completed_prefix:
        await history.append({"type": "result"}, _marker(source, completed=True))
    await history.append(
        {"type": "user", "uuid": "anchor", "content": "Must be excluded"}, _marker(source, "later-turn")
    )
    await history.append({"type": "result"}, _marker(source, "later-turn", completed=True))
    await copy_session_prefix(
        client_environment.store,
        project_key=make_project_key(client_environment.cwd),
        session_id=source,
        anchor_uuid="anchor",
        new_session_id=branch,
    )
    sdk = FakeSDK()
    client = client_environment.client(sdk, resume=branch)
    async with client:
        await client.query("Edited request")
        sdk.complete("Branch answer")
        messages = await _receive(client)

    operation = "thread_fork" if completed_prefix else "thread_start"
    params = next(params for name, params in sdk.operations if name == operation)
    if completed_prefix:
        assert params["thread_id"] == "saved-thread"
        assert params["lastTurnId"] == "old-turn"
    else:
        assert not any(name in {"thread_resume", "thread_fork"} for name, _ in sdk.operations)
    assert messages[-1]["session_id"] == branch
    entries = await client.history.load()
    assert all(entry["uuid"] != "anchor" for entry in entries)
    assert entries[-1]["codex"]["session_id"] == branch
    assert entries[-1]["codex"]["thread_id"] == ("forked-thread" if completed_prefix else "new-thread")


def _questions() -> dict:
    return {
        "questions": [
            {
                "question": "Which tone?",
                "header": "Tone",
                "multiSelect": False,
                "options": [
                    {"label": "Warm", "description": "A friendly tone"},
                    {"label": "Cool", "description": "A restrained tone"},
                ],
            }
        ]
    }


async def test_ask_user_question_waits_for_the_application_answer(client_environment):
    asked = asyncio.Event()
    answer = asyncio.get_running_loop().create_future()

    async def can_use_tool(name, arguments, _context):
        asked.set()
        return PermissionResultAllow(updated_input={**arguments, "answers": await answer})

    sdk = FakeSDK()
    client = client_environment.client(sdk, can_use_tool=can_use_tool)
    async with client:
        await client.query("Help me choose")
        callback = asyncio.create_task(asyncio.to_thread(sdk.call_tool, "AskUserQuestion", _questions()))
        async with asyncio.timeout(10):
            await asked.wait()
            assert not callback.done()
            answer.set_result({"Which tone?": "Warm"})
            response = await callback
        sdk.complete("Using a warm tone")
        messages = await _receive(client)

    assert response["success"] is True
    assert json.loads(response["contentItems"][0]["text"]) == {"Which tone?": "Warm"}
    assert messages[-1]["subtype"] == "success"


async def test_interrupt_cancels_a_pending_question_and_emits_terminal_result(client_environment):
    asked = asyncio.Event()
    cancelled = asyncio.Event()

    async def can_use_tool(_name, _arguments, _context):
        asked.set()
        try:
            await asyncio.get_running_loop().create_future()
        finally:
            cancelled.set()

    sdk = FakeSDK()
    client = client_environment.client(sdk, can_use_tool=can_use_tool)
    async with client:
        await client.query("Ask before continuing")
        callback = asyncio.create_task(asyncio.to_thread(sdk.call_tool, "AskUserQuestion", _questions()))
        async with asyncio.timeout(10):
            await asked.wait()
            await client.interrupt()
            response = await callback
            await cancelled.wait()
        messages = await _receive(client)

    assert response["success"] is False
    assert "interrupted" in response["contentItems"][0]["text"].lower()
    assert messages[-1]["type"] == "result"
    assert messages[-1]["session_status"] == "interrupted"
    assert messages[-1]["is_error"] is False
    assert sdk.closed


async def test_account_mismatch_closes_sdk_before_starting_a_thread(client_environment):
    sdk = FakeSDK()
    sdk.account = {"type": "chatgpt", "email": "another@example.test"}
    client = client_environment.client(sdk)
    with pytest.raises(CodexError) as exc:
        async with client:
            pytest.fail("Mismatched account must not enter a session")
    assert exc.value.code == "codex_login_required"
    assert sdk.closed
    assert not any(operation.startswith("thread_") for operation, _ in sdk.operations)
    assert await client.history.load() == []


async def test_sdk_timeout_closes_the_transport_and_refuses_further_queries(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    async with client:
        await client.query("Wait for a response")
        sdk.push("item/agentMessage/delta", UnknownNotification(params={"itemId": "partial", "delta": "Working"}))
        sdk.fail(TimeoutError("SDK notification timed out"))
        messages = await _receive(client)
        assert sdk.closed
        with pytest.raises(CodexError) as exc:
            await client.query("Must not reuse a timed out transport")

    assert exc.value.code == "codex_request_failed"
    stream = [message["event"] for message in messages if message["type"] == "stream_event"]
    assert stream[-1]["delta"]["text"] == "Working"
    result = messages[-1]
    assert (result["type"], result["session_status"], result["is_error"]) == ("result", "error", True)
    assert result["result"]
    entries = await client.history.load()
    assert entries[-1]["codex"]["turn_completed"] is False
    assert len([operation for operation, _ in sdk.operations if operation == "turn_start"]) == 1


async def test_usage_accumulates_requests_once_and_resets_its_baseline_each_turn(client_environment):
    sdk = FakeSDK()
    client = client_environment.client(sdk)
    async with client:
        await client.query("Use more than one request")
        for total_input, total_output, last_input, last_output in (
            (130, 15, 10, 4),
            (150, 20, 20, 5),
            (150, 20, 20, 5),
        ):
            sdk.push(
                "thread/tokenUsage/updated",
                {
                    "tokenUsage": {
                        "total": {"inputTokens": total_input, "outputTokens": total_output},
                        "last": {"inputTokens": last_input, "outputTokens": last_output},
                    }
                },
            )
        sdk.push("turn/completed", {"turn": {"id": sdk.turn_id, "status": "completed"}})
        first = await _receive(client)
        await client.query("Start a separate turn")
        sdk.push(
            "thread/tokenUsage/updated",
            {
                "tokenUsage": {
                    "total": {"inputTokens": 160, "outputTokens": 25},
                    "last": {"inputTokens": 10, "outputTokens": 5},
                }
            },
        )
        sdk.push("turn/completed", {"turn": {"id": sdk.turn_id, "status": "completed"}})
        second = await _receive(client)

    assert first[-1]["usage"] == {"input_tokens": 30, "output_tokens": 9}
    assert second[-1]["usage"] == {"input_tokens": 10, "output_tokens": 5}
    entries = await client.history.load()
    assert [entry["usage"] for entry in entries if entry["type"] == "result"] == [
        first[-1]["usage"],
        second[-1]["usage"],
    ]

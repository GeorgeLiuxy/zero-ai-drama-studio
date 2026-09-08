"""Conversation engine selection, cold recovery and subscription ledger integration."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore
from lib.db.models.api_call import ApiCall
from lib.i18n import _ as translate
from server.agent_runtime import session_manager as manager_module
from server.agent_runtime.codex_history import CodexHistory
from server.agent_runtime.event_log import EventLogStore
from server.agent_runtime.models import LiveMessage
from server.agent_runtime.session_manager import AgentStartupError, SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import FakeSDKClient


class _BoundCodexClient(FakeSDKClient):
    def __init__(self, *, options, project_name, projects_root, user_id):
        super().__init__(messages=[{"type": "system", "subtype": "init", "session_id": "codex-session"}])
        self.options = options
        self.project_name = project_name
        self.projects_root = projects_root
        self.user_id = user_id


@pytest.fixture
async def codex_session_manager(tmp_path, session_factory):
    project = tmp_path / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "project.json").write_text('{"title": "Demo"}', encoding="utf-8")
    environment = {
        "ARCREEL_AGENT_BACKEND": "codex",
        "ARCREEL_CODEX_AGENT_MODEL": "gpt-configured",
        "ARCREEL_CODEX_AGENT_EFFORT": "low",
    }
    clients = []

    async def provider_env():
        return dict(environment)

    def client_factory(**kwargs):
        client = _BoundCodexClient(**kwargs)
        clients.append(client)
        return client

    manager = SessionManager(
        project_root=tmp_path,
        meta_store=SessionMetaStore(session_factory=session_factory),
        event_log_store=EventLogStore(session_factory=session_factory),
        session_factory=session_factory,
        provider_env_loader=provider_env,
        codex_client_factory=client_factory,
        sdk_id_timeout=2,
    )
    try:
        yield manager, environment, clients
    finally:
        await manager.shutdown_gracefully()


@pytest.mark.parametrize("model_fields", [{"model": "gpt-actual"}, {"modelUsage": {"gpt-actual": {}}}])
async def test_new_codex_conversation_records_actual_model_and_zero_subscription_charge(
    codex_session_manager, session_factory, model_fields
):
    manager, _, clients = codex_session_manager
    session_id = await manager.send_new_session("demo", "Write a scene")
    assert session_id == "codex-session"
    assert manager.sessions[session_id].assistant_provider == "codex"
    assert manager.sessions[session_id].assistant_model == "gpt-configured"
    client = clients[0]
    assert client.project_name == "demo"
    assert client.projects_root == manager.projects_root
    async with manager.stream_messages(session_id) as stream, asyncio.timeout(3):
        client.push_message(
            {
                "type": "result",
                "session_id": session_id,
                "provider": "codex",
                **model_fields,
                "usage": {"input_tokens": 32, "output_tokens": 9},
                "total_cost_usd": 99,
            }
        )
        async for event in stream:
            if isinstance(event, LiveMessage) and event.message["type"] == "log_turn_complete":
                break
    await manager.close_session(session_id)
    async with session_factory() as session:
        row = (await session.execute(select(ApiCall))).scalar_one()
        assert row.model == "gpt-actual"
        assert row.provider == "codex"
        assert row.cost_amount == 0
        assert row.currency == "USD"
        assert (row.input_tokens, row.output_tokens, row.usage_tokens) == (32, 9, 41)


@pytest.mark.parametrize("empty_prefix", [False, True])
async def test_cold_codex_session_keeps_original_engine_model_and_effort(
    codex_session_manager, session_factory, empty_prefix
):
    manager, environment, clients = codex_session_manager
    environment.update(ARCREEL_AGENT_BACKEND="claude", ARCREEL_CODEX_AGENT_MODEL="other-default")
    parent_id = "parent-codex-session"
    child_id = "child-codex-session"
    stored_id = parent_id if empty_prefix else child_id
    await manager.meta_store.create("demo", parent_id)
    await manager.meta_store.create("demo", child_id, fork_parent_session_id=parent_id)
    history = CodexHistory(DbSessionStore(session_factory), manager.projects_root / "demo", stored_id)
    await history.append(
        {"type": "system", "subtype": "init", "uuid": "runtime-marker"},
        {"thread_id": "original-thread", "model": "gpt-original", "effort": "high"},
    )
    managed = await manager.get_or_connect(child_id, resumable=not empty_prefix)
    assert managed.assistant_provider == "codex"
    assert managed.assistant_model == "gpt-original"
    assert clients[0].options.env["ARCREEL_CODEX_AGENT_EFFORT"] == "high"
    assert clients[0].options.env["ARCREEL_AGENT_BACKEND"] == "codex"
    assert clients[0].project_name == "demo"


@pytest.mark.parametrize(("store_mode", "empty_prefix"), [("off", False), ("db", False), ("db", True)])
async def test_filesystem_claude_history_keeps_its_engine(
    codex_session_manager, tmp_path, monkeypatch, store_mode, empty_prefix
):
    manager, _, clients = codex_session_manager
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", store_mode)
    config_dir = tmp_path / "sdk-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    parent_id = str(uuid4())
    session_id = str(uuid4()) if empty_prefix else parent_id
    await manager.meta_store.create("demo", parent_id)
    if empty_prefix:
        await manager.meta_store.create("demo", session_id, fork_parent_session_id=parent_id)
    project = manager.projects_root / "demo"
    transcript_dir = config_dir / "projects" / make_project_key(project)
    transcript_dir.mkdir(parents=True)
    transcript = {
        "type": "user",
        "uuid": str(uuid4()),
        "parentUuid": None,
        "sessionId": parent_id,
        "cwd": str(project),
        "message": {"role": "user", "content": "Existing Claude scene"},
    }
    (transcript_dir / f"{parent_id}.jsonl").write_text(json.dumps(transcript) + "\n", encoding="utf-8")
    legacy_client = FakeSDKClient()
    monkeypatch.setattr(manager_module, "ClaudeSDKClient", lambda options: legacy_client)

    managed = await manager.get_or_connect(session_id, resumable=not empty_prefix)
    assert managed.assistant_provider == "anthropic"
    assert managed.assistant_model != "gpt-configured"
    assert clients == []


async def test_unidentified_old_session_cannot_start_a_new_codex_thread(codex_session_manager):
    manager, _, clients = codex_session_manager
    session_id = str(uuid4())
    await manager.meta_store.create("demo", session_id)
    with pytest.raises(AgentStartupError) as error:
        await manager.get_or_connect(session_id)
    assert error.value.message == translate("codex_history_unavailable")
    assert session_id not in manager.sessions
    assert clients == []

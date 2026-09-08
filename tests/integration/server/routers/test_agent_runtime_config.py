"""Agent runtime selection uses persisted settings and the cached subscription catalog."""

from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from lib.config.service import ConfigService, build_anthropic_env_dict
from lib.db import get_async_session
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.i18n import _
from server.dependencies import get_config_service
from server.error_handlers import register_error_handlers
from server.routers import agent_config, system_config


@pytest.fixture
async def runtime_config_api(session_factory):
    state = {
        "enabled": True,
        "connected": True,
        "models": [
            {
                "id": "catalog-entry",
                "model": "gpt-test-codex",
                "displayName": "Test Codex",
                "isDefault": True,
                "defaultReasoningEffort": "high",
                "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}],
            }
        ],
    }
    app = FastAPI()
    register_error_handlers(app)

    async def session_dependency():
        async with session_factory() as session:
            yield session

    def config_dependency(session=Depends(get_async_session)):
        return ConfigService(session, codex_state_reader=lambda: deepcopy(state))

    app.dependency_overrides[get_async_session] = session_dependency
    app.dependency_overrides[get_config_service] = config_dependency
    app.include_router(agent_config.router, prefix="/api/v1")
    app.include_router(system_config.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, state


async def test_subscription_selection_persists_into_agent_options_without_api_credentials(
    runtime_config_api, session_factory
):
    client, state = runtime_config_api
    initial = (await client.get("/api/v1/agent/runtime")).json()
    assert initial["runtime"] == "claude"
    assert initial["ready"] is False
    assert initial["model"] == "gpt-test-codex"

    enabled = await client.patch("/api/v1/agent/runtime", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json() == {
        "runtime": "codex",
        "enabled": True,
        "ready": True,
        "model": "gpt-test-codex",
        "reasoning_effort": "low",
        "models": [
            {"id": "gpt-test-codex", "display_name": "Test Codex", "supported_reasoning_efforts": ["low", "high"]}
        ],
        "error_code": None,
    }
    async with session_factory() as session:
        env = await build_anthropic_env_dict(session, codex_state_reader=lambda: state)
        assert env["ARCREEL_AGENT_BACKEND"] == "codex"
        assert env["ARCREEL_CODEX_AGENT_MODEL"] == "gpt-test-codex"
        assert env["ARCREEL_CODEX_AGENT_EFFORT"] == "low"
        assert env["ANTHROPIC_API_KEY"] == ""
    system = (await client.get("/api/v1/system/config")).json()["settings"]
    assert system["agent_ready"] is True
    assert system["anthropic_api_key"]["is_set"] is False

    state["connected"] = False
    disconnected = (await client.get("/api/v1/agent/runtime")).json()
    assert disconnected["enabled"] is True
    assert disconnected["ready"] is False
    assert disconnected["error_code"] == "codex_login_required"
    disabled = await client.patch("/api/v1/agent/runtime", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["runtime"] == "claude"
    assert disabled.json()["ready"] is False
    assert state["enabled"] is True


@pytest.mark.parametrize(
    ("changes", "state_changes", "expected_code"),
    [
        ({"enabled": True, "model": "invented-model"}, {}, "codex_models_unavailable"),
        ({"enabled": True, "reasoning_effort": "ultra"}, {}, "codex_reasoning_effort_unsupported"),
        ({"enabled": True}, {"connected": False}, "codex_login_required"),
        ({"enabled": True}, {"models": []}, "codex_models_unavailable"),
    ],
)
async def test_invalid_subscription_selection_is_atomic(
    runtime_config_api, session_factory, changes, state_changes, expected_code
):
    client, state = runtime_config_api
    state.update(state_changes)
    response = await client.patch("/api/v1/agent/runtime", json=changes)
    assert response.status_code == 422
    assert response.json()["detail"] == _(expected_code)
    async with session_factory() as session:
        assert await ConfigService(session).get_all_settings() == {}


async def test_claude_ready_uses_active_credentials_and_preserves_subscription_preferences(
    runtime_config_api, session_factory
):
    client, state = runtime_config_api
    async with session_factory() as session:
        repo = AgentCredentialRepository(session)
        credential = await repo.create(
            preset_id="__custom__", display_name="Test", base_url="https://example.com", api_key="test-key"
        )
        await repo.set_active(credential.id)
        await session.commit()
    configured = await client.patch(
        "/api/v1/agent/runtime", json={"model": "gpt-test-codex", "reasoning_effort": "high"}
    )
    assert configured.status_code == 200
    assert configured.json()["runtime"] == "claude"
    assert configured.json()["ready"] is True
    async with session_factory() as session:
        env = await build_anthropic_env_dict(session, codex_state_reader=lambda: state)
        assert env["ARCREEL_AGENT_BACKEND"] == "claude"
        assert env["ARCREEL_CODEX_AGENT_EFFORT"] == "high"
        assert env["ANTHROPIC_API_KEY"] == "test-key"

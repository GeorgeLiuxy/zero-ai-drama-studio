"""Codex Python SDK client adapted to ArcReel's session and event contracts."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterable, AsyncIterator
from concurrent.futures import CancelledError as FutureCancelledError
from pathlib import Path
from typing import Any
from uuid import uuid4

import jsonschema
from openai_codex import CodexConfig
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import ConfigReadResponse
from openai_codex.models import UnknownNotification

from lib.codex_runtime import CodexError, read_state, select_model
from lib.db.base import DEFAULT_USER_ID
from lib.i18n import _
from lib.logging_utils import redact_diagnostic_text
from lib.project_manager import ProjectManager
from server.agent_runtime.codex_history import CodexHistory, restoration
from server.agent_runtime.codex_instructions import build_agent_instructions
from server.agent_runtime.codex_support_tools import build_codex_support_tools, load_memory_context
from server.agent_runtime.message_serialization import utc_now_iso
from server.agent_runtime.sdk_tools import build_arcreel_tools

logger = logging.getLogger(__name__)
_HTTP_PROVIDER = "arcreel_openai_http"

_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "header": {"type": "string"},
                    "multiSelect": {"type": "boolean"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                            "required": ["label", "description"],
                        },
                    },
                },
                "required": ["question", "header", "options", "multiSelect"],
            },
        },
    },
    "required": ["questions"],
}


def _tool_problem(code: str, *, locale: str, **params: Any) -> dict[str, Any]:
    return {
        "success": False,
        "contentItems": [
            {
                "type": "inputText",
                "text": json.dumps(
                    {"problem": {"code": code, "detail": _("codex_agent_" + code, locale=locale), "params": params}},
                    ensure_ascii=False,
                ),
            }
        ],
    }


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, UnknownNotification):
        return dict(value.params)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return dict(value)


class CodexAgentClient:
    """One durable Codex thread, with explicit project-bound tool dispatch."""

    def __init__(
        self,
        *,
        options: Any,
        project_name: str,
        projects_root: Path,
        user_id: str = DEFAULT_USER_ID,
        sdk_factory: Any = CodexClient,
        state_reader: Any = read_state,
    ) -> None:
        self.options = options
        self.project_name = project_name
        self.projects_root = Path(projects_root)
        self.user_id = user_id
        self.cwd = Path(options.cwd).resolve()
        self.environment = dict(options.env or {})
        self.locale = self.environment.get("ARCREEL_AGENT_LOCALE", "zh")
        self.state_reader = state_reader
        self.sdk_factory = sdk_factory
        self.tools = {
            tool.name: tool
            for tool in [
                *build_arcreel_tools(
                    project_name=project_name,
                    projects_root=self.projects_root,
                    user_id=user_id,
                ),
                *build_codex_support_tools(
                    project_name=project_name,
                    projects_root=self.projects_root,
                    user_id=user_id,
                ),
            ]
        }
        self.session_id = str(options.resume or options.session_id or uuid4())
        self.history = CodexHistory(options.session_store, self.cwd, self.session_id)
        self.sdk: Any = None
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.model = ""
        self.effort = ""
        self.identity = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending_tool: Any = None
        self.active_tool: asyncio.Task | None = None
        self.messages: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.pump: asyncio.Task | None = None
        self.stopping = False
        self.closed = False
        self.failure_code: str | None = None
        self.stream_ids: set[str] = set()
        self.tool_calls = 0
        self.disabled_mcp: dict[str, Any] = {}
        self.instructions = build_agent_instructions(project_name, self.locale)
        self.tool_results: dict[str, tuple[str, dict[str, Any]]] = {}
        self.tool_lock = asyncio.Lock()

    async def __aenter__(self) -> CodexAgentClient:
        if self.options.session_store is None:
            raise CodexError("codex_request_failed")
        self.loop = asyncio.get_running_loop()
        state = self.state_reader()
        records = await self.history.load()
        restored = restoration(records, self.session_id)
        project = await asyncio.to_thread(
            ProjectManager(str(self.projects_root)).load_project_readonly, self.project_name
        )
        memory = await load_memory_context(
            project_name=self.project_name, projects_root=self.projects_root, user_id=self.user_id
        )
        self.instructions = build_agent_instructions(
            self.project_name, self.locale, project=project, memory_context=memory
        )
        selected = select_model(
            restored.get("model") or self.environment.get("ARCREEL_CODEX_AGENT_MODEL") or None, state
        )
        self.model = selected.get("model") or selected["id"]
        self.effort = restored.get("effort") or self.environment.get("ARCREEL_CODEX_AGENT_EFFORT") or "low"
        environment: dict[str, str] = dict.fromkeys(
            (
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "CODEX_API_KEY",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
            ),
            "",
        )
        config = CodexConfig(
            config_overrides=(
                f'model_provider="{_HTTP_PROVIDER}"',
                f'model_providers.{_HTTP_PROVIDER}.name="OpenAI (ArcReel)"',
                f'model_providers.{_HTTP_PROVIDER}.base_url="https://chatgpt.com/backend-api/codex"',
                f'model_providers.{_HTTP_PROVIDER}.wire_api="responses"',
                f"model_providers.{_HTTP_PROVIDER}.requires_openai_auth=true",
                f"model_providers.{_HTTP_PROVIDER}.supports_websockets=false",
                'forced_login_method="chatgpt"',
            ),
            cwd=str(self.cwd),
            env=environment,
            client_name="arcreel_agent",
            client_title="ArcReel Agent",
        )
        self.sdk = self.sdk_factory(config=config, approval_handler=self.handle_server_request)
        try:
            async with asyncio.timeout(60):
                await asyncio.to_thread(self.sdk.start)
                await asyncio.to_thread(self.sdk.initialize)
                account = _payload(await asyncio.to_thread(self.sdk.account_read)).get("account") or {}
                self.identity = hashlib.sha256(str(account.get("email", "")).encode()).hexdigest()
                if account.get("type") != "chatgpt" or self.identity != state.get("account_hash"):
                    raise CodexError("codex_login_required")
                if restored.get("account_hash") and restored["account_hash"] != self.identity:
                    raise CodexError("codex_login_required")
                # Disable inherited MCP integrations for this thread only. Read
                # server names without logging config values or editing shared settings.
                effective = await asyncio.to_thread(
                    self.sdk.request,
                    "config/read",
                    {"cwd": str(self.cwd), "includeLayers": False},
                    response_model=ConfigReadResponse,
                )
                servers = (effective.config.model_extra or {}).get("mcp_servers", {})
                if isinstance(servers, dict):
                    self.disabled_mcp = {
                        "mcp_servers": {name: {"enabled": False} for name in servers if isinstance(name, str)}
                    }
                params = self.thread_options()
                if restored.get("thread_id"):
                    # Resumed/forked threads retain their original dynamic tool definitions.
                    params.pop("dynamicTools", None)
                    if restored.get("fork"):
                        response = await asyncio.to_thread(
                            self.sdk.thread_fork,
                            restored["thread_id"],
                            {**params, "lastTurnId": restored["last_turn_id"]},
                        )
                    else:
                        response = await asyncio.to_thread(self.sdk.thread_resume, restored["thread_id"], params)
                else:
                    response = await asyncio.to_thread(self.sdk.thread_start, params)
                self.thread_id = _payload(response)["thread"]["id"]
            await self.emit({"type": "system", "subtype": "init", "model": self.model, "runtime": "codex"})
            return self
        except BaseException:
            await self.close_sdk()
            raise

    def thread_options(self) -> dict[str, Any]:
        specs = [
            {
                "type": "function",
                "name": f"arcreel_{tool.name}",
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self.tools.values()
        ]
        specs.append(
            {
                "type": "function",
                "name": "AskUserQuestion",
                "description": "Ask the user essential clarification questions.",
                "inputSchema": _ASK_SCHEMA,
            }
        )
        return {
            "model": self.model,
            "modelProvider": _HTTP_PROVIDER,
            "cwd": str(self.cwd),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "dynamicTools": specs,
            "baseInstructions": self.instructions,
            "config": {
                **self.disabled_mcp,
                "features.shell_tool": False,
                "features.multi_agent": False,
                "features.apps": False,
                "features.plugins": False,
                "features.memories": False,
                "features.image_generation": False,
                "features.skill_search": False,
                "skills.include_instructions": False,
                "web_search": "disabled",
                "project_doc_max_bytes": 0,
            },
        }

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.stopping = True
        await self.close_sdk()
        if self.pump is not None and not self.pump.done():
            self.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.pump

    async def close_sdk(self) -> None:
        self.closed = True
        if self.pending_tool is not None:
            self.pending_tool.cancel()
        if self.active_tool is not None:
            self.active_tool.cancel()
        if self.sdk is not None:
            await asyncio.to_thread(self.sdk.close)

    async def emit(self, message: dict[str, Any], *, completed: bool = False) -> None:
        message.setdefault("session_id", self.session_id)
        message.setdefault("uuid", str(uuid4()))
        message.setdefault("timestamp", utc_now_iso())
        await self.history.append(
            message,
            {
                "session_id": self.session_id,
                "thread_id": self.thread_id,
                "turn_id": self.turn_id,
                "turn_completed": completed,
                "model": self.model,
                "effort": self.effort,
                "account_hash": self.identity,
            },
        )
        await self.messages.put(message)

    async def query(self, prompt: str | AsyncIterable[dict], session_id: str = "default") -> None:
        if self.closed:
            raise CodexError("codex_request_failed")
        if self.pump is not None and not self.pump.done():
            raise RuntimeError("session busy")
        self.stopping = False
        self.failure_code = None
        self.stream_ids.clear()
        self.tool_calls = 0
        self.tool_results.clear()
        blocks: list[dict[str, Any]] = []
        if isinstance(prompt, str):
            blocks.append({"type": "text", "text": prompt})
        else:
            async for message in prompt:
                content = message.get("message", {}).get("content", [])
                blocks.extend(content if isinstance(content, list) else [{"type": "text", "text": content}])
        inputs: list[dict[str, Any]] = []
        for block in blocks:
            if block.get("type") == "text":
                inputs.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    inputs.append({"type": "image", "url": f"data:{source['media_type']};base64,{source['data']}"})
                else:
                    raise CodexError("codex_request_failed")
        try:
            async with asyncio.timeout(60):
                started = await asyncio.to_thread(self.sdk.turn_start, self.thread_id, inputs, {"effort": self.effort})
            self.turn_id = _payload(started)["turn"]["id"]
            await self.emit({"type": "user", "content": blocks})
            self.pump = asyncio.create_task(self.pump_turn(), name=f"codex-agent-{self.session_id}")
        except BaseException:
            await self.close_sdk()
            raise

    async def receive_response(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            message = await self.messages.get()
            if message is None:
                return
            yield message

    async def interrupt(self) -> None:
        self.stopping = True
        if self.pending_tool is not None:
            self.pending_tool.cancel()
        if self.active_tool is not None:
            self.active_tool.cancel()
        if self.turn_id is not None:
            try:
                async with asyncio.timeout(10):
                    await asyncio.to_thread(self.sdk.turn_interrupt, self.thread_id, self.turn_id)
            except Exception:
                # Closing wakes the SDK's blocking reader; the pump emits an interrupted result.
                await self.close_sdk()

    def handle_server_request(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        """The SDK invokes this callback on its reader thread, not the ASGI loop."""
        if method != "item/tool/call" or not params or self.stopping or self.loop is None:
            return {"decision": "decline"}
        if params.get("threadId") != self.thread_id:
            return _tool_problem("wrong_session", locale=self.locale)
        if params.get("turnId") != self.turn_id:
            return _tool_problem("stale_turn", locale=self.locale)
        future = asyncio.run_coroutine_threadsafe(self.dispatch_tool(params), self.loop)
        self.pending_tool = future
        try:
            return future.result(timeout=600)
        except (FutureCancelledError, TimeoutError):
            future.cancel()
            return {"success": False, "contentItems": [{"type": "inputText", "text": "Tool call interrupted"}]}
        except Exception as exc:
            logger.warning("Codex tool dispatch failed: %s", redact_diagnostic_text(exc)[:1000])
            if self.failure_code is not None:
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.interrupt()))
            return {"success": False, "contentItems": [{"type": "inputText", "text": "Tool execution failed"}]}
        finally:
            if self.pending_tool is future:
                self.pending_tool = None

    async def dispatch_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        # Execute project mutations serially and do not replay a completed call
        # if the transport delivers its request more than once.
        async with self.tool_lock:
            call_id = str(params.get("callId", ""))
            signature = json.dumps([params.get("tool"), params.get("arguments")], sort_keys=True)
            if call_id and call_id in self.tool_results:
                original, result = self.tool_results[call_id]
                if original != signature:
                    return _tool_problem("call_id_conflict", locale=self.locale)
                return result
            result = await self.dispatch_once(params)
            if call_id:
                self.tool_results[call_id] = (signature, result)
            return result

    async def dispatch_once(self, params: dict[str, Any]) -> dict[str, Any]:
        # The loop may enter this coroutine before the reader has assigned its
        # concurrent Future. Track the actual Task so an immediate stop cannot
        # miss a pending question during that handoff.
        task = asyncio.current_task()
        self.active_tool = task
        try:
            if self.stopping or self.closed:
                return {"success": False, "contentItems": [{"type": "inputText", "text": "Tool call interrupted"}]}
            try:
                return await self.invoke_tool(params)
            except jsonschema.ValidationError as exc:
                # Do not echo invalid instance values, which can contain secrets
                # or the entire script. The field and rule identify the repair.
                return _tool_problem(
                    "invalid_tool_arguments",
                    locale=self.locale,
                    tool=str(params.get("tool", "")),
                    field=list(exc.absolute_path),
                    rule=exc.validator,
                    expected=exc.validator_value,
                )
        finally:
            if self.active_tool is task:
                self.active_tool = None

    async def invoke_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        self.tool_calls += 1
        if self.tool_calls > (self.options.max_turns or 64):
            self.failure_code = "codex_tool_unsupported"
            raise CodexError("codex_tool_unsupported")
        name = str(params.get("tool", ""))
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _tool_problem("invalid_tool_arguments", locale=self.locale)
        if name == "AskUserQuestion":
            jsonschema.validate(arguments, _ASK_SCHEMA)
            result = await self.options.can_use_tool(name, arguments, None)
            if getattr(result, "behavior", None) != "allow":
                response = {"isError": True, "content": [{"type": "text", "text": "Question cancelled"}]}
            else:
                response = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result.updated_input.get("answers", {}), ensure_ascii=False),
                        }
                    ]
                }
        else:
            prefix = "arcreel_"
            tool = self.tools.get(name.removeprefix(prefix)) if name.startswith(prefix) else None
            if tool is None:
                return _tool_problem("unknown_tool", locale=self.locale, tool=name)
            if not isinstance(tool.input_schema, dict):
                raise CodexError("codex_tool_unsupported")
            jsonschema.validate(arguments, tool.input_schema)
            response = await tool.handler(arguments)
        content = []
        for block in response.get("content", []):
            if block.get("type") == "text":
                content.append({"type": "inputText", "text": block.get("text", "")})
            elif block.get("type") == "image":
                content.append({"type": "inputImage", "imageUrl": f"data:{block['mimeType']};base64,{block['data']}"})
        return {"success": not (response.get("isError") or response.get("is_error")), "contentItems": content}

    async def stream_text(self, item_id: str, text: str) -> None:
        if item_id not in self.stream_ids:
            self.stream_ids.add(item_id)
            await self.emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": item_id}}})
            await self.emit(
                {
                    "type": "stream_event",
                    "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                }
            )
        await self.emit(
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            }
        )

    async def pump_turn(self) -> None:
        usage: dict[str, Any] = {}
        usage_baseline: dict[str, int] | None = None
        error: dict[str, Any] | None = None
        completed = False
        try:
            async with asyncio.timeout(float(os.environ.get("ARCREEL_CODEX_AGENT_TIMEOUT_SECONDS", "1800"))):
                while True:
                    notification = await asyncio.to_thread(self.sdk.next_turn_notification, self.turn_id)
                    params = _payload(notification.payload)
                    method = notification.method
                    if method == "item/agentMessage/delta":
                        await self.stream_text(params["itemId"], params.get("delta", ""))
                    elif method == "item/started":
                        item = params.get("item", {})
                        if item.get("type") == "dynamicToolCall":
                            await self.emit(
                                {
                                    "type": "assistant",
                                    "message_id": item["id"],
                                    "model": self.model,
                                    "content": [
                                        {
                                            "type": "tool_use",
                                            "id": item["id"],
                                            "name": (
                                                "mcp__arcreel__" + item["tool"].removeprefix("arcreel_")
                                                if item["tool"].startswith("arcreel_")
                                                else item["tool"]
                                            ),
                                            "input": item.get("arguments", {}),
                                        }
                                    ],
                                }
                            )
                    elif method == "item/completed":
                        item = params.get("item", {})
                        if item.get("type") == "agentMessage":
                            await self.emit(
                                {
                                    "type": "assistant",
                                    "message_id": item["id"],
                                    "model": self.model,
                                    "content": [{"type": "text", "text": item.get("text", "")}],
                                }
                            )
                        elif item.get("type") == "dynamicToolCall":
                            blocks = [
                                {"type": "text", "text": b.get("text", "")}
                                for b in item.get("contentItems") or []
                                if b.get("type") == "inputText"
                            ]
                            await self.emit(
                                {
                                    "type": "user",
                                    "content": [
                                        {
                                            "type": "tool_result",
                                            "tool_use_id": item["id"],
                                            "content": blocks,
                                            "is_error": item.get("success") is False or item.get("status") == "failed",
                                        }
                                    ],
                                }
                            )
                    elif method == "thread/tokenUsage/updated":
                        reported = params.get("tokenUsage", {})
                        last = reported.get("last", {})
                        total = reported.get("total", {})
                        fields = ("inputTokens", "outputTokens")
                        if all(isinstance(source.get(key), int) for source in (last, total) for key in fields):
                            if usage_baseline is None:
                                # `last` is one inference request, not the whole
                                # turn. Infer the prior cumulative usage on the
                                # first update, including after a cold resume.
                                usage_baseline = {key: total[key] - last[key] for key in fields}
                            usage = {key: max(0, total[key] - usage_baseline[key]) for key in fields}
                        else:
                            usage = last
                    elif method == "error":
                        error = params.get("error")
                    elif method == "turn/completed":
                        turn = params.get("turn", {})
                        status = (
                            "error"
                            if self.failure_code
                            else (
                                "interrupted"
                                if self.stopping or turn.get("status") == "interrupted"
                                else ("completed" if turn.get("status") == "completed" else "error")
                            )
                        )
                        error = turn.get("error") or error
                        if error and status == "error":
                            CodexError("codex_generation_failed", upstream_error=error)
                        await self.finish(status, usage, terminal=True)
                        completed = True
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Codex agent turn stopped: %s", redact_diagnostic_text(exc)[:1000])
            if isinstance(exc, TimeoutError):
                self.failure_code = "codex_timeout"
            await self.close_sdk()
            await self.finish(
                "interrupted" if self.stopping and not self.failure_code else "error", usage, terminal=False
            )
            completed = True
        finally:
            if self.turn_id is not None:
                self.sdk.unregister_turn_notifications(self.turn_id)
            self.turn_id = None
            if not completed and not self.stopping:
                await self.finish("error", usage, terminal=False)
            await self.messages.put(None)

    async def finish(self, status: str, usage: dict[str, Any], *, terminal: bool) -> None:
        await self.emit(
            {
                "type": "result",
                "subtype": "success" if status == "completed" else status,
                "session_status": status,
                "is_error": status == "error",
                "provider": "codex",
                "model": self.model,
                "total_cost_usd": 0.0,
                "usage": {"input_tokens": usage.get("inputTokens"), "output_tokens": usage.get("outputTokens")},
                "result": ""
                if status == "completed"
                else _(
                    "codex_cancelled" if status == "interrupted" else (self.failure_code or "codex_generation_failed"),
                    locale=self.locale,
                ),
            },
            completed=terminal,
        )

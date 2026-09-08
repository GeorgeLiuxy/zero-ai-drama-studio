"""Local Codex App Server transport for ChatGPT subscription jobs.

Connection discovery never starts a model turn. Each generation owns one temporary
thread and process; cancellation interrupts that turn before closing its process.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from lib.app_data_dir import app_data_dir
from lib.logging_utils import redact_diagnostic_text
from lib.retry import NonRetryableError

logger = logging.getLogger(__name__)


class CodexError(NonRetryableError):
    """Stable failure code; a subscription job must never retry through paid APIs."""

    def __init__(self, code: str, *, upstream_error: dict[str, Any] | None = None) -> None:
        self.code = code
        self.params: dict[str, str] = {}
        # Keep upstream diagnostics in server logs, never in a public exception message.
        if upstream_error:
            diagnostic = {
                key: upstream_error[key] for key in ("code", "codexErrorInfo", "message") if key in upstream_error
            }
            logger.warning(
                "Codex %s: %s", code, redact_diagnostic_text(json.dumps(diagnostic, ensure_ascii=False))[:2000]
            )
        super().__init__(code)


def read_state() -> dict[str, Any]:
    """Read the non-secret deployment catalog; do not touch account credentials."""
    try:
        state = json.loads((app_data_dir() / ".codex-provider.json").read_text(encoding="utf-8"))
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {"enabled": False, "connected": False, "models": [], "image_generation": False}


def write_state(state: dict[str, Any]) -> None:
    """Atomically replace public connection metadata, without persisting tokens."""
    destination = app_data_dir() / ".codex-provider.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(state, handle, ensure_ascii=False)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def connection_summary(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = read_state() if state is None else state
    return {
        "enabled": bool(state.get("enabled")),
        "connected": bool(state.get("enabled") and state.get("connected")),
        "plan_type": state.get("plan_type"),
        "models_count": len(state.get("models", [])),
        "image_generation": bool(state.get("enabled") and state.get("connected") and state.get("image_generation")),
        "error_code": state.get("error_code"),
    }


def codex_command() -> list[str]:
    """Resolve a native binary; never interpret an executable path as shell code."""
    configured = os.environ.get("ARCREEL_CODEX_BIN")
    command = configured or shutil.which("codex")
    if not command:
        raise CodexError("codex_unavailable")
    path = Path(command)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
        candidates = sorted((path.parent / "node_modules" / "@openai" / "codex").glob("**/bin/codex.exe"))
        if not candidates:
            raise CodexError("codex_unavailable")
        path = candidates[0]
    if not path.is_file():
        raise CodexError("codex_unavailable")
    return [str(path), "app-server", "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"']


class CodexProcess:
    """One bounded JSONL connection. Process I/O runs outside the ASGI event loop."""

    def __init__(self, *, cancel: threading.Event | None = None, seconds: float = 600) -> None:
        self.cancel = cancel or threading.Event()
        self.deadline = time.monotonic() + seconds
        self.events: deque[dict[str, Any]] = deque()
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.sequence = 0
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        environment = dict(os.environ)
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"):
            environment.pop(key, None)
        self.process = subprocess.Popen(
            codex_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        self.messages.put(value)
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def send(self, value: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise CodexError("codex_unavailable") from exc

    def receive(self) -> dict[str, Any]:
        while True:
            if self.cancel.is_set():
                raise CodexError("codex_cancelled")
            if time.monotonic() >= self.deadline:
                raise CodexError("codex_timeout")
            try:
                message = self.messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if message is None:
                raise CodexError("codex_unavailable")
            if "id" in message and "method" in message:
                # Background generation cannot grant command, file or external-tool approvals.
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Unsupported request"}})
                raise CodexError("codex_tool_unsupported")
            return message

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id:
                if "error" in message:
                    raise CodexError("codex_request_failed", upstream_error=message["error"])
                return message.get("result", {})
            self.events.append(message)

    def initialize(self) -> dict[str, Any]:
        result = self.rpc("initialize", {"clientInfo": {"name": "arcreel", "version": "0.29.0"}})
        self.send({"method": "initialized"})
        return result

    def account(self) -> dict[str, Any]:
        account = self.rpc("account/read", {}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise CodexError("codex_login_required")
        return account

    def close(self) -> None:
        if self.process.poll() is None:
            if self.thread_id and self.turn_id:
                with contextlib.suppress(CodexError):
                    self.send(
                        {
                            "id": -1,
                            "method": "turn/interrupt",
                            "params": {
                                "threadId": self.thread_id,
                                "turnId": self.turn_id,
                            },
                        }
                    )
            if self.process.stdin:
                with contextlib.suppress(OSError):
                    self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()


def refresh_connection(
    enabled: bool,
    *,
    process_factory: Any = CodexProcess,
    state_reader: Any = read_state,
    state_writer: Any = write_state,
) -> dict[str, Any]:
    """Inspect account and model metadata; no turn/start or paid API request."""
    state = state_reader()
    if not enabled:
        state["enabled"] = False
        state_writer(state)
        return connection_summary(state)
    process = None
    try:
        process = process_factory(seconds=40)
        initialized = process.initialize()
        account = process.account()
        models: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = process.rpc("model/list", {"limit": 100, "cursor": cursor})
            models.extend(result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        if not models:
            raise CodexError("codex_models_unavailable")
        identity = hashlib.sha256(str(account.get("email", "")).encode()).hexdigest()
        version = initialized.get("userAgent", "")
        image_verified = (
            bool(state.get("image_generation"))
            and state.get("account_hash") == identity
            and state.get("runtime_version") == version
        )
        image_agent = state.get("image_agent_model")
        if not any(item.get("model") == image_agent for item in models):
            image_agent = next((item.get("model") for item in models if "mini" in item.get("model", "")), None)
        state = {
            "enabled": True,
            "connected": True,
            "plan_type": account.get("planType"),
            "models": models,
            "image_generation": image_verified,
            "account_hash": identity,
            "runtime_version": version,
            "error_code": None,
            "image_agent_model": image_agent,
        }
    except (CodexError, OSError) as exc:
        state.update(
            enabled=False,
            connected=False,
            image_generation=False,
            error_code=exc.code if isinstance(exc, CodexError) else "codex_unavailable",
        )
    finally:
        if process is not None:
            process.close()
    state_writer(state)
    return connection_summary(state)


def select_model(model: str | None, state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("enabled") or not state.get("connected"):
        raise CodexError("codex_login_required")
    models = state.get("models", [])
    if model:
        match = next((item for item in models if item.get("id") == model or item.get("model") == model), None)
    else:
        match = next((item for item in models if item.get("isDefault")), None)
    if match is None:
        raise CodexError("codex_models_unavailable")
    return match


def run_turn(
    *,
    model: str,
    inputs: list[dict[str, Any]],
    system_prompt: str | None,
    schema: dict[str, Any] | None,
    image: bool,
    cancel: threading.Event,
    process_factory: Any = CodexProcess,
    state_reader: Any = read_state,
) -> dict[str, Any]:
    state = state_reader()
    selected = select_model(model, state)
    if image and not state.get("image_generation"):
        raise CodexError("codex_image_unverified")
    timeout = float(os.environ.get("ARCREEL_CODEX_TIMEOUT_SECONDS", "600"))
    with contextlib.ExitStack() as stack:
        # Close the child before deleting its working directory (Windows holds a directory handle).
        cwd = stack.enter_context(tempfile.TemporaryDirectory(prefix="arcreel-codex-", ignore_cleanup_errors=True))
        process = process_factory(cancel=cancel, seconds=max(30, min(timeout, 3600)))
        stack.callback(process.close)
        initialized = process.initialize()
        account = process.account()
        identity = hashlib.sha256(str(account.get("email", "")).encode()).hexdigest()
        if identity != state.get("account_hash"):
            raise CodexError("codex_login_required")
        if image and initialized.get("userAgent") != state.get("runtime_version"):
            raise CodexError("codex_image_unverified")
        instruction = (
            (
                "Generate exactly one image using only the built-in image generation tool. "
                "Do not call shell commands, external APIs or other tools. Keep final text under 20 words."
            )
            if image
            else (
                "Return only the requested result. Do not use tools or access files. Follow the output schema when provided."
            )
        )
        thread = process.rpc(
            "thread/start",
            {
                "model": model,
                "modelProvider": "openai",
                "cwd": cwd,
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "baseInstructions": instruction,
                "developerInstructions": system_prompt,
                "config": {
                    "features.shell_tool": False,
                    "features.multi_agent": False,
                    "features.apps": False,
                    "features.plugins": False,
                    "features.memories": False,
                    "features.image_generation": image,
                    "web_search": "disabled",
                    "project_doc_max_bytes": 0,
                },
            },
        )
        process.thread_id = thread["thread"]["id"]
        efforts = {item.get("reasoningEffort") for item in selected.get("supportedReasoningEfforts", [])}
        effort = "low" if "low" in efforts else selected.get("defaultReasoningEffort", "medium")
        turn = process.rpc(
            "turn/start", {"threadId": process.thread_id, "input": inputs, "effort": effort, "outputSchema": schema}
        )
        process.turn_id = turn["turn"]["id"]
        result: dict[str, Any] = {"text": "", "images": [], "usage": {}}
        last_error: dict[str, Any] | None = None
        while True:
            message = process.events.popleft() if process.events else process.receive()
            params = message.get("params", {})
            if params.get("threadId") != process.thread_id:
                continue
            if message.get("method") == "error":
                last_error = params.get("error")
            elif message.get("method") == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                    result["text"] = item.get("text", "")
                elif item.get("type") == "imageGeneration" and item.get("status") == "completed":
                    path = Path(item.get("savedPath") or "").resolve()
                    artifact_root = Path(initialized["codexHome"]) / "generated_images" / process.thread_id
                    if not path.is_relative_to(artifact_root.resolve()) or not path.is_file():
                        raise CodexError("codex_image_missing")
                    if path.stat().st_size > 64 * 1024 * 1024:
                        raise CodexError("codex_image_missing")
                    result["images"].append({"data": path.read_bytes()})
            elif message.get("method") == "thread/tokenUsage/updated":
                result["usage"] = params.get("tokenUsage", {}).get("total", {})
            elif message.get("method") == "turn/completed":
                process.turn_id = None
                completed_turn = params.get("turn", {})
                if completed_turn.get("status") != "completed":
                    error = completed_turn.get("error") or last_error
                    code = "codex_generation_failed"
                    if error and error.get("codexErrorInfo") == "unauthorized":
                        code = "codex_login_required"
                    elif error and error.get("codexErrorInfo") == "badRequest":
                        code = "codex_request_failed"
                    raise CodexError(code, upstream_error=error)
                return result


class CodexRuntime:
    """Serialize subscription work across image and text adapters in this process."""

    def __init__(self, *, runner: Any = run_turn) -> None:
        self.runner = runner
        self.semaphore = asyncio.Semaphore(1)

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        await self.semaphore.acquire()
        cancel = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self.runner, **kwargs, cancel=cancel))

        def finished(result: asyncio.Task) -> None:
            # Ownership follows the worker, including repeated caller cancellation.
            self.semaphore.release()
            if not result.cancelled():
                result.exception()

        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel.set()
            raise


runtime = CodexRuntime()

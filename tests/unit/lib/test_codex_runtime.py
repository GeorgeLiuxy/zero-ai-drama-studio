from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
from collections import deque
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from lib.codex_runtime import CodexError, CodexProcess, CodexRuntime, refresh_connection, run_turn


def account_state() -> dict[str, Any]:
    return {
        "enabled": True,
        "connected": True,
        "models": [{"id": "account-model", "model": "account-model", "isDefault": True}],
        "account_hash": hashlib.sha256(b"subscription@example.test").hexdigest(),
        "runtime_version": "test-runtime",
        "image_generation": True,
    }


class FakeProcess:
    """In-memory App Server boundary with the public process protocol."""

    def __init__(
        self,
        codex_home: Path,
        *,
        models: list[dict] | None = None,
        block: bool = False,
        cleanup_gate: threading.Event | None = None,
    ):
        self.codex_home = codex_home
        self.models = account_state()["models"] if models is None else models
        self.block = block
        self.events: deque[dict[str, Any]] = deque()
        self.cancel = threading.Event()
        self.receiving = threading.Event()
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.closed = False
        self.cleanup_gate = cleanup_gate
        self.cleanup_started = threading.Event()
        self.cwd: Path | None = None
        self.cwd_exists_at_close = False

    def __call__(self, *, cancel: threading.Event | None = None, seconds: float = 600) -> FakeProcess:
        self.cancel = cancel if cancel is not None else self.cancel
        return self

    def initialize(self) -> dict[str, Any]:
        return {"userAgent": "test-runtime", "codexHome": str(self.codex_home)}

    def account(self) -> dict[str, str]:
        return {"type": "chatgpt", "email": "subscription@example.test", "planType": "plus"}

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "model/list":
            return {"data": self.models, "nextCursor": None}
        if method == "thread/start":
            self.cwd = Path(params["cwd"])
            return {"thread": {"id": "thread-test"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-test"}}
        raise AssertionError(f"Unexpected App Server method: {method}")

    def receive(self) -> dict[str, Any]:
        self.receiving.set()
        if self.block and self.cancel.wait(timeout=5):
            raise CodexError("codex_cancelled")
        raise AssertionError("The test supplied no completion event")

    def close(self) -> None:
        self.cwd_exists_at_close = self.cwd is not None and self.cwd.is_dir()
        self.cleanup_started.set()
        if self.cleanup_gate is not None and not self.cleanup_gate.wait(timeout=5):
            raise AssertionError("The test did not release process cleanup")
        self.closed = True


def test_api_key_account_is_rejected_by_real_process_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"test executable placeholder")
    monkeypatch.setenv("ARCREEL_CODEX_BIN", str(executable))
    monkeypatch.setenv("OPENAI_API_KEY", "paid-key-must-not-propagate")
    environments: list[dict] = []

    class FakeChild:
        def __init__(self, command: list[str], **kwargs: Any):
            environments.append(kwargs["env"])
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(json.dumps({"id": 1, "result": {"account": {"type": "apiKey"}}}) + "\n")

        def poll(self) -> int:
            return 0

    monkeypatch.setattr("lib.codex_runtime.subprocess.Popen", FakeChild)
    process = CodexProcess()
    try:
        with pytest.raises(CodexError) as exc:
            process.account()
        assert exc.value.code == "codex_login_required"
        assert "OPENAI_API_KEY" not in environments[0]
    finally:
        process.close()


def test_refresh_invalidates_image_verification_after_runtime_change(tmp_path: Path):
    state = account_state()
    state["runtime_version"] = "older-runtime"
    saved: list[dict[str, Any]] = []
    process = FakeProcess(tmp_path)

    summary = refresh_connection(True, process_factory=process, state_reader=lambda: state, state_writer=saved.append)

    assert summary == {
        "enabled": True,
        "connected": True,
        "plan_type": "plus",
        "models_count": 1,
        "image_generation": False,
        "error_code": None,
    }
    assert saved[0]["models"] == account_state()["models"]
    assert "email" not in saved[0]
    assert process.closed


def test_refresh_with_no_models_disables_stale_subscription_catalog(tmp_path: Path):
    saved: list[dict[str, Any]] = []
    process = FakeProcess(tmp_path, models=[])

    summary = refresh_connection(True, process_factory=process, state_reader=account_state, state_writer=saved.append)

    assert summary["error_code"] == "codex_models_unavailable"
    assert summary["enabled"] is False
    assert summary["connected"] is False
    assert saved[0]["image_generation"] is False
    assert process.closed


@pytest.mark.parametrize("inside_artifact_root", [True, False])
def test_image_turn_reads_only_its_completed_artifact(tmp_path: Path, inside_artifact_root: bool):
    artifact_root = tmp_path / "generated_images" / "thread-test"
    artifact_root.mkdir(parents=True)
    artifact = (artifact_root if inside_artifact_root else tmp_path) / "image.png"
    artifact.write_bytes(b"artifact bytes")
    process = FakeProcess(tmp_path)
    process.events.extend(
        [
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-test",
                    "turnId": "turn-test",
                    "item": {"type": "imageGeneration", "status": "completed", "savedPath": str(artifact)},
                },
            },
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-test", "turn": {"id": "turn-test", "status": "completed"}},
            },
        ]
    )
    operation = partial(
        run_turn,
        model="account-model",
        inputs=[{"type": "text", "text": "Draw a circle"}],
        system_prompt=None,
        schema=None,
        image=True,
        cancel=threading.Event(),
        process_factory=process,
        state_reader=account_state,
    )

    if inside_artifact_root:
        assert operation()["images"] == [{"data": b"artifact bytes"}]
    else:
        with pytest.raises(CodexError) as exc:
            operation()
        assert exc.value.code == "codex_image_missing"
    assert process.closed


@pytest.mark.parametrize("status", ["completed", "failed"])
def test_turn_releases_process_before_removing_working_directory(tmp_path: Path, status: str):
    process = FakeProcess(tmp_path)
    process.events.append(
        {"method": "turn/completed", "params": {"threadId": "thread-test", "turn": {"status": status}}}
    )
    operation = partial(
        run_turn,
        model="account-model",
        inputs=[],
        system_prompt=None,
        schema=None,
        image=False,
        cancel=threading.Event(),
        process_factory=process,
        state_reader=account_state,
    )

    if status == "completed":
        assert operation() == {"text": "", "images": [], "usage": {}}
    else:
        with pytest.raises(CodexError, match=r"^codex_generation_failed$"):
            operation()

    assert process.closed
    assert process.cwd_exists_at_close
    assert process.cwd is not None
    assert not process.cwd.exists()


@pytest.mark.parametrize("error_in_completion", [True, False])
def test_failed_turn_retains_redacted_upstream_reason_in_logs_only(tmp_path: Path, caplog, error_in_completion: bool):
    process = FakeProcess(tmp_path)
    upstream = {
        "codexErrorInfo": "badRequest",
        "message": "Invalid schema: additionalProperties must be false; api_key=sk-secretcredential",
        "additionalDetails": "source document must not be logged",
    }
    if not error_in_completion:
        process.events.append({"method": "error", "params": {"threadId": "thread-test", "error": upstream}})
    process.events.append(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-test",
                "turn": {"status": "failed", "error": upstream if error_in_completion else None},
            },
        }
    )

    with pytest.raises(CodexError, match=r"^codex_request_failed$"):
        run_turn(
            model="account-model",
            inputs=[],
            system_prompt=None,
            schema=None,
            image=False,
            cancel=threading.Event(),
            process_factory=process,
            state_reader=account_state,
        )

    assert "additionalProperties must be false" in caplog.text
    assert "sk-secretcredential" not in caplog.text
    assert "source document must not be logged" not in caplog.text
    assert process.closed
    assert process.cwd_exists_at_close


async def test_repeated_cancellation_keeps_subscription_serial_until_cleanup(tmp_path: Path):
    cleanup_gate = threading.Event()
    cancelled = FakeProcess(tmp_path, block=True, cleanup_gate=cleanup_gate)
    following = FakeProcess(tmp_path)
    following.events.append(
        {"method": "turn/completed", "params": {"threadId": "thread-test", "turn": {"status": "completed"}}}
    )
    lock = threading.Lock()
    active = 0
    peak = 0

    def runner(*, process: FakeProcess, cancel: threading.Event) -> dict[str, Any]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            return run_turn(
                model="account-model",
                inputs=[],
                system_prompt=None,
                schema=None,
                image=False,
                cancel=cancel,
                process_factory=process,
                state_reader=account_state,
            )
        finally:
            with lock:
                active -= 1

    client = CodexRuntime(runner=runner)
    task = asyncio.create_task(client.generate(process=cancelled))
    try:
        assert await asyncio.to_thread(cancelled.receiving.wait, 3)
        task.cancel()
        assert await asyncio.to_thread(cancelled.cleanup_started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.semaphore.locked()
        assert cancelled.cancel.is_set()
        next_task = asyncio.create_task(client.generate(process=following))
    finally:
        cleanup_gate.set()

    assert await next_task == {"text": "", "images": [], "usage": {}}
    assert peak == 1
    assert cancelled.closed
    assert following.closed

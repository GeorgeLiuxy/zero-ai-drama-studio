"""Codex runtime identity and transcript persistence in the shared SessionStore."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from lib.agent_session_store import make_project_key
from lib.agent_session_store.prefix_fork import SessionStoreLike
from server.agent_runtime.message_serialization import utc_now_iso

_PERSISTED_TYPES = frozenset({"user", "assistant", "system", "result"})
_CHAIN_TYPES = frozenset({"user", "assistant", "system"})


class CodexRestoration(TypedDict):
    thread_id: str | None
    fork: bool
    last_turn_id: str | None
    model: str | None
    effort: str | None
    account_hash: str | None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def restoration(entries: list[dict[str, Any]], public_session_id: str) -> CodexRestoration:
    """Resume an owned thread, or fork a copied prefix at its last completed turn."""
    markers = [entry["codex"] for entry in entries if isinstance(entry.get("codex"), dict)]
    latest = markers[-1] if markers else {}
    result: CodexRestoration = {
        "thread_id": _text(latest.get("thread_id")),
        "fork": False,
        "last_turn_id": _text(latest.get("turn_id")),
        "model": _text(latest.get("model")),
        "effort": _text(latest.get("effort")),
        "account_hash": _text(latest.get("account_hash")),
    }
    if latest.get("session_id") == public_session_id:
        return result
    # copy_session_prefix rebinds sessionId but deliberately retains the original
    # Codex marker. Never fork that source thread beyond the copied prefix.
    completed = next(
        (
            marker
            for marker in reversed(markers)
            if marker.get("thread_id") == result["thread_id"]
            and marker.get("turn_completed") is True
            and _text(marker.get("turn_id")) is not None
        ),
        None,
    )
    if result["thread_id"] is not None and completed is not None:
        result["fork"] = True
        result["last_turn_id"] = completed["turn_id"]
    else:
        result["thread_id"] = None
        result["last_turn_id"] = None
    return result


async def session_runtime_overrides(store: SessionStoreLike | None, cwd: Path | str, session_id: str) -> dict[str, str]:
    """Recover the persisted backend and Codex selection for an existing session."""
    if store is None:
        return {}
    entries = await store.load({"project_key": make_project_key(cwd), "session_id": session_id}) or []
    for entry in reversed(entries):
        marker = entry.get("codex")
        if isinstance(marker, dict):
            overrides = {"ARCREEL_AGENT_BACKEND": "codex"}
            for key, environment in (
                ("model", "ARCREEL_CODEX_AGENT_MODEL"),
                ("effort", "ARCREEL_CODEX_AGENT_EFFORT"),
            ):
                value = _text(marker.get(key))
                if value is not None:
                    overrides[environment] = value
            return overrides
    if any(entry.get("type") in {"user", "assistant"} and isinstance(entry.get("message"), dict) for entry in entries):
        return {"ARCREEL_AGENT_BACKEND": "claude"}
    return {}


class CodexHistory:
    """Persist complete normalized messages with SDK-compatible parent links."""

    def __init__(self, store: SessionStoreLike | None, cwd: Path | str, session_id: str) -> None:
        self._store = store
        self._key = {"project_key": make_project_key(cwd), "session_id": session_id}
        self._session_id = session_id
        self._lock = asyncio.Lock()
        self._loaded = False
        self._parent_uuid: str | None = None
        self._uuids: set[str] = set()

    async def _load(self) -> list[dict[str, Any]]:
        entries = await self._store.load(self._key) if self._store is not None else None
        entries = entries or []
        self._uuids = {entry["uuid"] for entry in entries if _text(entry.get("uuid")) is not None}
        self._parent_uuid = next(
            (
                entry["uuid"]
                for entry in reversed(entries)
                if entry.get("type") in _CHAIN_TYPES and _text(entry.get("uuid")) is not None
            ),
            None,
        )
        self._loaded = True
        return deepcopy(entries)

    async def load(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await self._load()

    async def append(self, message: dict[str, Any], codex: dict[str, Any]) -> None:
        if message.get("type") not in _PERSISTED_TYPES or self._store is None:
            return
        async with self._lock:
            if not self._loaded:
                await self._load()
            entry = deepcopy(message)
            entry_uuid = _text(entry.get("uuid")) or str(uuid4())
            if entry_uuid in self._uuids:
                return
            entry.update(
                uuid=entry_uuid,
                sessionId=self._session_id,
                parentUuid=self._parent_uuid,
                timestamp=_text(entry.get("timestamp")) or utc_now_iso(),
                codex={
                    **deepcopy(codex),
                    "session_id": self._session_id,
                    "turn_id": codex.get("turn_id"),
                    "turn_completed": codex.get("turn_completed") is True,
                },
            )
            if entry["type"] in {"user", "assistant"}:
                nested = entry.get("message")
                nested = nested if isinstance(nested, dict) else {}
                nested["role"] = entry["type"]
                nested["content"] = entry.pop("content", nested.get("content", []))
                for key in ("model", "usage", "stop_reason"):
                    if key in entry:
                        nested[key] = entry.pop(key)
                entry["message"] = nested
            await self._store.append(self._key, [entry])
            self._uuids.add(entry_uuid)
            # Result records persist runtime metadata but the SDK excludes them
            # from its conversation graph. They must not become chain parents.
            if entry["type"] in _CHAIN_TYPES:
                self._parent_uuid = entry_uuid


__all__ = ["CodexHistory", "CodexRestoration", "restoration", "session_runtime_overrides"]

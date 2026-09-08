"""Structured text generation through a locally authenticated Codex process."""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

import jsonschema

from lib.codex_runtime import CodexError, read_state, runtime, select_model
from lib.data_uri import image_to_data_uri
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    resolve_schema,
)

logger = logging.getLogger(__name__)


def _strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Close fixed objects for Codex without changing the caller's validation schema."""
    result = deepcopy(schema)

    def normalize(node: dict[str, Any]) -> None:
        if any(key in node for key in ("allOf", "oneOf", "not", "if", "then", "else", "patternProperties")):
            logger.warning("Codex output schema contains unsupported composition or conditional keywords")
            raise CodexError("codex_request_failed")

        node.pop("default", None)
        properties = node.get("properties")
        node_type = node.get("type")
        if node_type == "object" or (isinstance(node_type, list) and "object" in node_type) or properties is not None:
            if node.get("additionalProperties", False) is not False or (
                properties is None and node.get("additionalProperties") is not False
            ):
                logger.warning(
                    "Codex output schema requires fixed object properties; open dictionaries are unsupported"
                )
                raise CodexError("codex_request_failed")
            node["additionalProperties"] = False
            node["properties"] = properties = properties or {}
            # A default does not make a field nullable. Request a value for every
            # field, preserving each original type (including explicit null unions).
            node["required"] = list(properties)
            for child in properties.values():
                normalize(child)
        items = node.get("items")
        if isinstance(items, dict):
            normalize(items)
        for child in node.get("anyOf", []):
            normalize(child)
        for definitions in ("$defs", "definitions"):
            for child in node.get(definitions, {}).values():
                normalize(child)

    if result.get("type") != "object" or "anyOf" in result:
        logger.warning("Codex output schema requires an object at the root")
        raise CodexError("codex_request_failed")
    normalize(result)
    return result


class CodexTextBackend:
    def __init__(self, model: str | None = None, *, client: Any = runtime, state_reader: Any = read_state) -> None:
        self._model = model
        self.client = client
        self.state_reader = state_reader

    @property
    def name(self) -> str:
        return "codex"

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        item = select_model(None, self.state_reader())
        return item.get("model") or item["id"]

    @property
    def capabilities(self) -> set[TextCapability]:
        item = select_model(self.model, self.state_reader())
        capabilities = {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}
        if "image" in item.get("inputModalities", []):
            capabilities.add(TextCapability.VISION)
        return capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        inputs: list[dict[str, Any]] = []
        for picture in request.images or []:
            if picture.path:
                uri = await asyncio.to_thread(image_to_data_uri, picture.path, {})
                inputs.append({"type": "image", "url": uri})
            elif picture.url:
                inputs.append({"type": "image", "url": picture.url})
        inputs.append({"type": "text", "text": request.prompt})
        schema = resolve_schema(request.response_schema) if request.response_schema else None
        result = await self.client.generate(
            model=self.model,
            inputs=inputs,
            system_prompt=request.system_prompt,
            schema=_strict_output_schema(schema) if schema is not None else None,
            image=False,
        )
        text = result.get("text", "")
        if not text:
            raise CodexError("codex_empty_result")
        if schema is not None:
            try:
                jsonschema.validate(json.loads(text), schema)
            except (ValueError, jsonschema.ValidationError) as exc:
                raise CodexError("codex_invalid_json") from exc
        usage = result.get("usage", {})
        return TextGenerationResult(
            text=text,
            provider=self.name,
            model=self.model,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
        )

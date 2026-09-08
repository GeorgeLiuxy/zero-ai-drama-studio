from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic import BaseModel, Field

from lib.codex_runtime import CodexError
from lib.project_manager import ProjectOverview
from lib.text_backends.base import TextGenerationRequest
from lib.text_backends.codex import CodexTextBackend


class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.request: dict[str, Any] = {}

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {"text": self.text, "usage": {"inputTokens": 12, "outputTokens": 5}}


def scene_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"scene": {"type": "string"}}, "required": ["scene"]}


async def test_structured_text_result_preserves_schema_and_subscription_usage():
    client = FakeClient('{"scene":"A blue circle"}')
    backend = CodexTextBackend(model="account-model", client=client)
    schema = scene_schema()

    result = await backend.generate(
        TextGenerationRequest(prompt="Describe a scene", system_prompt="Be brief", response_schema=schema)
    )

    assert json.loads(result.text) == {"scene": "A blue circle"}
    assert (result.provider, result.model, result.input_tokens, result.output_tokens) == (
        "codex",
        "account-model",
        12,
        5,
    )
    assert client.request["schema"] == {**scene_schema(), "additionalProperties": False}
    assert schema == scene_schema()
    assert client.request["image"] is False


async def test_project_overview_uses_closed_structured_output():
    overview = {
        "synopsis": "A detective returns home.",
        "genre": "Mystery",
        "theme": "Trust",
        "world_setting": "A modern coastal town.",
        "language": "en",
    }
    client = FakeClient(json.dumps(overview))
    backend = CodexTextBackend(model="account-model", client=client)

    result = await backend.generate(TextGenerationRequest(prompt="Analyze source", response_schema=ProjectOverview))

    assert ProjectOverview.model_validate_json(result.text).model_dump() == overview
    assert client.request["schema"]["additionalProperties"] is False
    assert set(client.request["schema"]["required"]) == set(overview)
    assert "additionalProperties" not in ProjectOverview.model_json_schema()


async def test_nested_defaults_preserve_original_response_validation():
    class Scene(BaseModel):
        label: str = "Untitled"

    class Story(BaseModel):
        scenes: list[Scene]
        ending: Scene | None = None
        title: str = "Untitled"
        tags: list[str] = Field(default_factory=list)

    schema = Story.model_json_schema()
    original = deepcopy(schema)
    client = FakeClient('{"scenes":[{}],"extra":"accepted by original schema"}')
    backend = CodexTextBackend(model="account-model", client=client)

    result = await backend.generate(TextGenerationRequest(prompt="Plan story", response_schema=schema))

    story = Story.model_validate_json(result.text)
    assert story.scenes[0].label == "Untitled"
    assert story.ending is None
    assert story.title == "Untitled"
    assert story.tags == []
    strict = client.request["schema"]
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == {"scenes", "ending", "title", "tags"}
    scene = strict["properties"]["scenes"]["items"]
    assert scene["additionalProperties"] is False
    assert scene["required"] == ["label"]
    assert "default" not in scene["properties"]["label"]
    ending = strict["properties"]["ending"]
    assert "default" not in ending
    assert ending["anyOf"][0]["additionalProperties"] is False
    assert ending["anyOf"][1] == {"type": "null"}
    assert strict["properties"]["title"]["type"] == "string"
    assert "default" not in strict["properties"]["title"]
    assert schema == original


@pytest.mark.parametrize("additional_properties", [True, {"type": "string"}])
async def test_open_dictionary_schema_is_rejected_before_generation(additional_properties):
    schema = {
        "type": "object",
        "properties": {"metadata": {"type": "object", "additionalProperties": additional_properties}},
        "required": ["metadata"],
    }
    original = deepcopy(schema)
    client = FakeClient('{"metadata":{"key":"value"}}')
    backend = CodexTextBackend(model="account-model", client=client)

    with pytest.raises(CodexError) as exc:
        await backend.generate(TextGenerationRequest(prompt="Describe metadata", response_schema=schema))

    assert exc.value.code == "codex_request_failed"
    assert client.request == {}
    assert schema == original


async def test_invalid_structured_response_fails_without_paid_fallback():
    backend = CodexTextBackend(model="account-model", client=FakeClient('{"scene":42}'))

    with pytest.raises(CodexError) as exc:
        await backend.generate(TextGenerationRequest(prompt="Describe a scene", response_schema=scene_schema()))
    assert exc.value.code == "codex_invalid_json"

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.codex_runtime import CodexError
from lib.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.image_backends.codex import CodexImageBackend


class FakeClient:
    def __init__(self, data: bytes):
        self.data = data
        self.request: dict[str, Any] = {}

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        self.request = kwargs
        return {"images": [{"data": self.data}], "usage": {"inputTokens": 8, "outputTokens": 2}}


async def test_image_adapter_encodes_reference_and_writes_verified_png(tmp_path: Path):
    buffer = io.BytesIO()
    Image.new("RGB", (3, 2), "blue").save(buffer, format="PNG")
    reference = tmp_path / "reference.png"
    reference.write_bytes(buffer.getvalue())
    destination = tmp_path / "output" / "scene.png"
    client = FakeClient(buffer.getvalue())
    state = {
        "enabled": True,
        "connected": True,
        "image_generation": True,
        "models": [{"model": "account-model", "isDefault": True}],
    }
    backend = CodexImageBackend(client=client, state_reader=lambda: state)

    result = await backend.generate(
        ImageGenerationRequest(
            prompt="Use this reference",
            output_path=destination,
            reference_images=[ReferenceImage(str(reference), "Scene")],
        )
    )

    assert result.image_path == destination
    assert (result.provider, result.model, result.text_input_tokens, result.text_output_tokens) == (
        "codex",
        "gpt-image-2",
        8,
        2,
    )
    with Image.open(destination) as output:
        assert output.format == "PNG"
        assert output.size == (3, 2)
        assert output.getpixel((0, 0)) == (0, 0, 255)
    encoded = next(item["url"] for item in client.request["inputs"] if item["type"] == "image")
    assert base64.b64decode(encoded.split(",", 1)[1]) == reference.read_bytes()


async def test_unverified_image_connection_creates_no_output(tmp_path: Path):
    destination = tmp_path / "image.png"
    backend = CodexImageBackend(client=FakeClient(b""), state_reader=lambda: {"image_generation": False})

    with pytest.raises(CodexError) as exc:
        await backend.generate(ImageGenerationRequest(prompt="Draw a circle", output_path=destination))
    assert exc.value.code == "codex_image_unverified"
    assert not destination.exists()

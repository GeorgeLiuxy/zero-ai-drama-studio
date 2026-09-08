"""Codex image tool adapter; only verified completed artifacts are accepted."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

from PIL import Image

from lib.async_thread import run_noninterruptible_sync
from lib.codex_runtime import CodexError, read_state, runtime, select_model
from lib.data_uri import image_to_data_uri
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult


class CodexImageBackend:
    def __init__(self, model: str | None = None, *, client: Any = runtime, state_reader: Any = read_state) -> None:
        if model not in (None, "gpt-image-2"):
            raise CodexError("codex_models_unavailable")
        self.client = client
        self.state_reader = state_reader

    @property
    def name(self) -> str:
        return "codex"

    @property
    def model(self) -> str:
        return "gpt-image-2"

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        state = self.state_reader()
        if not state.get("image_generation"):
            raise CodexError("codex_image_unverified")
        # The image engine is a tool, separate from the conversation's text model.
        selected = select_model(state.get("image_agent_model"), state)
        inputs: list[dict[str, Any]] = []
        for reference in request.reference_images:
            if reference.label:
                inputs.append({"type": "text", "text": reference.label})
            uri = await asyncio.to_thread(image_to_data_uri, Path(reference.path), {})
            inputs.append({"type": "image", "url": uri})
        prompt = f"{request.prompt}\nRequested aspect ratio: {request.aspect_ratio}."
        if request.image_size:
            prompt += f" Requested image size: {request.image_size}."
        inputs.append({"type": "text", "text": prompt})
        result = await self.client.generate(
            model=selected.get("model") or selected["id"],
            inputs=inputs,
            system_prompt=None,
            schema=None,
            image=True,
        )
        images = result.get("images", [])
        if len(images) != 1:
            raise CodexError("codex_image_missing")

        def save() -> None:
            try:
                with Image.open(io.BytesIO(images[0]["data"])) as picture:
                    picture.load()
                    request.output_path.parent.mkdir(parents=True, exist_ok=True)
                    picture.save(request.output_path, format="PNG")
            except (OSError, ValueError, KeyError) as exc:
                raise CodexError("codex_image_missing") from exc

        await run_noninterruptible_sync(save)
        usage = result.get("usage", {})
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self.name,
            model=self.model,
            text_input_tokens=usage.get("inputTokens"),
            text_output_tokens=usage.get("outputTokens"),
        )

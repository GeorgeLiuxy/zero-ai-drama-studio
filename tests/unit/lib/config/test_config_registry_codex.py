from dataclasses import replace

import pytest

from lib.config.registry import (
    PROVIDER_REGISTRY,
    codex_models_from_state,
    default_model_for_provider,
    model_info_for,
)
from lib.pricing.types import Subscription


def test_codex_catalog_tracks_account_capabilities_without_static_mutation(monkeypatch: pytest.MonkeyPatch):
    state = {
        "enabled": True,
        "connected": True,
        "image_generation": False,
        "models": [
            {"id": "opaque-id", "model": "account-model", "displayName": "Account model", "isDefault": True},
            {"id": "vision-model", "inputModalities": ["text", "image"]},
        ],
    }
    meta = replace(PROVIDER_REGISTRY["codex"], model_source=lambda: codex_models_from_state(state))
    monkeypatch.setitem(PROVIDER_REGISTRY, "codex", meta)

    assert default_model_for_provider("codex", "text") == "account-model"
    text_model = model_info_for("codex", "account-model")
    assert text_model is not None
    assert text_model.display_name == "Account model"
    assert text_model.capabilities == ["text_generation", "structured_output"]
    assert isinstance(text_model.pricing, Subscription)
    assert meta.effective_models["vision-model"].capabilities == ["text_generation", "structured_output", "vision"]
    assert meta.media_types == ["text"]
    assert "text_to_image" not in meta.capabilities

    state["image_generation"] = True
    assert default_model_for_provider("codex", "image") == "gpt-image-2"
    assert meta.media_types == ["image", "text"]
    assert "image_to_image" in meta.capabilities
    assert meta.models == {}

    state["connected"] = False
    assert meta.effective_models == {}
    assert default_model_for_provider("codex", "text") is None
    assert model_info_for("codex", "account-model") is None


@pytest.mark.parametrize(("enabled", "connected"), [(False, True), (True, False), (False, False)])
def test_disconnected_catalog_hides_stale_models_and_image_capability(enabled: bool, connected: bool):
    assert (
        codex_models_from_state(
            {
                "enabled": enabled,
                "connected": connected,
                "models": [{"model": "stale-model", "isDefault": True}],
                "image_generation": True,
            }
        )
        == {}
    )


def test_catalog_ignores_malformed_entries_without_inventing_vision():
    models = codex_models_from_state(
        {
            "enabled": True,
            "connected": True,
            "models": [None, {}, {"model": 2}, {"model": "text-only", "inputModalities": "image"}],
            "image_generation": "true",
        }
    )
    assert list(models) == ["text-only"]
    assert models["text-only"].capabilities == ["text_generation", "structured_output"]

from dataclasses import replace

import pytest

from lib.api_errors import BadRequestError
from lib.config.registry import PROVIDER_REGISTRY, codex_models_from_state
from server.routers._validators import validate_backend_value


def test_codex_backend_validation_uses_connected_catalog(monkeypatch: pytest.MonkeyPatch):
    state = {
        "enabled": True,
        "connected": True,
        "image_generation": True,
        "models": [{"model": "account-model", "isDefault": True}],
    }
    monkeypatch.setitem(
        PROVIDER_REGISTRY,
        "codex",
        replace(PROVIDER_REGISTRY["codex"], model_source=lambda: codex_models_from_state(state)),
    )

    assert validate_backend_value("codex/account-model", "default_text_backend") is None
    assert validate_backend_value("codex/gpt-image-2", "default_image_backend") is None
    with pytest.raises(BadRequestError):
        validate_backend_value("codex/gpt-image-2", "default_text_backend")
    with pytest.raises(BadRequestError):
        validate_backend_value("codex/unavailable-model", "default_text_backend")
    state["connected"] = False
    with pytest.raises(BadRequestError):
        validate_backend_value("codex/account-model", "default_text_backend")

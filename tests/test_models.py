"""Tests for src/models/models.py — provider dispatch and env-driven config.

These tests do NOT actually instantiate the underlying model clients; they only
exercise the dispatcher and the env-driven helpers, so they don't need
strands/anthropic/openai installed.
"""

import os
from unittest.mock import patch

import pytest

from models import models as models_mod
from models.models import build_model, current_model_id, current_provider


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Clear all model-related env vars before each test."""
    for var in [
        "MODEL_PROVIDER",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ]:
        monkeypatch.delenv(var, raising=False)


class TestCurrentProvider:
    def test_default_is_ollama(self):
        assert current_provider() == "ollama"

    def test_respects_env(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
        assert current_provider() == "anthropic"

    def test_normalizes_case_and_whitespace(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "  OpenAI  ")
        assert current_provider() == "openai"


class TestCurrentModelId:
    def test_ollama_default(self):
        assert current_model_id() == "qwen3:4b"

    def test_anthropic_default(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
        assert "claude" in current_model_id()

    def test_openai_default(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "openai")
        assert "gpt" in current_model_id()

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:8b")
        assert current_model_id() == "llama3.1:8b"


class TestBuildModelDispatch:
    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "bogus")
        with pytest.raises(ValueError, match="Unknown MODEL_PROVIDER"):
            build_model()

    def test_anthropic_requires_api_key(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
        with patch.object(models_mod, "anthropic_model", wraps=models_mod.anthropic_model):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                build_model()

    def test_openai_requires_api_key(self, monkeypatch):
        monkeypatch.setenv("MODEL_PROVIDER", "openai")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            build_model()

    def test_ollama_dispatches_to_factory(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(models_mod, "ollama_model", lambda: sentinel)
        # rebuild PROVIDERS map so it picks up the patched factory
        monkeypatch.setattr(
            models_mod,
            "PROVIDERS",
            {"ollama": models_mod.ollama_model, "anthropic": models_mod.anthropic_model, "openai": models_mod.openai_model},
        )
        assert build_model() is sentinel

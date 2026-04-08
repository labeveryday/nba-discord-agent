"""Model factories for the NBA Discord Agent.

Supports three providers, selected via MODEL_PROVIDER:
    - ollama    (default, local; requires Ollama running)
    - anthropic (requires ANTHROPIC_API_KEY)
    - openai    (requires OPENAI_API_KEY)
"""

from __future__ import annotations

import os


def ollama_model():
    from strands.models.ollama import OllamaModel

    return OllamaModel(
        host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        model_id=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        temperature=float(os.getenv("MODEL_TEMPERATURE", "0.6")),
        top_p=0.95,
    )


def anthropic_model():
    from strands.models.anthropic import AnthropicModel

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic")

    return AnthropicModel(
        client_args={"api_key": api_key},
        model_id=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "4000")),
        params={"temperature": float(os.getenv("MODEL_TEMPERATURE", "0.6"))},
    )


def openai_model():
    from strands.models.openai import OpenAIModel

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai")

    return OpenAIModel(
        client_args={"api_key": api_key},
        model_id=os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07"),
        params={
            "max_completion_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "16000")),
            "temperature": float(os.getenv("MODEL_TEMPERATURE", "0.6")),
        },
    )


PROVIDERS = {
    "ollama": ollama_model,
    "anthropic": anthropic_model,
    "openai": openai_model,
}


def build_model():
    """Construct the model selected by MODEL_PROVIDER (default: ollama)."""
    provider = os.getenv("MODEL_PROVIDER", "ollama").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown MODEL_PROVIDER={provider!r}. "
            f"Choose one of: {', '.join(PROVIDERS)}"
        )
    return PROVIDERS[provider]()


def current_provider() -> str:
    return os.getenv("MODEL_PROVIDER", "ollama").strip().lower()


def current_model_id() -> str:
    """Return the model id for the active provider (for status / alerts)."""
    provider = current_provider()
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "qwen3:4b")
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-5-mini-2025-08-07")
    return "unknown"

"""The one place the app gets its AI connection from, so tests can swap in a fake."""

from __future__ import annotations

from collections.abc import Callable

from app.config import get_settings
from pipeline.ai import AI, AnthropicAI


def default_ai() -> AI:
    settings = get_settings()
    return AnthropicAI(settings.anthropic_api_key, settings.anthropic_model,
                       cache_dir=settings.ai_cache_dir if settings.ai_cache else None)


factory: Callable[[], AI] = default_ai


def get_ai() -> AI:
    return factory()

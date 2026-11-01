"""Provider registry: ``build(settings)`` returns the provider the ``llm:`` section names."""
from __future__ import annotations

from typing import Any

from nicheclock.llm.providers.anthropic import AnthropicProvider
from nicheclock.llm.providers.base import Provider
from nicheclock.llm.providers.claude_code import ClaudeCodeProvider
from nicheclock.llm.providers.codex import CodexProvider
from nicheclock.llm.providers.gemini import GeminiProvider
from nicheclock.llm.providers.ollama import OllamaProvider
from nicheclock.llm.providers.openai import OpenAIProvider
from nicheclock.llm.settings import LLMSettings

REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider, "openai": OpenAIProvider, "gemini": GeminiProvider,
    "ollama": OllamaProvider, "claude-code": ClaudeCodeProvider, "codex": CodexProvider,
}


def build(settings: LLMSettings, **kw: Any) -> Provider:
    """``kw`` (session, sleep) goes to the HTTP providers only."""
    cls = REGISTRY[settings.provider]
    return cls(settings, **kw) if kw and issubclass(cls, _http()) else cls(settings)


def _http() -> type:
    from nicheclock.llm.providers.http import HTTPProvider
    return HTTPProvider

"""Anthropic Messages API (``POST {base}/v1/messages``)."""
from __future__ import annotations

from typing import Any

from nicheclock.llm.errors import ProviderError
from nicheclock.llm.providers.base import Raw, split_system
from nicheclock.llm.providers.http import HTTPProvider
from nicheclock.llm.types import Message

VERSION = "2023-06-01"


class AnthropicProvider(HTTPProvider):
    name = "anthropic"
    default_base = "https://api.anthropic.com"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        system, chat = split_system(messages)
        body: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens,
                                "messages": [{"role": m.role, "content": m.content} for m in chat]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        data = self._post("/v1/messages", {"x-api-key": self._secret(), "anthropic-version": VERSION,
                                           "content-type": "application/json"}, body)
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            u = data.get("usage") or {}
            tin = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) \
                + (u.get("cache_read_input_tokens") or 0)
            return Raw(text, int(tin), int(u.get("output_tokens") or 0))
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("anthropic: unexpected reply shape") from None

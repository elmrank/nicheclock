"""OpenAI Chat Completions (``POST {base}/chat/completions``); any OpenAI-compatible server via base_url."""
from __future__ import annotations

from typing import Any

from nicheclock.llm.errors import ProviderError
from nicheclock.llm.providers.base import Raw
from nicheclock.llm.providers.http import HTTPProvider
from nicheclock.llm.types import Message


class OpenAIProvider(HTTPProvider):
    name = "openai"
    default_base = "https://api.openai.com/v1"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        body: dict[str, Any] = {"model": self.model, "max_completion_tokens": max_tokens,
                                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if temperature is not None:
            body["temperature"] = temperature
        if schema:
            body["response_format"] = {"type": "json_object"}
        key = self._secret()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            data = self._post("/chat/completions", headers, body)
        except ProviderError as e:
            # an endpoint that rejects json mode still answers when asked for plain text
            if schema and "HTTP 400" in str(e) and "response_format" in str(e):
                body.pop("response_format")
                data = self._post("/chat/completions", headers, body)
            else:
                raise
        try:
            text = data["choices"][0]["message"].get("content") or ""
            u = data.get("usage") or {}
            return Raw(text, int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("openai: unexpected reply shape") from None

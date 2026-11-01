"""Ollama (local, ``POST {base}/api/chat``). No key, no cost."""
from __future__ import annotations

from typing import Any

from nicheclock.llm.errors import ProviderError
from nicheclock.llm.providers.base import Raw
from nicheclock.llm.providers.http import HTTPProvider
from nicheclock.llm.types import Message


class OllamaProvider(HTTPProvider):
    name = "ollama"
    metered = False
    default_base = "http://localhost:11434"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        opts: dict[str, Any] = {"num_predict": max_tokens}
        if temperature is not None:
            opts["temperature"] = temperature
        body: dict[str, Any] = {"model": self.model, "stream": False, "options": opts,
                                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if schema:
            body["format"] = "json"
        data = self._post("/api/chat", {}, body)
        try:
            return Raw(data["message"]["content"], int(data.get("prompt_eval_count") or 0),
                       int(data.get("eval_count") or 0))
        except (KeyError, TypeError):
            raise ProviderError("ollama: unexpected reply shape") from None

    def cost(self, raw: Raw) -> float | None:
        return 0.0

"""Google Gemini ``generateContent`` (key in the ``x-goog-api-key`` header, never in the URL)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nicheclock.llm.errors import ProviderError
from nicheclock.llm.providers.base import Raw, split_system
from nicheclock.llm.providers.http import HTTPProvider
from nicheclock.llm.types import Message


class GeminiProvider(HTTPProvider):
    name = "gemini"
    default_base = "https://generativelanguage.googleapis.com"

    def _call(self, messages: list[Message], schema: Any, max_tokens: int, temperature: float | None) -> Raw:
        system, chat = split_system(messages)
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if temperature is not None:
            gen["temperature"] = temperature
        if schema:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {
            "contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                         for m in chat],
            "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        model = self.model.removeprefix("models/")
        data = self._post(f"/v1beta/models/{quote(model, safe='.-_')}:generateContent",
                          {"x-goog-api-key": self._secret()}, body)
        try:
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            u = data.get("usageMetadata") or {}
            out = int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0)
            if not text and cand.get("finishReason") not in (None, "STOP"):
                raise ProviderError(f"gemini: no text (finishReason {cand.get('finishReason')}); "
                                    "thinking models may need a larger max_tokens")
            return Raw(text, int(u.get("promptTokenCount") or 0), out)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("gemini: unexpected reply shape") from None

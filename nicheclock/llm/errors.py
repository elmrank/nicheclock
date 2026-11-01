"""Errors of the model layer, and key redaction.

Every message that leaves this package (exceptions, logs, CLI output) goes through ``redact``:
an API key must never appear in them, whatever the remote end echoes back.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


class LLMError(RuntimeError):
    """Base class; the message is safe to print (secrets redacted)."""


class LLMConfigError(LLMError):
    """The ``llm:`` section is missing something or has a bad value."""


class ProviderUnavailable(LLMError):
    """The provider cannot be used right now: no key, CLI not installed, not signed in."""


class ProviderError(LLMError):
    """A request failed (HTTP status, timeout, CLI exit code, unreadable reply)."""


class InvalidOutput(LLMError):
    """The model answered, but not with JSON that matches the schema. Carries the spent usage."""

    def __init__(self, message: str, result: Any = None) -> None:
        super().__init__(message)
        self.result = result


class BudgetExceeded(LLMError):
    """The next call would push today's estimated spend over ``daily_budget_usd``."""


_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),            # OpenAI / Anthropic style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),          # Google API keys
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]{8,}=*"),
    re.compile(r"(?i)(x-api-key|x-goog-api-key|authorization)\s*[:=]\s*\S+"),
)
MIN_SECRET_LEN = 4


def redact(text: object, secrets: Iterable[str] = ()) -> str:
    """``text`` with every known secret and anything key-shaped replaced by ``***``."""
    out = str(text)
    for s in sorted({s for s in secrets if s and len(s) >= MIN_SECRET_LEN}, key=len, reverse=True):
        out = out.replace(s, "***")
    for p in _PATTERNS:
        out = p.sub(lambda m: (m.group(1) + " ***") if m.lastindex else "***", out)
    return out

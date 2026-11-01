"""Bring-your-own-model layer (see ARCHITECTURE.md): providers, usage ledger with a daily budget, and the
optional tasks (fit, merge, junk, gaps, profile). Without an ``llm:`` section nothing here runs and
the rule-based ranking stands.

Design: a single completion seam with a tolerant JSON parse + re-ask, one provider interface in front
of API and CLI sign-in providers, and provider-pinned model references. Written for nicheclock's
synchronous, store-backed setting.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nicheclock.llm.errors import (BudgetExceeded, InvalidOutput, LLMConfigError, LLMError,
                                   ProviderError, ProviderUnavailable, redact)
from nicheclock.llm.session import LLMSession
from nicheclock.llm.settings import LLMSettings, parse, problems
from nicheclock.llm.types import Message, Result, Usage

__all__ = ["BudgetExceeded", "InvalidOutput", "LLMConfigError", "LLMError", "LLMSession", "LLMSettings",
           "Message", "ProviderError", "ProviderUnavailable", "Result", "Usage", "open_session",
           "parse", "problems", "redact"]


def open_session(cfg: Any, store: Any, project: str = "", **provider_kw: Any) -> LLMSession | None:
    """A session for ``cfg.llm`` (None when no model is configured). ``provider_kw`` (session, sleep)
    is for tests. Raises ``LLMConfigError`` for a bad ``llm:`` section."""
    if not getattr(cfg, "llm", None):
        return None
    from nicheclock.llm.providers import build

    settings = parse(cfg.llm, getattr(cfg, "base_dir", None) or Path.cwd())
    return LLMSession(settings, build(settings, **provider_kw), store, project)

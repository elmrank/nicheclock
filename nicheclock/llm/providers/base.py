"""One interface for every provider: ``complete(messages, schema, max_tokens, temperature) -> Result``."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Sequence

from nicheclock.llm import pricing
from nicheclock.llm.errors import InvalidOutput, LLMError, ProviderError, redact
from nicheclock.llm.jsonutil import extract_json, validate
from nicheclock.llm.settings import LLMSettings
from nicheclock.llm.types import Message, Result, Usage

log = logging.getLogger("nicheclock.llm")

JSON_DIRECTIVE = ("Reply with one JSON value that matches this JSON Schema and nothing else "
                  "(no prose, no code fence):\n")


@dataclass
class Raw:
    """What a provider call returns before JSON handling and pricing."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None        # set by providers that report their own cost


class Provider:
    name = ""
    #: True when tokens are billed per use (budget needs a price); False: local or subscription.
    metered = True

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings
        self.model = settings.model
        self._secrets: list[str] = []

    # -- public ----------------------------------------------------------------------------
    def complete(self, messages: Sequence[Message], schema: dict[str, Any] | None = None,
                 max_tokens: int = 1024, temperature: float | None = None) -> Result:
        msgs = _with_schema(messages, schema) if schema else list(messages)
        t0 = time.monotonic()
        try:
            raw = self._call(msgs, schema, max_tokens, temperature)
        except LLMError as e:
            raise type(e)(self.redact(e)) from None
        except Exception as e:      # never let a library message (it may echo headers) escape unredacted
            raise ProviderError(self.redact(f"{self.name}: {type(e).__name__}: {e}")) from None
        res = Result(text=raw.text, usage=Usage(raw.input_tokens, raw.output_tokens),
                     cost_usd_est=self.cost(raw), provider=self.name, model=self.model,
                     latency_s=round(time.monotonic() - t0, 3))
        log.debug("llm call provider=%s model=%s in=%d out=%d cost=%s %.1fs", self.name, self.model,
                  res.usage.input, res.usage.output, res.cost_usd_est, res.latency_s)
        if schema:
            try:
                value = extract_json(raw.text)
            except ValueError as e:
                raise InvalidOutput(f"reply is not JSON ({e})", res) from None
            errs = validate(value, schema)
            if errs:
                raise InvalidOutput("reply does not match the schema: " + "; ".join(errs), res)
            res.json = value
        return res

    def cost(self, raw: Raw) -> float | None:
        if raw.cost_usd is not None:
            return round(raw.cost_usd, 8)
        return pricing.estimate(self.model, raw.input_tokens, raw.output_tokens,
                                override=self.settings.price_per_mtok)

    def available(self) -> tuple[bool, str]:
        """(usable, reason); never makes a billable call."""
        return True, "ok"

    def redact(self, text: object) -> str:
        return redact(text, self._secrets)

    # -- to implement ----------------------------------------------------------------------
    def _call(self, messages: list[Message], schema: dict[str, Any] | None, max_tokens: int,
              temperature: float | None) -> Raw:
        raise NotImplementedError


def _with_schema(messages: Sequence[Message], schema: dict[str, Any]) -> list[Message]:
    directive = JSON_DIRECTIVE + json.dumps(schema, separators=(",", ":"))
    out = list(messages)
    for i, m in enumerate(out):
        if m.role == "system":
            out[i] = Message("system", m.content + "\n\n" + directive)
            return out
    return [Message("system", directive)] + out


def split_system(messages: Sequence[Message]) -> tuple[str, list[Message]]:
    """(system text, chat messages with consecutive same-role turns merged)."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    chat: list[Message] = []
    for m in messages:
        if m.role == "system":
            continue
        if chat and chat[-1].role == m.role:
            chat[-1] = Message(m.role, chat[-1].content + "\n\n" + m.content)
        else:
            chat.append(m)
    return system, chat

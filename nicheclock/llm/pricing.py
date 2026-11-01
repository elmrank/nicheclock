"""Price table for cost estimates (USD per million tokens, standard tier, no caching/batch discount).

Sources, read on 2026-10-31 (the table is an estimate: providers change prices; override a model with
``llm.price_per_mtok`` and re-check against the pages below):
  * Anthropic: https://platform.claude.com/docs/en/about-claude/pricing  ("Model pricing")
  * OpenAI:    https://platform.openai.com/docs/pricing  (Standard, short-context rows)
  * Google:    https://ai.google.dev/gemini-api/docs/pricing  (Paid tier, Standard)
A model that is not listed has no estimate: ``cost_usd_est`` is None, tokens are still recorded.
Ollama runs on your machine: cost 0. ``claude-code`` reports its own equivalent API cost; ``codex``
reports tokens only, so it is priced from this table when its model is listed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

PRICES_AS_OF = "2026-10-31"


@dataclass(frozen=True)
class Price:
    input: float                     # USD per 1M input tokens
    output: float                    # USD per 1M output tokens
    long_over: int | None = None     # prompts above this many tokens use the long_* prices
    long_input: float | None = None
    long_output: float | None = None

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        long = self.long_over is not None and input_tokens > self.long_over
        pin = self.long_input if long and self.long_input is not None else self.input
        pout = self.long_output if long and self.long_output is not None else self.output
        return (input_tokens * pin + output_tokens * pout) / 1_000_000


# model id -> [(valid through ISO date or None, Price)]: the first entry still valid applies.
_T = dict[str, list[tuple[str | None, Price]]]
PRICES: _T = {
    # Anthropic
    "claude-opus-5-5": [(None, Price(4, 20))],
    "claude-opus-5": [(None, Price(5, 25))],
    "claude-opus-4-8": [(None, Price(5, 25))],
    "claude-opus-4-7": [(None, Price(5, 25))],
    "claude-opus-4-6": [(None, Price(5, 25))],
    "claude-opus-4-5": [(None, Price(5, 25))],
    "claude-sonnet-5-5": [(None, Price(2, 10))],
    "claude-sonnet-5": [(None, Price(2, 10))],
    "claude-sonnet-4-6": [(None, Price(3, 15))],
    "claude-sonnet-4-5": [(None, Price(3, 15))],
    "claude-haiku-5-5": [(None, Price(0.10, 0.50, 100_000, 0.50, 2.50))],
    "claude-haiku-4-5": [(None, Price(1, 5))],
    # OpenAI
    "gpt-6-astra": [(None, Price(10, 50))],
    "gpt-6.1-sol": [(None, Price(2, 10))],
    "gpt-6.1-terra": [(None, Price(1, 5))],
    "gpt-6-luna": [(None, Price(0.10, 0.50))],
    # Google (output includes thinking tokens)
    "gemini-4-argon": [("2026-11-30", Price(2, 10)), (None, Price(4, 20))],
    "gemini-3.8-flash": [("2026-12-31", Price(0.75, 3.75)), (None, Price(1.50, 7.50))],
    "gemini-3.5-flash-lite": [(None, Price(0.30, 2.50))],
    "gemini-3.1-flash-lite": [(None, Price(0.25, 1.50))],
    "gemini-3.1-pro-preview": [(None, Price(2, 12, 200_000, 4, 18))],
    "gemini-2.5-pro": [(None, Price(1.25, 10, 200_000, 2.50, 15))],
    "gemini-2.5-flash": [(None, Price(0.30, 2.50))],
    "gemini-2.5-flash-lite": [(None, Price(0.10, 0.40))],
}
_SUFFIX = re.compile(r"-(\d{8}|latest)$")


def normalize(model: str) -> str:
    m = model.strip().lower().removeprefix("models/")
    m = _SUFFIX.sub("", m)
    if m.startswith("claude-"):
        m = m.replace(".", "-")
    return m


def lookup(model: str, on: date | None = None) -> Price | None:
    entries = PRICES.get(normalize(model))
    if not entries:
        return None
    on = on or date.today()
    for until, price in entries:
        if until is None or on <= date.fromisoformat(until):
            return price
    return None


def estimate(model: str, input_tokens: int, output_tokens: int, *,
             override: tuple[float, float] | None = None, on: date | None = None) -> float | None:
    """USD estimate, or None when the model's price is unknown."""
    price = Price(*override) if override else lookup(model, on)
    return None if price is None else round(price.cost(input_tokens, output_tokens), 8)

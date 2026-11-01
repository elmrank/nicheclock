"""``LLMSession``: a provider plus the daily budget, the usage ledger and the JSON re-ask.

Every model call of nicheclock goes through ``LLMSession.call``:
  1. refuse it when today's estimated spend + the call's worst case would exceed ``daily_budget_usd``;
  2. call the provider; a reply that is not valid JSON for the schema is re-asked once with the error;
  3. record tokens and the estimated cost in the store (``llm_usage``), also for replies that failed
     validation (they were billed).
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from nicheclock.llm import pricing
from nicheclock.llm.errors import (BudgetExceeded, InvalidOutput, LLMError, ProviderError,
                                   ProviderUnavailable)
from nicheclock.llm.providers.base import Provider
from nicheclock.llm.settings import LLMSettings
from nicheclock.llm.types import Message, Result

log = logging.getLogger("nicheclock.llm")
CHARS_PER_TOKEN = 3.0          # pessimistic: real text is ~4 chars/token, so the estimate over-counts
MAX_RETRY_TOKENS = 8000


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd_est: float = 0.0
    unpriced_calls: int = 0
    by_task: dict[str, int] = field(default_factory=dict)


def utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class LLMSession:
    def __init__(self, settings: LLMSettings, provider: Provider, store: Any, project: str = "",
                 day: Callable[[], str] = utc_day) -> None:
        self.settings, self.provider, self.store, self.project = settings, provider, store, project
        self._day = day
        self.totals = Totals()
        self.budget_reached = False
        self.dead = False       # the provider failed (outage, no key): skip the rest of the run

    # -- budget ----------------------------------------------------------------------------
    def spent_today(self) -> float:
        return self.store.llm_spent(self._day())

    def _projected(self, messages: Sequence[Message], max_tokens: int) -> float | None:
        tin = int(sum(len(m.content) for m in messages) / CHARS_PER_TOKEN) + 1
        return pricing.estimate(self.provider.model, tin, max_tokens, override=self.settings.price_per_mtok)

    def check_budget(self, messages: Sequence[Message], max_tokens: int) -> None:
        budget = self.settings.daily_budget_usd
        if budget is None:
            return
        spent = self.spent_today()
        projected = self._projected(messages, max_tokens)
        if projected is None:
            if self.provider.metered:
                self.budget_reached = True
                raise BudgetExceeded(_t("llm.budget_unpriced", provider=self.provider.name,
                                        model=self.provider.model))
            projected = 0.0
        if spent >= budget or spent + projected > budget:
            self.budget_reached = True
            raise BudgetExceeded(_t("llm.budget_reached", spent=spent, budget=budget, projected=projected))

    # -- calls -----------------------------------------------------------------------------
    def _record(self, task: str, res: Result) -> None:
        self.store.llm_record(self._day(), res.provider, res.model, task, res.usage.input,
                              res.usage.output, res.cost_usd_est, self.project)
        t = self.totals
        t.calls += 1
        t.input_tokens += res.usage.input
        t.output_tokens += res.usage.output
        t.by_task[task] = t.by_task.get(task, 0) + 1
        if res.cost_usd_est is None:
            t.unpriced_calls += 1
        else:
            t.cost_usd_est += res.cost_usd_est

    def call(self, task: str, messages: Sequence[Message], schema: dict[str, Any] | None = None,
             max_tokens: int = 1024, temperature: float | None = None, reasks: int = 1,
             check: Callable[[Any], str | None] | None = None) -> Result:
        """One logical call (at most ``1 + reasks`` provider calls). ``check(json)`` returns a problem
        string for a reply that parses but is not acceptable; it triggers the same re-ask."""
        msgs = list(messages)
        tokens = max_tokens
        last: LLMError | None = None
        if self.dead:
            raise ProviderError(f"{self.provider.name} failed earlier in this run; not retrying")
        for attempt in range(reasks + 1):
            self.check_budget(msgs, tokens)
            try:
                res = self.provider.complete(msgs, schema, tokens, temperature)
            except (ProviderError, ProviderUnavailable):
                self.dead = True
                raise
            except InvalidOutput as e:
                if e.result is not None:
                    self._record(task, e.result)
                last = e
                bad = e.result.text if e.result is not None else ""
                problem = str(e)
            else:
                self._record(task, res)
                problem = check(res.json) if (check and schema) else None
                if problem is None:
                    return res
                last = InvalidOutput(problem, res)
                bad = res.text
            if attempt < reasks:
                log.debug("llm task=%s re-ask: %s", task, problem)
                msgs = list(messages) + [
                    Message("assistant", bad[:4000]),
                    Message("user", f"That reply was not usable ({problem}). Reply again with only the "
                                    "corrected JSON, nothing else.")]
                tokens = min(tokens * 2, max(MAX_RETRY_TOKENS, max_tokens))
        assert last is not None
        raise InvalidOutput(f"{task}: {last}", getattr(last, "result", None))

    # -- report ----------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        t = self.totals
        return {"provider": self.provider.name, "model": self.provider.model or "(CLI default)",
                "calls": t.calls, "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
                "cost_usd_est": round(t.cost_usd_est, 6), "unpriced_calls": t.unpriced_calls,
                "budget_reached": self.budget_reached, "tasks": dict(t.by_task)}

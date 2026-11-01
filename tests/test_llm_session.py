"""Price table, usage ledger, daily budget stop, JSON re-ask, redaction through the session."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from llm_fakes import KEY, FakeProvider, fake_session, settings

from nicheclock.llm import (BudgetExceeded, InvalidOutput, ProviderError, ProviderUnavailable, pricing)
from nicheclock.llm.session import LLMSession
from nicheclock.llm.types import messages_of
from nicheclock.store import Store

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
MSGS = messages_of("sys", json.dumps({"q": "x"}))


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "d") as st:
        yield st


def test_price_table_lookup_dates_suffixes_and_unknown():
    assert pricing.lookup("claude-haiku-4-5").input == 1
    assert pricing.lookup("claude-haiku-4-5-20251001").output == 5          # dated snapshot id
    assert pricing.lookup("Claude-Sonnet-4.6").input == 3                    # dots, case
    assert pricing.lookup("claude-sonnet-4-6-experimental") is None          # never guess a neighbour
    assert pricing.lookup("models/gemini-2.5-flash").input == 0.30
    assert pricing.lookup("gemini-4-argon", date(2026, 11, 30)).input == 2
    assert pricing.lookup("gemini-4-argon", date(2026, 12, 1)).input == 4     # announced increase
    assert pricing.estimate("gpt-6-luna", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert pricing.estimate("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)          # long-context tier
    assert pricing.estimate("mystery-model", 10, 10) is None
    assert pricing.estimate("mystery-model", 1_000_000, 1_000_000, override=(1.0, 2.0)) == 3.0
    assert pricing.PRICES_AS_OF == "2026-10-31" and "platform.claude.com" in pricing.__doc__


def test_ledger_records_tokens_cost_and_unknown_price(store):
    s = fake_session(store, lambda p: '{"n": 1}', model="claude-haiku-4-5", tokens=(1000, 200))
    r = s.call("fit", MSGS, SCHEMA)
    assert r.cost_usd_est == pytest.approx(0.002) and r.json == {"n": 1}
    u = fake_session(store, lambda p: "hi", model="mystery-model")
    r2 = u.call("junk", MSGS)
    assert r2.cost_usd_est is None
    rows = store.db.execute("SELECT * FROM llm_usage ORDER BY id").fetchall()
    assert [(r["task"], r["provider"], r["model"], r["input_tokens"], r["output_tokens"], r["cost_usd_est"])
            for r in rows] == [("fit", "fake", "claude-haiku-4-5", 1000, 200, pytest.approx(0.002)),
                               ("junk", "fake", "mystery-model", 1000, 200, None)]
    cols = {c[1] for c in store.db.execute("PRAGMA table_info(llm_usage)")}
    assert {"day", "provider", "model", "task", "input_tokens", "output_tokens", "cost_usd_est"} <= cols
    assert store.llm_spent(rows[0]["day"]) == pytest.approx(0.002)
    agg = store.llm_usage("2000-01-01")
    assert {a["task"]: (a["calls"], a["unpriced"]) for a in agg} == {"fit": (1, 0), "junk": (1, 1)}
    assert s.summary()["cost_usd_est"] == pytest.approx(0.002) and u.summary()["unpriced_calls"] == 1


def test_daily_budget_is_a_hard_stop(store):
    st = settings(daily_budget_usd=0.003)
    s = fake_session(store, lambda p: '{"n": 1}', st=st, model="claude-haiku-4-5", tokens=(1000, 200))
    s.call("fit", MSGS, SCHEMA, max_tokens=100)
    s.call("fit", MSGS, SCHEMA, max_tokens=100)           # 0.002 spent, worst case of the next ~0.0006 still fits
    assert s.spent_today() == pytest.approx(0.004)
    with pytest.raises(BudgetExceeded, match="llm budget reached"):
        s.call("fit", MSGS, SCHEMA, max_tokens=100)
    assert s.budget_reached and len(s.provider.calls) == 2           # the third call never left the machine
    with pytest.raises(BudgetExceeded):
        s.call("fit", MSGS, SCHEMA, max_tokens=100)


def test_budget_refuses_a_call_whose_worst_case_would_overshoot(store):
    st = settings(daily_budget_usd=0.01)
    s = fake_session(store, lambda p: "x", st=st, model="claude-haiku-4-5")
    with pytest.raises(BudgetExceeded):                     # 4000 output tokens x $5/M = $0.02 > $0.01
        s.call("fit", MSGS, max_tokens=4000)
    assert not s.provider.calls
    s.call("fit", MSGS, max_tokens=100)                     # a smaller one fits


def test_budget_resets_on_a_new_day_and_zero_budget_blocks_everything(store):
    day = ["2026-10-30"]
    st = settings(daily_budget_usd=0.003)
    prov = FakeProvider(lambda p: "x", st, model="claude-haiku-4-5")
    s = LLMSession(st, prov, store, "p", day=lambda: day[0])
    s.call("fit", MSGS, max_tokens=50)
    s.call("fit", MSGS, max_tokens=50)
    with pytest.raises(BudgetExceeded):
        s.call("fit", MSGS, max_tokens=50)
    day[0] = "2026-10-31"
    s.budget_reached = False
    s.call("fit", MSGS, max_tokens=50)
    z = fake_session(store, lambda p: "x", st=settings(daily_budget_usd=0), model="claude-haiku-4-5")
    with pytest.raises(BudgetExceeded):
        z.call("fit", MSGS)


def test_budget_needs_a_price_for_metered_providers_but_not_local_or_subscription(store):
    st = settings(daily_budget_usd=1.0)
    s = fake_session(store, lambda p: "x", st=st, model="mystery-model")
    with pytest.raises(BudgetExceeded, match="price_per_mtok"):
        s.call("fit", MSGS)
    priced = fake_session(store, lambda p: "x", st=settings(daily_budget_usd=1.0, price_per_mtok=[1, 2]), model="mystery-model")
    priced.call("fit", MSGS, max_tokens=10)
    local = fake_session(store, lambda p: "x", st=st, model="llama3", metered=False)
    local.call("fit", MSGS)                                   # unmetered: unknown cost, nothing to enforce
    assert local.budget_reached is False


def test_malformed_json_is_re_asked_once_and_every_attempt_is_billed(store):
    replies = iter(["I think it is 3", '{"n": 3}'])
    s = fake_session(store, lambda p: next(replies))
    r = s.call("fit", MSGS, SCHEMA)
    assert r.json == {"n": 3} and len(s.provider.calls) == 2
    retry = s.provider.calls[1]
    assert retry[-2].role == "assistant" and "I think it is 3" in retry[-2].content
    assert retry[-1].role == "user" and "not usable" in retry[-1].content
    assert store.db.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2


def test_still_malformed_after_the_re_ask_raises_invalid_output(store):
    s = fake_session(store, lambda p: "never json")
    with pytest.raises(InvalidOutput, match="fit: .*not JSON"):
        s.call("fit", MSGS, SCHEMA)
    assert len(s.provider.calls) == 2
    assert store.db.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 2


def test_semantic_check_triggers_the_same_re_ask(store):
    replies = iter(['{"n": 99}', '{"n": 2}'])
    s = fake_session(store, lambda p: next(replies))
    r = s.call("fit", MSGS, SCHEMA, check=lambda j: None if j["n"] < 10 else f"n={j['n']} is too big")
    assert r.json == {"n": 2}
    assert "n=99 is too big" in s.provider.calls[1][-1].content


def test_provider_outage_stops_further_calls_this_run(store):
    class Down(FakeProvider):
        def _call(self, *a):
            raise ProviderUnavailable(f"no key {KEY}")

    prov = Down(lambda p: "x")
    s = LLMSession(prov.settings, prov, store, "p")
    with pytest.raises(ProviderUnavailable) as e:
        s.call("fit", MSGS)
    assert KEY not in str(e.value)
    with pytest.raises(ProviderError, match="failed earlier"):
        s.call("fit", MSGS)
    assert s.dead


def test_unexpected_library_error_is_wrapped_and_redacted(store):
    class Weird(FakeProvider):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._secrets.append(KEY)

        def _call(self, *a):
            raise ValueError(f"bad header Authorization: Bearer {KEY}")

    prov = Weird(lambda p: "x")
    s = LLMSession(prov.settings, prov, store, "p")
    with pytest.raises(ProviderError) as e:
        s.call("fit", MSGS)
    assert KEY not in str(e.value) and "ValueError" in str(e.value)


def test_store_migrates_an_old_database(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    db = sqlite3.connect(d / "nicheclock.db")
    db.execute("CREATE TABLE topics (id INTEGER PRIMARY KEY, run_id INTEGER, project TEXT NOT NULL, niche TEXT NOT NULL,"
               " key TEXT NOT NULL, label TEXT NOT NULL, score REAL NOT NULL, why TEXT NOT NULL DEFAULT '', url TEXT,"
               " signals TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
    db.commit()
    db.close()
    with Store(d) as st:
        cols = {c[1] for c in st.db.execute("PRAGMA table_info(topics)")}
        assert {"llm_fit", "llm_reason", "llm_status", "llm_note", "rule_score"} <= cols
        assert st.topics("p") == []
    with Store(d):          # idempotent
        pass

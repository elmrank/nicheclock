"""The model layer wired into run / outputs / MCP / CLI, against a fake model server."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest
import yaml
from integ_helpers import FakeServer
from llm_fakes import KEY, api_reply
from test_pipeline import NOW, ROUTES, _cfg

from nicheclock import pipeline
from nicheclock.cli import main
from nicheclock.config import load_config, save_config, validate_config
from nicheclock.models import FeedItem
from nicheclock.pipeline import run_project
from nicheclock.store import Store
from tests.conftest import make_client, report_data


def model_server(fit=88, junk=(), groups=(), garbage=False):
    """A fake Ollama that answers each task by the system prompt it receives."""
    s = FakeServer()

    def responder(path, js):
        msgs = js["messages"]
        system, user = msgs[0]["content"], msgs[-1]["content"]
        if garbage:
            return api_reply(path, "<<not json>>")
        try:
            p = json.loads(user)
        except ValueError:
            return api_reply(path, "{}")
        if "Search Console gaps" in system:
            out = {"items": [{"id": g["id"], "angle": f"Write about {g['query']}", "questions": ["What is it?", "Is it free?"]}
                             for g in p["gaps"]]}
        elif "group trend topics" in system:
            out = {"groups": [list(g) for g in groups]}
        elif "filter a trend feed" in system:
            out = {"flagged": [{"id": t["id"], "reason": "ephemeral"} for t in p["topics"] if t["title"] in junk]}
        else:
            out = {"items": [{"id": t["id"], "fit": fit, "reason": f"{t['title']} is on topic for bird watching readers"}
                             for t in p["topics"]]}
        return api_reply(path, json.dumps(out), 400, 80)

    s.responder = responder
    return s


def with_llm(cfg, server, **kw):
    cfg.llm = {"provider": "ollama", "model": "fake", "base_url": server.url, **kw}
    return cfg


def run(cfg, **kw):
    client, _ = make_client(ROUTES)
    with Store.open(cfg) as st:
        return run_project(cfg, cfg.projects[0], client=client, store=st, day=NOW.date(), notify=False, **kw)


def test_run_with_model_scores_and_writes_fit_everywhere(tmp_path):
    with model_server(fit=88) as srv:
        cfg = with_llm(_cfg(tmp_path), srv)
        r = run(cfg)
    assert not r.errors, r.errors
    items = r.feed.items
    assert items and all(i.llm_fit == 88 and i.llm_reason and i.rule_score is not None for i in items)
    assert all(i.score == pytest.approx(round(0.4 * i.rule_score + 0.6 * 88, 1)) for i in items)
    assert r.llm["calls"] >= 2 and r.llm["provider"] == "ollama" and r.llm["cost_usd_est"] == 0.0
    out = tmp_path / "out" / "gb"
    feed = json.loads((out / "feed.json").read_text())
    assert feed["items"][0]["llm_fit"] == 88 and "on topic for bird watching readers" in feed["items"][0]["llm_reason"]
    assert feed["llm"]["calls"] == r.llm["calls"] and feed["llm"]["fit_weight"] == 0.6
    md = (out / "feed.md").read_text()
    assert "fit 88: " in md and "Judged by ollama/fake" in md
    page = report_data(out / "index.html")
    assert page["schema"] == 1 and page["topics"] and all(t["fit"] == 88 and t["rule_score"] is not None for t in page["topics"])
    assert "on topic for bird watching readers" in page["topics"][0]["reason"] and page["llm"]["fit_weight"] == 0.6
    assert 'src="../assets/app.js"' in (out / "index.html").read_text() and (tmp_path / "out" / "assets" / "app.js").exists()
    assert "fit 88" in (out / "feed.xml").read_text()
    with Store.open(cfg) as st:
        got = st.topics("gb", "birdwatching")
        assert got and all(t.llm_fit == 88 and t.rule_score is not None for t in got)
        assert st.db.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == r.llm["calls"]
    # a rerun reuses the cached judgements: no second round of fit calls
    n_requests = len(srv.requests)
    with model_server(fit=1) as srv2:
        r2 = run(with_llm(_cfg(tmp_path), srv2))
    assert srv2.requests == [] and all(i.llm_fit == 88 for i in r2.feed.items) and n_requests > 0


def test_junk_is_hidden_and_merge_absorbs(tmp_path):
    base = run(_cfg(tmp_path / "plain"))
    titles = [i.title for i in base.feed.items]
    assert len(titles) >= 2
    with model_server(junk=(titles[0],)) as srv:
        r = run(with_llm(_cfg(tmp_path / "junk"), srv))
    assert titles[0] not in [i.title for i in r.feed.items]
    assert any(f"llm: junk: ephemeral" == i.why and i.title == titles[0] for i in r.feed.low_confidence)
    with Store.open(with_llm(_cfg(tmp_path / "junk"), srv)) as st:
        hidden = {t.label: t.llm_status for t in st.topics("gb", include_hidden=True)}
        assert hidden[titles[0]] == "junk" and titles[0] not in {t.label for t in st.topics("gb")}


def test_malformed_model_output_falls_back_to_rules(tmp_path):
    base = run(_cfg(tmp_path / "plain"))
    with model_server(garbage=True) as srv:
        r = run(with_llm(_cfg(tmp_path / "bad"), srv))
    assert any(e.startswith("llm") for e in r.errors)
    assert [(i.title, i.score) for i in r.feed.items] == [(i.title, i.score) for i in base.feed.items]
    assert all(i.llm_fit is None for i in r.feed.items)


def test_budget_reached_runs_rule_based_and_says_so(tmp_path):
    base = run(_cfg(tmp_path / "plain"))
    with model_server() as srv:
        cfg = _cfg(tmp_path / "cap")
        cfg.llm = {"provider": "openai", "model": "pricey", "base_url": srv.url + "/v1",
                   "price_per_mtok": [1000, 1000], "daily_budget_usd": 0.01}
        r = run(cfg)
    assert srv.requests == [] and not r.errors
    assert any("llm budget reached" in n for n in r.notes)
    assert [(i.title, i.score) for i in r.feed.items] == [(i.title, i.score) for i in base.feed.items]
    assert "llm budget reached" in (tmp_path / "cap" / "out" / "gb" / "feed.md").read_text()
    assert r.llm["budget_reached"] is True and r.llm["calls"] == 0


def test_missing_key_or_bad_config_never_stops_a_run(tmp_path, monkeypatch):
    monkeypatch.delenv("NC_NO_SUCH_KEY", raising=False)
    cfg = _cfg(tmp_path / "a")
    cfg.llm = {"provider": "anthropic", "model": "claude-haiku-4-5", "api_key_env": "NC_NO_SUCH_KEY"}
    r = run(cfg)
    assert r.feed.items and any("NC_NO_SUCH_KEY" in e for e in r.errors)
    assert sum("NC_NO_SUCH_KEY" in e for e in r.errors) == 1            # reported once, not once per task
    cfg2 = _cfg(tmp_path / "b")
    cfg2.llm = {"provider": "telepathy", "model": "x"}
    r2 = run(cfg2)
    assert r2.feed.items and any("llm.provider must be one of" in e for e in r2.errors)


def test_gaps_get_angles_and_questions(tmp_path, monkeypatch):
    gap = FeedItem("gb", None, "gap", "best birdwatching", "https://gb.test/p", "900 impressions, position 12", 900.0)
    monkeypatch.setattr(pipeline, "_gaps", lambda project, store, limit=50: [gap])
    with model_server() as srv:
        r = run(with_llm(_cfg(tmp_path), srv))
    g = r.feed.gaps[0]
    assert g.angle == "Write about best birdwatching" and g.questions == ["What is it?", "Is it free?"]
    feed = json.loads((tmp_path / "out" / "gb" / "feed.json").read_text())
    assert feed["gaps"][0]["questions"] == ["What is it?", "Is it free?"]
    assert "angle: Write about best birdwatching" in (tmp_path / "out" / "gb" / "feed.md").read_text()
    row = report_data(tmp_path / "out" / "gb" / "index.html")["gaps"]["rows"][0]
    assert row["angle"] == "Write about best birdwatching" and row["questions"] == ["What is it?", "Is it free?"]
    sent = [json.loads(q["json"]["messages"][-1]["content"]) for q in srv.requests
            if '"gaps":' in q["json"]["messages"][-1]["content"]]
    assert sent and "gb.test" not in json.dumps(sent) and "900" not in json.dumps(sent)


# -- CLI --------------------------------------------------------------------------------------

def write_cfg(tmp_path, srv, **llm):
    cfg = with_llm(_cfg(tmp_path), srv, **llm)
    cfg.path = tmp_path / "nicheclock.yaml"
    save_config(cfg, cfg.path)
    return cfg.path


def test_llm_test_command_prints_everything_but_the_key(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NC_TEST_KEY", KEY)
    with model_server() as srv:
        f = write_cfg(tmp_path, srv)
        raw = yaml.safe_load(f.read_text())
        raw["llm"] = {"provider": "openai", "model": "gpt-6-luna", "api_key_env": "NC_TEST_KEY",
                      "base_url": srv.url + "/v1", "daily_budget_usd": 1.5}
        f.write_text(yaml.safe_dump(raw))
        srv.responder = lambda path, js: api_reply(path, "ok", 12, 3)
        assert main(["-c", str(f), "llm", "test"]) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "NC_TEST_KEY" in out                       # the env var name is fine, the value never
    for needle in ("provider   openai", "model      gpt-6-luna", "latency", "12 in / 3 out", "$0.000003", "of $1.50 budget"):
        assert needle in out
    assert srv.requests[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_llm_test_reports_failure_without_the_key(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NC_TEST_KEY", KEY)
    f = tmp_path / "nicheclock.yaml"
    f.write_text(yaml.safe_dump({"projects": [{"name": "p", "niches": [{"name": "n", "keywords": ["x"]}]}],
                                 "llm": {"provider": "anthropic", "model": "claude-haiku-4-5", "api_key_env": "NC_TEST_KEY",
                                         "base_url": "http://127.0.0.1:9"}}))
    assert main(["-c", str(f), "llm", "test"]) == 1
    cap = capsys.readouterr()
    assert "llm test failed" in cap.err and KEY not in cap.out + cap.err
    f.write_text(yaml.safe_dump({"projects": [{"name": "p", "niches": [{"name": "n", "keywords": ["x"]}]}]}))
    assert main(["-c", str(f), "llm", "test"]) == 2 and "no `llm:` section" in capsys.readouterr().err
    f.write_text(yaml.safe_dump({"projects": [{"name": "p", "niches": [{"name": "n", "keywords": ["x"]}]}],
                                 "llm": {"provider": "anthropic", "model": "m", "api_key": KEY}}))
    assert main(["-c", str(f), "llm", "test"]) == 2                       # inline keys are refused...
    assert KEY not in capsys.readouterr().err                              # ...and not echoed back


def test_llm_usage_command(tmp_path, capsys):
    with model_server() as srv:
        f = write_cfg(tmp_path, srv, daily_budget_usd=2)
        cfg = load_config(f)
        run(cfg)
        capsys.readouterr()
        assert main(["-c", str(f), "llm", "usage", "--days", "3"]) == 0
    out = capsys.readouterr().out
    assert "fit" in out and "ollama" in out and "fake" in out and "total" in out
    assert "today (UTC): $0.0000 of $2.00 budget" in out


def test_eval_export_writes_side_by_side_csv(tmp_path, capsys):
    with model_server(fit=60) as srv:
        f = write_cfg(tmp_path, srv)
        cfg = load_config(f)
        run(cfg)
        # make the model disagree with the rules so the two columns differ
        with Store.open(cfg) as st:
            st.db.execute("UPDATE topics SET llm_reason='=cmd|evil', llm_fit=5 WHERE id=(SELECT MIN(id) FROM topics)")
            st.db.commit()
        out = tmp_path / "eval.csv"
        assert main(["-c", str(f), "eval", "export", "--project", "gb", "--days", "7", "--top", "5", "--out", str(out)]) == 0
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["rank", "rule_id", "rule_title", "rule_niche", "rule_score", "rule_why", "rule_usable?",
                       "llm_id", "llm_title", "llm_niche", "llm_score", "llm_fit", "llm_reason", "llm_usable?"]
    body = rows[1:]
    assert 1 <= len(body) <= 5 and [r[0] for r in body] == [str(i) for i in range(1, len(body) + 1)]
    assert all(r[6] == "" and r[13] == "" for r in body)               # blank columns to rate
    assert all(r[1].startswith("birdwatching/") and r[2] and r[8] and r[12] for r in body)
    rule_scores = [float(r[4]) for r in body]
    assert rule_scores == sorted(rule_scores, reverse=True)
    llm_scores = [float(r[10]) for r in body]
    assert llm_scores == sorted(llm_scores, reverse=True)
    assert not any(c.startswith("=") for r in body for c in r)           # formulas are neutralised
    assert "usable?" in capsys.readouterr().out


def test_eval_export_needs_model_scored_topics(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    cfg.path = tmp_path / "nicheclock.yaml"
    save_config(cfg, cfg.path)
    run(cfg)
    assert main(["-c", str(cfg.path), "eval", "export"]) == 1
    assert "no topic of 'gb' was scored by a model" in capsys.readouterr().err
    assert not list((tmp_path / "out" / "gb").glob("eval-*.csv"))


def test_config_validation_of_the_llm_section(tmp_path):
    f = tmp_path / "c.yaml"
    base = {"projects": [{"name": "p", "niches": [{"name": "n", "keywords": ["x"]}]}]}
    f.write_text(yaml.safe_dump({**base, "llm": {"provider": "openai", "model": "m"}}))
    assert any("api_key_env" in e for e in validate_config(load_config(f)))
    f.write_text(yaml.safe_dump({**base, "llm": {"provider": "claude-code", "daily_budget_usd": 2,
                                                  "max_items_per_run": 10, "send_gsc_rows": False}}))
    assert validate_config(load_config(f)) == []
    f.write_text(yaml.safe_dump({**base, "llm": "openai"}))
    assert any("llm must be a mapping" in e for e in validate_config(load_config(f)))
    f.write_text(yaml.safe_dump({**base, "projects": [{**base["projects"][0], "profile": {"title": "T", "description": "D", "x": 1}}]}))
    cfg = load_config(f)
    assert cfg.projects[0].profile == {"title": "T", "description": "D"}
    save_config(cfg, tmp_path / "out.yaml")
    assert yaml.safe_load((tmp_path / "out.yaml").read_text())["projects"][0]["profile"]["title"] == "T"


# -- MCP ---------------------------------------------------------------------------------------

def test_mcp_trends_gaps_and_explain_topic(tmp_path):
    pytest.importorskip("mcp", reason="install the mcp extra to run MCP server tests")
    from nicheclock import mcp_server

    gap = FeedItem("gb", None, "gap", "best birdwatching", None, "900 impressions", 900.0)
    with model_server(fit=77) as srv:
        cfg = with_llm(_cfg(tmp_path), srv)
        import nicheclock.pipeline as pl
        orig = pl._gaps
        pl._gaps = lambda project, store, limit=50: [gap]
        try:
            r = run(cfg)
        finally:
            pl._gaps = orig
    n_before = len(srv.requests)
    trends = mcp_server.trends_for_niche(cfg, "gb", "birdwatching")
    assert trends and all(t["fit"] == 77 and "bird watching readers" in t["fit_reason"] and "rule_score" in t for t in trends)
    top = trends[0]
    ex = mcp_server.explain_topic(cfg, "gb", top["title"])
    assert ex["title"] == top["title"] and ex["signals"] and ex["niche_profile"]["keywords"] == ["bird watching", "bird feeder"]
    assert ex["fit"] == 77 and ex["site"]["urls"] == ["https://gb.test"] and "judge_with_your_model" in ex
    assert ex["signals"][0]["source"] and "url" in ex["signals"][0]
    assert len(srv.requests) == n_before                                      # explain_topic never calls a model
    assert mcp_server.explain_topic(cfg, "gb", top["title"].upper(), niche="BIRDWATCHING")["key"] == ex["key"]
    with pytest.raises(mcp_server.ToolError, match="no topic matching"):
        mcp_server.explain_topic(cfg, "gb", "zzz nothing")
    with pytest.raises(mcp_server.ToolError, match="unknown project"):
        mcp_server.explain_topic(cfg, "nope", "x")
    with pytest.raises(mcp_server.ToolError, match="topic must be"):
        mcp_server.explain_topic(cfg, "gb", "  ")
    gaps = mcp_server.gsc_gaps(cfg, "gb")
    assert gaps[0]["angle"] == "Write about best birdwatching" and gaps[0]["reader_questions"] == ["What is it?", "Is it free?"]
    # without any data the read-only server says so instead of creating a store
    empty = _cfg(tmp_path / "empty")
    with pytest.raises(mcp_server.ToolError, match="no data yet"):
        mcp_server.explain_topic(empty, "gb", "x")
    assert not (tmp_path / "empty" / "data").exists()

"""Sources whose terms forbid AI use never reach a model (data-rights review, section 9.4)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
import yaml
from llm_fakes import fake_session, settings
from test_llm_integration import model_server, run, with_llm
from tests.conftest import report_data
from test_llm_tasks import fit_script, multi_script
from test_pipeline import _cfg

from nicheclock.config import Niche, Project, load_config, save_config, validate_config
from nicheclock.llm.denylist import DEFAULT_DENYLIST, NOT_SENT, Denylist, host_of
from nicheclock.llm.enrich import enrich_topics
from nicheclock.models import Signal, Topic
from nicheclock.store import Store

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
REQUIRED = ("news.google.com theguardian.com wired.com arstechnica.com pitchfork.com newyorker.com vanityfair.com "
            "gq.com vogue.com variety.com deadline.com billboard.com rollingstone.com hollywoodreporter.com nme.com "
            "scmp.com pcgamer.com gamesradar.com techradar.com tomshardware.com futurism.com").split()


def test_default_list_has_every_required_domain_and_matches_subdomains_only():
    assert set(REQUIRED) <= set(DEFAULT_DENYLIST)
    d = Denylist()
    for url in ("https://www.wired.com/story/x", "https://news.google.com/rss/articles/abc", "HTTP://M.TheGuardian.com:8443/a",
                "https://uk.pcgamer.com/x", "variety.com", "https://user:pw@deadline.com/x"):
        assert d.denies(url), url
    for url in ("https://notwired.com/x", "https://wired.com.evil.test/x", "https://example.com/wired.com",
                "https://google.com/news", "https://news.ycombinator.com/item?id=1", "", None, "https://trends.google.com/trending"):
        assert not d.denies(url), url
    assert host_of("https://www.Example.com:80/p?q=1") == "example.com"
    assert Denylist(["https://Blog.Example.org/path"]).denies("https://x.blog.example.org/a")


def sig(source, term, url=None, **extra):
    return Signal(source, term, url, rank=1, seen_at=NOW, extra=extra)


def topic(label, score, *signals):
    t = Topic(key=label.lower(), label=label, niche="birdwatching", score=score, signals=list(signals))
    t.why = f"evidence for {label}"
    return t


def test_reason_checks_item_url_gdelt_domain_feed_and_off_feeds():
    d = Denylist()
    assert d.reason(topic("a", 1, sig("hn", "a", "https://www.theguardian.com/x"))) == NOT_SENT       # HN story pointing there
    assert d.reason(topic("b", 1, sig("gdelt", "b", "https://tracker.test/r?u=1", domain="variety.com"))) == NOT_SENT
    assert d.reason(topic("c", 1, sig("rss", "c", "https://elsewhere.test/p", feed="https://www.nme.com/feed"))) == NOT_SENT
    assert d.reason(topic("d", 1, sig("rss", "d", "https://blog.test/p", feed="https://blog.test/feed"))) is None
    assert d.reason(topic("d", 1, sig("rss", "d", "https://blog.test/p", feed="https://blog.test/feed")),
                    ["https://blog.test/feed"]) == NOT_SENT
    mixed = topic("e", 1, sig("hn", "e", "https://ok.test/x"), sig("gdelt", "e too", "https://www.wired.com/y"))
    assert d.reason(mixed) == NOT_SENT                                  # one denied signal holds back the whole topic
    assert d.reason(topic("f", 1, sig("google_trends", "f", "https://trends.google.com/trending?geo=US"))) is None


@pytest.fixture
def project():
    n = Niche("birdwatching", keywords=["bird watching"], entities=["Merlin Bird ID"], llm_off_feeds=["https://private.test/feed"])
    return Project("gb", ["https://gb.test"], [n])


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "d") as st:
        yield st


def test_blocked_topics_stay_in_the_feed_unscored_and_never_enter_a_prompt(store, project):
    ok = topic("Snow Goose demo", 80, sig("hn", "Snow Goose demo", "https://ok.test/a"))
    g = topic("Guardian bird watching piece", 90, sig("hn", "Guardian bird watching piece", "https://www.theguardian.com/birds/x"))
    v = topic("Variety bird watching deal", 85, sig("gdelt", "Variety bird watching deal", "https://t.test/r", domain="variety.com"))
    f = topic("Private feed item", 70, sig("rss", "Private feed item", "https://x.test/1", feed="https://private.test/feed"))
    mine = topic("Custom blocked story", 60, sig("hn", "Custom blocked story", "https://news.badsite.example/a"))
    ts = {"birdwatching": [g, v, ok, f, mine]}
    s = fake_session(store, multi_script({}), st=settings(denylist=["badsite.example"]))
    rep = enrich_topics(s, project, ts, NOW)
    assert rep.blocked == 4
    for t in (g, v, f, mine):
        assert t.llm_note == NOT_SENT and t.llm_fit is None and t.llm_status is None      # stays visible, no fit score
    assert ok.llm_fit is not None and ok.llm_note is None
    sent = " ".join(m.content for call in s.provider.calls for m in call)
    for banned in ("Guardian bird watching piece", "Variety bird watching deal", "Private feed item", "Custom blocked story",
                   "theguardian", "variety.com", "badsite"):
        assert banned not in sent, banned
    assert "Snow Goose demo" in sent
    assert g.score == pytest.approx(round(0.4 * 90 + 0.6 * 50, 1))                          # neutral prior, ranking only


def test_a_run_with_only_blocked_topics_makes_no_model_call(store, project):
    g = topic("Guardian piece", 90, sig("hn", "Guardian piece", "https://www.theguardian.com/x"))
    s = fake_session(store, multi_script({}))
    rep = enrich_topics(s, project, {"birdwatching": [g]}, NOW)
    assert s.provider.calls == [] and rep.blocked == 1 and g.llm_note == NOT_SENT


def test_end_to_end_feed_marks_denied_items_in_every_output(tmp_path):
    base = run(_cfg(tmp_path / "plain"))
    pick = next(i for i in base.feed.items if i.url and urlsplit(i.url).hostname)
    dom = urlsplit(pick.url).hostname
    with model_server(fit=80) as srv:
        cfg = with_llm(_cfg(tmp_path / "deny"), srv, denylist=[dom])
        r = run(cfg)
    item = next(i for i in r.feed.items if i.title == pick.title)
    assert item.llm_fit is None and item.llm_note == NOT_SENT
    assert all(pick.title not in q["json"]["messages"][-1]["content"] for q in srv.requests)
    out = tmp_path / "deny" / "out" / "gb"
    feed = json.loads((out / "feed.json").read_text())
    assert next(i for i in feed["items"] if i["title"] == pick.title)["llm_note"] == NOT_SENT
    assert feed["llm"]["not_sent"] >= 1
    assert NOT_SENT in (out / "feed.md").read_text() and NOT_SENT in json.dumps(report_data(out / "index.html")["topics"])
    assert NOT_SENT in (out / "feed.xml").read_text()
    with Store.open(cfg) as st:
        assert {t.label: t.llm_note for t in st.topics("gb")}[pick.title] == NOT_SENT


def test_per_feed_llm_false_in_the_config_round_trips(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump({"projects": [{"name": "p", "niches": [{
        "name": "n", "keywords": ["x"], "sources": ["rss"],
        "rss": ["https://a.test/feed", {"url": "https://b.test/feed", "llm": False}, {"url": "https://c.test/feed", "llm": True}],
        "watch": [{"url": "https://w.test/feed", "llm": False}]}]}],
        "llm": {"provider": "ollama", "model": "m", "denylist": ["example.org"]}}))
    cfg = load_config(f)
    n = cfg.projects[0].niches[0]
    assert n.rss == ["https://a.test/feed", "https://b.test/feed", "https://c.test/feed"]     # still plain URL lists
    assert n.watch == ["https://w.test/feed"] and n.llm_off_feeds == ["https://b.test/feed", "https://w.test/feed"]
    assert validate_config(cfg) == []
    save_config(cfg, tmp_path / "o.yaml")
    raw = yaml.safe_load((tmp_path / "o.yaml").read_text())["projects"][0]["niches"][0]
    assert raw["rss"] == ["https://a.test/feed", {"url": "https://b.test/feed", "llm": False}, "https://c.test/feed"]
    assert raw["watch"] == [{"url": "https://w.test/feed", "llm": False}]
    assert load_config(tmp_path / "o.yaml").projects[0].niches[0].llm_off_feeds == n.llm_off_feeds
    bad = yaml.safe_load(f.read_text())
    bad["llm"]["denylist"] = "wired.com"
    f.write_text(yaml.safe_dump(bad))
    assert any("denylist must be a list" in e for e in validate_config(load_config(f)))


def test_mcp_explain_topic_withholds_evidence_of_denied_sources(tmp_path):
    pytest.importorskip("mcp", reason="install the mcp extra to run MCP server tests")
    from nicheclock import mcp_server

    cfg = _cfg(tmp_path)
    cfg.llm = {"provider": "ollama", "model": "m", "denylist": ["badsite.example"]}
    with Store.open(cfg) as st:
        t = topic("Guardian bird watching piece", 90, sig("hn", "Guardian bird watching piece", "https://www.theguardian.com/birds/x"))
        u = topic("Fine story", 50, sig("hn", "Fine story", "https://ok.test/a"))
        w = topic("Custom blocked", 40, sig("hn", "Custom blocked", "https://x.badsite.example/a"))
        for x in (t, u, w):
            x.niche = "birdwatching"
        t.llm_note = NOT_SENT                      # what a run records
        st.save_topics(None, "gb", "birdwatching", [t, u, w])
    for title in ("Guardian bird watching piece", "Custom blocked"):
        ex = mcp_server.explain_topic(cfg, "gb", title)
        assert ex["llm_note"] == NOT_SENT and "signals" not in ex and "judge_with_your_model" not in ex
    ok = mcp_server.explain_topic(cfg, "gb", "Fine story")
    assert ok["signals"] and "judge_with_your_model" in ok
    listed = {x["title"]: x for x in mcp_server.trends_for_niche(cfg, "gb", "birdwatching")}
    assert listed["Guardian bird watching piece"]["llm_note"] == NOT_SENT and "llm_note" not in listed["Fine story"]

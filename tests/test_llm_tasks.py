"""The five tasks, enrichment (blend, hidden topics, caps), data minimisation and fallbacks."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from llm_fakes import fake_session, settings

from nicheclock.config import Niche, Project
from nicheclock.llm.enrich import UNJUDGED_FIT, blend, enrich_gaps, enrich_topics, EnrichReport
from nicheclock.llm.tasks import fit, gaps, junk, merge, profile
from nicheclock.llm.tasks.common import TaskReport, site_profile
from nicheclock.models import FeedItem, Signal, Topic
from nicheclock.store import Store

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
GSC_ROWS = [
    {"date": "2026-10-20", "query": "best birdwatching", "page": "https://site.test/secret-page", "clicks": 17,
     "impressions": 4321, "ctr": 0.004, "position": 8.4, "window": "cur"},
    {"date": "2026-10-20", "query": "bird feeder pc", "page": "https://site.test/other", "clicks": 3,
     "impressions": 900, "ctr": 0.003, "position": 15.2, "window": "cur"},
    {"date": "2026-10-01", "query": "old query", "page": "https://site.test/old", "clicks": 1, "impressions": 5,
     "ctr": 0.2, "position": 3.0, "window": "prev"},
]


def topic(label, score, source="hn", niche="birdwatching", url="https://news.test/x", **extra):
    t = Topic(key=label.lower(), label=label, niche=niche, score=score,
              signals=[Signal(source, label, url, rank=1, seen_at=NOW, extra=extra)])
    t.why = f"{source} evidence for {label}"
    return t


@pytest.fixture
def project():
    n = Niche("birdwatching", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID"], exclude=["tips"],
              formats=["tier list"])
    return Project("gb", ["https://gb.test"], [n], profile={"title": "Birdnotes", "description": "Bird watching game news"})


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "d") as st:
        yield st


def fit_script(fits):
    """Reply with fits[title] (default 70) for every topic of a fit prompt."""
    def script(p):
        return json.dumps({"items": [{"id": t["id"], "fit": fits.get(t["title"], 70), "reason": f"because {t['title']}"}
                                     for t in p["topics"]]})
    return script


def test_fit_scores_topics_with_reasons_and_payload_has_titles_snippets_only(store, project):
    ts = [topic("Snow Goose demo", 40, text="A bird watching demo went live today."), topic("Weather in Oslo", 55)]
    s = fake_session(store, fit_script({"Snow Goose demo": 92, "Weather in Oslo": 8}))
    rep = TaskReport()
    got = fit.judge(s, site_profile(project, project.niches[0], store, s.settings), ts, rep)
    assert got["snow goose demo"] == (92.0, "because Snow Goose demo") and got["weather in oslo"][0] == 8.0
    sent = s.provider.calls[0][-1].content
    p = json.loads(sent)
    assert p["site"]["title"] == "Birdnotes" and p["site"]["niche"]["keywords"] == ["bird watching", "bird feeder"]
    assert p["topics"][0]["snippets"][0].startswith("hn: Snow Goose demo") and "bird watching demo went live" in sent
    assert "https://" not in sent                                   # no URLs leave the machine
    assert "fit" in s.provider.calls[0][0].content and "0-100" in s.provider.calls[0][0].content   # versioned prompt file


def test_fit_is_cached_so_a_rerun_costs_nothing(store, project):
    ts = [topic("A", 40), topic("B", 30)]
    s = fake_session(store, fit_script({}))
    prof = site_profile(project, project.niches[0], store, s.settings)
    fit.judge(s, prof, ts, TaskReport())
    rep = TaskReport()
    fit.judge(s, prof, ts, rep)
    assert len(s.provider.calls) == 1 and rep.cached == 2
    fit.judge(s, {**prof, "title": "changed"}, ts, TaskReport())         # a different site profile re-judges
    assert len(s.provider.calls) == 2


def test_fit_batches_and_unknown_ids_trigger_a_re_ask(store, project):
    ts = [topic(f"T{i}", 50 - i) for i in range(15)]
    replies, seen = [], {}

    def script(p):
        seen.update(p if "topics" in p else {})              # a re-ask message is plain text
        replies.append(len(seen["topics"]))
        if len(replies) == 1:
            return json.dumps({"items": [{"id": 99, "fit": 50, "reason": "x"}]})        # invalid ids
        return fit_script({})(seen)

    s = fake_session(store, script)
    got = fit.judge(s, {}, ts, TaskReport())
    assert len(got) == 15 and replies == [12, 12, 3]                    # 12 + re-ask of the same 12, then 3


def test_fit_malformed_batch_is_skipped_and_reported(store, project):
    s = fake_session(store, lambda p: "no json")
    rep = TaskReport()
    assert fit.judge(s, {}, [topic("A", 1)], rep) == {}
    assert rep.errors and "skipped a batch" in rep.errors[0]


def test_junk_flags_only_what_the_model_names(store, project):
    ts = [topic("Wordle answer today", 50), topic("Snow Goose review", 60)]
    s = fake_session(store, lambda p: json.dumps({"flagged": [{"id": 1, "reason": "daily puzzle answer"}]}))
    rep = TaskReport()
    out = junk.flag(s, {}, ts, rep)
    assert out == {"wordle answer today": "daily puzzle answer"}
    out2 = junk.flag(s, {}, ts, TaskReport())                           # cached, including the "fine" one
    assert out2 == out and len(s.provider.calls) == 1
    assert "evidence" not in s.provider.calls[0][-1].content


def test_merge_groups_same_story_and_validates(store):
    ts = [topic("Osprey 2 launch date", 80, "hn"), topic("Osprey II release date announced", 60, "google_trends"),
          topic("Silksong patch", 50)]
    s = fake_session(store, lambda p: json.dumps({"groups": [[1, 2]]}))
    g = merge.groups(s, ts, TaskReport())
    assert [[t.label for t in grp] for grp in g] == [["Osprey 2 launch date", "Osprey II release date announced"]]
    assert "https" not in s.provider.calls[0][-1].content
    again = merge.groups(s, ts, TaskReport())
    assert len(s.provider.calls) == 1 and len(again) == 1               # cached by topic keys
    bad = fake_session(store, lambda p: json.dumps({"groups": [[1, 2], [2, 3]]}), project="other")
    rep = TaskReport()
    assert merge.groups(bad, ts, rep) == [] and rep.errors             # an id in two groups twice: skipped
    assert merge.groups(s, ts[:1], TaskReport()) == []


def test_gaps_angle_and_questions_and_minimisation(store, project):
    store.add_gsc_rows("gb", GSC_ROWS)
    g = [FeedItem("gb", None, "gap", "best birdwatching", "https://site.test/secret-page",
                  "4,321 impressions, position 8.4", 4321.0)]
    script = lambda p: json.dumps({"items": [{"id": 1, "angle": "Rank the best birdwatching of the year",
                                              "questions": ["Which are free?", "Which work offline?"]}]})
    s = fake_session(store, script)
    out = gaps.advise(s, site_profile(project, None, store, s.settings), g, TaskReport())
    assert out["best birdwatching"] == ("Rank the best birdwatching of the year", ["Which are free?", "Which work offline?"])
    sent = s.provider.calls[0][-1].content
    assert "best birdwatching" in sent
    for secret in ("secret-page", "4321", "4,321", "8.4", "https://site.test"):
        assert secret not in sent                                       # default: query strings only
    rows = fake_session(store, script, st=settings(send_gsc_rows=True), project="rows")
    gaps.advise(rows, site_profile(project, None, store, rows.settings), g, TaskReport())
    sent = rows.provider.calls[0][-1].content
    assert "secret-page" in sent and "4321" in sent and "8.4" in sent   # opt-in: the rows go along


def test_site_profile_sends_gsc_queries_not_rows_unless_enabled(store, project):
    store.add_gsc_rows("gb", GSC_ROWS)
    p = site_profile(project, project.niches[0], store, settings())
    assert p["top_queries"] == ["best birdwatching", "bird feeder pc"]      # current window only, by impressions
    dumped = json.dumps(p)
    assert "secret-page" not in dumped and "4321" not in dumped and "gsc_rows" not in p
    q = site_profile(project, project.niches[0], store, settings(send_gsc_rows=True))
    assert q["gsc_rows"][0] == {"query": "best birdwatching", "page": "https://site.test/secret-page", "clicks": 17,
                                "impressions": 4321, "position": 8.4}
    assert "top_queries" not in site_profile(project, None, None, settings())          # no store, no queries


def test_profile_proposes_niches_from_titles_and_slugs(store):
    pages = [(f"Page {i} about snowy owl bosses", f"guides/boss-{i}") for i in range(80)]
    reply = {"niches": [{"name": "Raptor watching", "keywords": ["Snowy Owl", "boss guide", "snowy owl"],
                         "formats": ["guide"], "exclude": ["free download"]}]}
    s = fake_session(store, lambda p: json.dumps(reply))
    out = profile.propose(s, {"host": "x.test"}, pages)
    assert out == [{"name": "Raptor watching", "keywords": ["snowy owl", "boss guide"], "formats": ["guide"],
                    "exclude": ["free download"]}]
    sent = json.loads(s.provider.calls[0][-1].content)
    assert len(sent["pages"]) == 50 and set(sent["pages"][0]) == {"title", "slug"}      # at most 50, titles + slugs


# -- enrichment ------------------------------------------------------------------------------------

def test_blend_formula():
    assert blend(80, 20, 0.6) == 44.0 and blend(50, 90, 0.0) == 50.0 and blend(10, 100, 1.0) == 100.0


def multi_script(fits=None, junk_titles=(), groups=None):
    fits = fits or {}

    def script(p):
        if "gaps" in p:
            return json.dumps({"items": [{"id": g["id"], "angle": f"angle for {g['query']}", "questions": ["q1?", "q2?"]}
                                         for g in p["gaps"]]})
        ts = p["topics"]
        if "site" not in p:        # merge: titles + sources only
            return json.dumps({"groups": groups or []})
        if ts and "evidence" in ts[0]:
            return fit_script(fits)(p)
        return json.dumps({"flagged": [{"id": t["id"], "reason": "spam"} for t in ts if t["title"] in junk_titles]})
    return script


def test_enrich_blends_hides_junk_merges_and_keeps_rule_score(store, project):
    a = topic("Osprey 2 release date", 80, "hn")
    b = topic("Osprey II release date announced", 60, "google_trends")
    c = topic("Cracked apps free download", 70)
    d = topic("Snow Goose review", 50)
    ts = {"birdwatching": [a, c, b, d]}
    s = fake_session(store, multi_script({"Osprey 2 release date": 90, "Snow Goose review": 30},
                                         junk_titles=("Cracked apps free download",), groups=[[1, 2]]))
    # junk runs first, then merge over what is left, then fit
    s.provider.script = multi_script({"Osprey 2 release date": 90, "Snow Goose review": 30},
                                     junk_titles=("Cracked apps free download",), groups=[[1, 2]])
    rep = enrich_topics(s, project, ts, NOW)
    assert c.llm_status == "junk" and c.llm_note == "spam" and rep.junk == 1
    assert {a.llm_status, b.llm_status, d.llm_status} == {None, "merged"}
    primary, absorbed = (a, b) if a.llm_status is None else (b, a)
    assert absorbed.llm_note.startswith("merged into") and "also reported as" in primary.llm_note and rep.merged == 1
    assert len(primary.signals) == 2 and "merged:" in primary.why
    assert primary.llm_fit == 90 and primary.llm_reason
    assert primary.score == blend(primary.rule_score, 90, 0.6) and primary.rule_score != primary.score
    assert d.llm_fit == 30 and d.score == blend(50, 30, 0.6) == 38.0
    assert absorbed.llm_fit is None
    assert not rep.errors and not rep.notes


def test_enrich_respects_max_items_and_scores_unjudged_with_a_neutral_prior(store, project):
    ts = {"birdwatching": [topic(f"T{i}", 90 - i) for i in range(6)]}
    s = fake_session(store, multi_script({}), st=settings(max_items_per_run=2, tasks=["fit"]))
    enrich_topics(s, project, ts, NOW)
    judged = [t for t in ts["birdwatching"] if t.llm_fit is not None]
    assert [t.label for t in judged] == ["T0", "T1"]                     # the top of the rule ranking
    t5 = ts["birdwatching"][5]
    assert t5.llm_fit is None and t5.score == blend(85, UNJUDGED_FIT, 0.6)
    assert len(s.provider.calls) == 1                                    # only the fit task ran


def test_enrich_budget_stop_keeps_rule_scores_and_reports(store, project):
    ts = {"birdwatching": [topic("A", 70), topic("B", 60)]}
    st = settings(daily_budget_usd=0.0001)
    s = fake_session(store, multi_script({}), st=st, model="claude-haiku-4-5")
    rep = enrich_topics(s, project, ts, NOW)
    assert not s.provider.calls and "llm budget reached" in rep.notes[0]
    assert [t.score for t in ts["birdwatching"]] == [70, 60] and all(t.llm_fit is None for t in ts["birdwatching"])
    assert all(t.rule_score == t.score for t in ts["birdwatching"])


def test_enrich_malformed_model_output_falls_back_to_rules(store, project):
    ts = {"birdwatching": [topic("A", 70), topic("B", 60)]}
    s = fake_session(store, lambda p: "<<garbage>>")
    rep = enrich_topics(s, project, ts, NOW)
    assert [t.score for t in ts["birdwatching"]] == [70, 60]
    assert rep.errors and all("llm" in e for e in rep.errors)
    assert all(t.llm_status is None for t in ts["birdwatching"])


def test_enrich_gaps_fills_angle_and_questions(store, project):
    g = [FeedItem("gb", None, "gap", "bird treaty act checklist", None, "100 impressions", 100.0),
         FeedItem("gb", None, "gap", "second", None, "50 impressions", 50.0)]
    s = fake_session(store, multi_script())
    rep = EnrichReport()
    enrich_gaps(s, project, g, rep)
    assert g[0].angle == "angle for bird treaty act checklist" and g[0].questions == ["q1?", "q2?"] and rep.gaps == 2
    off = fake_session(store, multi_script(), st=settings(tasks=["fit"]))
    g2 = [FeedItem("gb", None, "gap", "x", None, "", 1.0)]
    enrich_gaps(off, project, g2, EnrichReport())
    assert g2[0].angle is None and not off.provider.calls


def test_hidden_topics_are_stored_but_not_listed_and_fields_round_trip(store):
    t = topic("Kept", 70)
    t.rule_score, t.llm_fit, t.llm_reason = 60.0, 90.0, "fits well"
    j = topic("Junked", 65)
    j.llm_status, j.llm_note = "junk", "spam"
    store.save_topics(1, "gb", "birdwatching", [t, j])
    shown = store.topics("gb", since=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert [x.label for x in shown] == ["Kept"] and shown[0].llm_fit == 90.0 and shown[0].rule_score == 60.0
    allr = store.topics("gb", since=datetime(2020, 1, 1, tzinfo=timezone.utc), include_hidden=True)
    assert {x.label: x.llm_status for x in allr} == {"Kept": None, "Junked": "junk"}
    assert Topic.from_dict(shown[0].to_dict()).llm_reason == "fits well"
    assert "llm_fit" not in topic("Plain", 1).to_dict()                  # no model: the serialised form is unchanged

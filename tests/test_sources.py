from datetime import date, datetime, timezone

import pytest

from nicheclock.config import Niche
from nicheclock.match import match_text, relevant
from nicheclock.sources import (apple, attribution, federal_register, gdelt, github, google_trends, hn,
                                ics, mediawiki, rss, wikipedia)
from tests.conftest import fixture_text, make_client

DAY = date(2026, 10, 31)
NOW = datetime(2026, 10, 31, 8, tzinfo=timezone.utc)


def test_every_source_carries_terms():
    for name, (url, note) in attribution().items():
        assert url.startswith("https://") and len(note) > 30, name


def test_no_forbidden_source_modules():
    import pkgutil
    import nicheclock.sources as pkg
    names = {m.name for m in pkgutil.iter_modules(pkg.__path__)}
    for bad in ("reddit", "arctic_shift", "producthunt", "github_trending", "amazon"):
        assert not any(bad in n for n in names)


def test_google_trends_parse():
    sigs = google_trends.parse(fixture_text("google_trends.xml"), "US")
    assert [s.term for s in sigs][:2] == ["Chiefs vs Bills", "Merlin Bird ID update"]
    assert sigs[0].score == 500_000 and sigs[0].rank == 1
    assert sigs[1].extra["news"][0]["title"].startswith("Cornell Lab")
    assert sigs[3].score == 1000


def test_google_trends_collect(niche):
    c, ses = make_client([("trending/rss?geo=US", "google_trends.xml")])
    sigs = google_trends.collect(c, niche, DAY)
    assert len(sigs) == 4 and "geo=US" in ses.calls[-1]
    kept = relevant(sigs, niche)
    # "tips" is excluded, "Chiefs vs Bills" matches nothing
    assert [s.term for s in kept] == ["Merlin Bird ID update", "Bird Feeder night"]


def test_wikipedia_top_and_series():
    import json
    top = wikipedia.parse_top(json.loads(fixture_text("wiki_top.json")))
    assert [t for _, t, _ in top] == ["Merlin Bird ID (app)", "Chiefs",
                                      "Bird feeder (device)", "Bird migration"]
    rising = wikipedia.parse_series(json.loads(fixture_text("wiki_series_rising.json")))
    recent, base, ratio = wikipedia.growth(rising)
    assert (round(recent), base, round(ratio, 2)) == (320, 100, 3.2)
    assert wikipedia.growth(wikipedia.parse_series(json.loads(fixture_text("wiki_series_flat.json"))))[2] == 1.0


def test_wikipedia_collect_entities_and_top(niche):
    niche.sources = ["wikipedia"]
    niche.entities = ["Merlin Bird ID (app)", "Osprey 2"]
    c, ses = make_client([
        ("/top/en.wikipedia/all-access/2026/10/30", (404, "")),
        ("/top/en.wikipedia/all-access/2026/10/29", "wiki_top.json"),
        ("w/api.php", "mediawiki_query.json"),
        ("per-article/en.wikipedia.org/all-access/user/Merlin_Bird_ID_%28app%29", "wiki_series_rising.json"),
        ("per-article/en.wikipedia.org/all-access/user/Osprey_II", "wiki_series_flat.json"),
        ("per-article", "wiki_series_flat.json"),
    ])
    sigs = {s.term: s for s in wikipedia.collect(c, niche, DAY)}
    itt = sigs["Merlin Bird ID (app)"]
    assert itt.rank == 3 and itt.extra["ratio"] == 3.2 and itt.extra["entity"]
    assert "Osprey II" not in sigs               # flat series is no signal
    kept = relevant(list(sigs.values()), niche)
    assert "Chiefs" not in [s.term for s in kept]


def test_mediawiki_parse_query():
    import json
    out = mediawiki.parse_query(json.loads(fixture_text("mediawiki_query.json")),
                                ["Merlin Bird ID (app)", "osprey 2", "Mercury", "Nope Nope"])
    assert out["osprey 2"].title == "Osprey II" and out["osprey 2"].description == "2024 mobile app"
    assert out["Mercury"].disambiguation and out["Nope Nope"] is None
    assert out["Merlin Bird ID (app)"].url.endswith("Merlin_Bird_ID_%28app%29") or \
        out["Merlin Bird ID (app)"].url.endswith("Merlin_Bird_ID_(app)")


def test_hn_collect(niche):
    c, ses = make_client([("hn.algolia.com", "hn.json")])
    sigs = hn.collect(c, niche, DAY)
    assert [s.term for s in sigs] == ["Show HN: A local bird watching journal", "Ask HN: best bird feeder tips?"]
    assert sigs[0].url == "https://ex.test/engine" and sigs[1].url.startswith("https://news.ycombinator.com/item")
    assert sigs[0].rank == 1 and sigs[0].score == 312
    assert sum("search_by_date" in u for u in ses.calls) == 3      # one per keyword/entity
    kept = relevant(sigs, niche)          # queried -> relevant, but "tips" excluded
    assert [s.term for s in kept] == ["Show HN: A local bird watching journal"]


def test_github_collect(niche):
    c, ses = make_client([("api.github.com/search/repositories", "github.json")])
    sigs = github.collect(c, niche, DAY)
    assert sigs[0].term == "foo/birding-log" and sigs[0].score == 340 and sigs[0].extra["language"] == "Python"
    assert "created%3A%3E2026-10-01" in ses.calls[-1] or "created:>2026-10-01" in ses.calls[-1]
    assert [s.term for s in relevant(sigs, niche)] == ["foo/birding-log"]   # strict: description says bird watching
    assert "in%3Aname%2Cdescription" in ses.calls[-1] or "in:name,description" in ses.calls[-1]


def test_apple_parse(niche):
    import json
    sigs = apple.parse(json.loads(fixture_text("apple.json")), "top-free", "us", NOW)
    assert sigs[0].term == "Among Us" and sigs[0].rank == 1 and sigs[0].extra["genres"] == ["Games"]
    niche.keywords = ["among us"]
    assert [s.term for s in relevant(sigs, niche)] == ["Among Us"]


def test_gdelt_spike_and_articles(niche):
    import json
    vals = gdelt.parse_timeline(json.loads(fixture_text("gdelt_timeline.json")))
    recent, base, ratio = gdelt.spike(vals)
    assert round(ratio, 2) == 3.5
    arts = gdelt.parse_articles(json.loads(fixture_text("gdelt_artlist.json")))
    assert arts[0]["created_at"].startswith("2026-10-30T12:00") and arts[0]["domain"] == "news.example"
    c, ses = make_client([("mode=timelinevol", "gdelt_timeline.json"), ("mode=artlist", "gdelt_artlist.json")])
    sigs = gdelt.collect(c, niche, DAY)
    spikes = [s for s in sigs if s.extra.get("metric")]
    arts = [s for s in sigs if not s.extra.get("metric")]
    assert spikes and spikes[0].extra["ratio"] == 3.5
    assert [a.term for a in arts][0].startswith("Bird feeder") and arts[0].url == "https://news.example/a"
    q = [u for u in ses.calls if "mode=artlist" in u][0]
    assert "OR" in q and "sort=datedesc" in q and "timespan=7d" in q       # 3 phrases OR-joined, 7 days
    kept = relevant(arts, niche)                                           # strict: the title must match
    assert [a.term for a in kept] == ["Bird feeder sales rebound as bird watching grows"]


def test_gdelt_skipped_once_rate_limited(niche):
    c, ses = make_client([("api.gdeltproject.org", (429, "slow down"))])
    assert gdelt.collect(c, niche, DAY) == []
    n = len(ses.calls)
    assert gdelt.collect(c, niche, DAY) == [] and len(ses.calls) == n       # no more requests this run


def test_federal_register(niche):
    niche.keywords, niche.entities, niche.exclude = ["waterfowl"], [], []
    c, ses = make_client([("federalregister.gov/api/v1/documents.json", "fedreg.json")])
    sigs = federal_register.collect(c, niche, DAY)
    assert [s.term[:10] for s in sigs] == ["Migratory "]           # the Sept rule is older than 7 days
    assert sigs[0].extra["title_match"] and not sigs[0].extra.get("low_confidence")
    ev = federal_register.collect_events(c, niche, DAY)             # the widget rule matches only in its abstract
    assert [(e.kind, e.date.isoformat()) for e in ev] == [("comment deadline", "2026-12-01")]
    niche.keywords = ["labelling"]
    low = federal_register.collect(c, niche, date(2026, 9, 5))
    assert low and low[0].extra["low_confidence"] is True


def test_rss_and_atom(niche):
    r = rss.parse(fixture_text("feed.rss"))
    assert [i["title"] for i in r] == ["Bird watching roadmap 2027", "Old post", "Launch event: Bird watching Festival"]
    a = rss.parse(fixture_text("feed.atom"))
    assert a[0]["link"] == "https://atom.test/1" and a[0]["published"].year == 2026
    niche.rss = ["https://blog.test/feed.xml"]
    c, _ = make_client([("blog.test/feed.xml", "feed.rss")])
    sigs = rss.collect(c, niche, DAY)
    assert [s.term for s in sigs] == ["Bird watching roadmap 2027"]          # old dropped, future is an event
    ev = rss.collect_events(c, niche, DAY)
    assert [(e.title, e.date.isoformat()) for e in ev] == [("Launch event: Bird watching Festival", "2026-11-18")]
    assert "IGNORED" not in str([s.to_dict() for s in sigs])


def test_ics(niche):
    evs = ics.parse(fixture_text("cal.ics"))
    assert evs[0]["summary"] == "Spring Bird Festival, main show" and evs[0]["start"] == date(2026, 11, 20)
    assert evs[1]["summary"] == "Weekly stream with a very long folded title"
    niche.ics = ["https://cal.test/a.ics"]
    c, _ = make_client([("cal.test/a.ics", "cal.ics")])
    out = ics.collect_events(c, niche, DAY)
    assert [(e.date.isoformat(), e.title[:6]) for e in out] == [
        ("2026-11-04", "Weekly"), ("2026-11-11", "Weekly"), ("2026-11-18", "Weekly"), ("2026-11-20", "Spring")]


def test_search_terms_interleave_and_strip_disambiguation():
    from nicheclock.sources._common import search_terms
    n = Niche("n", keywords=["bird watching", "bird feeder", "party game"],
              entities=["Merlin Bird ID (app)", "Osprey II"])
    assert search_terms(n, 4) == ["bird watching", "Merlin Bird ID", "bird feeder", "Osprey II"]


def test_gdelt_queue_defers_and_resumes_next_run(tmp_path):
    a = Niche("A", keywords=["bird feeder"], sources=["gdelt"])
    b = Niche("B", keywords=["suet cake"], sources=["gdelt"])
    c3 = Niche("C", keywords=["nest box"], sources=["gdelt"])
    # run 1: A is answered, then GDELT rate-limits: B's two requests are queued
    c, ses = make_client([("bird+feeder", "gdelt_timeline.json"), ("api.gdeltproject.org", (429, "slow down"))],
                         cache_dir=tmp_path)
    note = gdelt.prime(c, [a, b], DAY)
    assert "2 request(s) queued" in note and len(gdelt.load_queue(c)) == 2
    n_calls = len(ses.calls)
    assert gdelt.collect(c, b, DAY) == [] and len(ses.calls) == n_calls       # no more requests this run
    # run 2: C comes first in the config, but B's queued requests go first; A is cached
    c2, ses2 = make_client([("mode=timelinevol", "gdelt_timeline.json"), ("mode=artlist", "gdelt_artlist.json")],
                           cache_dir=tmp_path)
    assert gdelt.prime(c2, [a, c3, b], DAY) is None
    gd = [u for u in ses2.calls if "api.gdeltproject.org/api" in u]
    assert "suet+cake" in gd[0] and "suet+cake" in gd[1] and "nest+box" in gd[2]
    assert not any("bird+feeder" in u and "mode=artlist" in u for u in gd)   # A's timeline was cached in run 1
    assert gdelt.load_queue(c2) == []
    n = len(ses2.calls)
    sigs = gdelt.collect(c2, b, DAY)                                           # served from the cache
    assert len(ses2.calls) == n
    assert any(s.extra.get("metric") for s in sigs) and any(s.url == "https://news.example/a" for s in sigs)


def test_rss_watch_feeds_are_flagged(niche):
    niche.rss = ["https://blog.test/feed.xml"]
    niche.watch = ["https://watch.test/feed.xml"]
    c, _ = make_client([("blog.test/feed.xml", "feed.rss"), ("watch.test/feed.xml", "feed.atom")])
    sigs = rss.collect(c, niche, DAY)
    flags = {s.term: bool(s.extra.get("watch")) for s in sigs}
    assert flags == {"Bird watching roadmap 2027": False, "Atom entry": True}

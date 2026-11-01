import json
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from nicheclock.cluster import cluster, rank_topics
from nicheclock.config import Config, Identity, Niche, Project
from nicheclock.match import match_text, normalize
from nicheclock.models import Event, Signal
from nicheclock.pipeline import run_project
from nicheclock.store import Store
from nicheclock.upcoming import apply_lag, early_risers, lag_days
from tests.conftest import make_client, report_data

NOW = datetime.now(timezone.utc)


def test_match_phrases():
    n = Niche("n", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID"], exclude=["beginner"])
    assert match_text("Best Bird-Watching of 2026", n).keywords == ["bird watching"]
    assert match_text("merlin bird id review", n).entities == ["Merlin Bird ID"]
    assert match_text("Split screens for beginners", n).ok is False      # plural stem matches, excluded
    assert match_text("birding", n).ok is False
    assert normalize("Café — Osprey II (app)") == "cafe osprey ii"


def test_cluster_merges_by_entity_and_overlap():
    n = Niche("n", keywords=["bird watching"], entities=["Merlin Bird ID"])
    sigs = [Signal("wikipedia", "Merlin Bird ID (app)", rank=3, seen_at=NOW),
            Signal("hn", "Merlin Bird ID sold 20M copies", score=100, rank=1, seen_at=NOW, extra={"query": "x"}),
            Signal("google_trends", "new bird watching tool", rank=5, seen_at=NOW),
            Signal("hn", "New bird watching tool announced", score=10, rank=2, seen_at=NOW),
            Signal("github", "foo/bar", score=5, seen_at=NOW)]
    topics = {t.label: t for t in cluster(sigs, n)}
    assert topics["Merlin Bird ID (app)"].sources == ["wikipedia"]
    assert topics["Merlin Bird ID sold 20M copies"].sources == ["hn"]       # a headline mentioning the entity stays separate
    merged = [t for t in topics.values() if len(t.signals) == 2]
    assert len(merged) == 1 and merged[0].sources == ["google_trends", "hn"]
    assert len(topics) == 4


def test_scoring_multi_source_beats_single_and_recency():
    n = Niche("n", keywords=["x"])
    multi = Signal("google_trends", "alpha", rank=5, seen_at=NOW), Signal("hn", "alpha", score=300, rank=1, seen_at=NOW)
    single = (Signal("google_trends", "beta", rank=5, seen_at=NOW),)
    old = (Signal("google_trends", "gamma", rank=5, seen_at=NOW - timedelta(days=4)),)
    ranked = rank_topics(cluster(list(multi + single + old), n), NOW)
    assert [t.label for t in ranked] == ["alpha", "beta", "gamma"]
    assert "2 sources" in ranked[0].why and 0 < ranked[2].score < ranked[1].score < ranked[0].score <= 100


def test_wikipedia_growth_scores_higher():
    n = Niche("n", keywords=["x"])
    rising = Signal("wikipedia", "R", rank=50, seen_at=NOW, extra={"ratio": 4.0, "views_3d": 400, "base_14d": 100})
    flat = Signal("wikipedia", "F", rank=50, seen_at=NOW, extra={"views": 400})
    ranked = rank_topics(cluster([rising, flat], n), NOW)
    assert ranked[0].label == "R" and "x4.0" in ranked[0].why


def test_upcoming_lag_and_riser(tmp_path):
    cfg = Config(identity=Identity("a/1", "a@b.c"), default_lag_days=3, data_dir=tmp_path)
    with Store(tmp_path) as st:
        assert lag_days(cfg, st, "p") == 3
        st.set_meta("p", "index_lag_days", 6)
        assert lag_days(cfg, st, "p") == 6
    ev = apply_lag([Event("k", "t", date(2026, 12, 10))], 6)
    assert ev[0].publish_by == date(2026, 12, 4)
    n = Niche("n", keywords=["x"])
    s = Signal("wikipedia", "Osprey II", extra={"ratio": 3.0, "base_14d": 100, "views_3d": 300})
    weak = Signal("wikipedia", "Tiny", extra={"ratio": 3.0, "base_14d": 5, "views_3d": 15})
    got = early_risers([s, weak], n, date(2026, 10, 31))
    assert [e.title for e in got] == ["Osprey II"] and got[0].kind == "early riser"


def _cfg(tmp_path):
    n = Niche("birdwatching", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID (app)"],
              exclude=["tips"], sources=["google_trends", "wikipedia", "hn", "github", "gdelt", "apple",
                                         "federal_register", "rss"],
              rss=["https://blog.test/feed.xml"], ics=["https://cal.test/a.ics"], formats=["tier list"])
    return Config(identity=Identity("nc-test/1 (+mailto:t@e.com)", "t@e.com"), data_dir=tmp_path / "data",
                  out_dir=tmp_path / "out", projects=[Project("gb", ["https://gb.test"], [n])])


ROUTES = [
    ("trending/rss", "google_trends.xml"),
    ("/top/en.wikipedia/all-access/", "wiki_top.json"),
    ("w/api.php", "mediawiki_query.json"),
    ("per-article", "wiki_series_rising.json"),
    ("search_by_date", "hn.json"),
    ("api.github.com", "github.json"),
    ("gdeltproject.org", "gdelt_timeline.json"),
    ("apps.json", "apple.json"),
    ("federalregister.gov", "fedreg.json"),
    ("blog.test", "feed.rss"),
    ("cal.test", "cal.ics"),
]


def test_run_project_end_to_end(tmp_path):
    cfg = _cfg(tmp_path)
    client, _ = make_client(ROUTES)
    with Store.open(cfg) as st:
        r = run_project(cfg, cfg.projects[0], client=client, store=st, day=NOW.date(), notify=False)
        assert not r.errors, r.errors
        titles = [i.title for i in r.feed.items]
        assert "Merlin Bird ID (app)" in titles or "Merlin Bird ID" in titles
        assert r.counts["birdwatching"]["hn"] == 1               # the "tips" story is excluded
        assert "Chiefs vs Bills" not in titles
        kinds = {e.kind for e in r.feed.upcoming}
        assert {"calendar", "early riser"} <= kinds
        assert all(e.publish_by == e.date - timedelta(days=cfg.default_lag_days) for e in r.feed.upcoming)
        assert st.topics("gb", "birdwatching") and st.last_run("gb")["status"] == "ok"
        # second run the same day does not duplicate signals
        n1 = len(st.recent_signals("gb", "birdwatching"))
        run_project(cfg, cfg.projects[0], client=make_client(ROUTES)[0], store=st, day=NOW.date(), notify=False)
        assert len(st.recent_signals("gb", "birdwatching")) == n1

    out = tmp_path / "out" / "gb"
    feed = json.loads((out / "feed.json").read_text())
    assert feed["project"] == "gb" and feed["items"] and feed["upcoming"]
    assert set(feed["items"][0]) == {"project", "niche", "kind", "title", "url", "why", "score", "at", "region", "region_precision", "regions", "evidence"}
    assert feed["items"] == sorted(feed["items"], key=lambda i: -i["score"])
    html = (out / "index.html").read_text()
    page = report_data(out / "index.html")
    assert "Data source: Google Trends" in html and page["upcoming"] and len(page["topics"]) == len(feed["items"])
    assert all(t["sources"] and t["sources"][0]["url"] for t in page["topics"] if t["url"])        # evidence behind every row
    md = (out / "feed.md").read_text()
    assert "## Upcoming" in md and "| Date | Publish by |" in md
    root = ET.fromstring((out / "feed.xml").read_text())
    assert root.tag == "rss" and len(root.findall("./channel/item")) >= len(feed["items"])
    assert (tmp_path / "out" / "gb" / "feed.json").exists()


def test_run_survives_failing_source(tmp_path):
    cfg = _cfg(tmp_path)
    client, _ = make_client([r for r in ROUTES if r[0] != "trending/rss"])   # google trends -> 404
    with Store.open(cfg) as st:
        r = run_project(cfg, cfg.projects[0], client=client, store=st, notify=False)
    assert any("google_trends" in e for e in r.errors) and r.feed.items


def test_relevance_floor():
    from nicheclock.cluster import passes_floor
    from nicheclock.models import Topic

    def t(*sigs):
        return Topic("k", "k", list(sigs))

    def sig(source, kws=(), ents=()):
        return Signal(source, "x", extra={"keywords": list(kws), "entities": list(ents)})

    assert passes_floor(t(sig("github", ents=["Merlin Bird ID"])))                       # entity
    assert passes_floor(t(sig("hn", kws=["bird watching", "birding"])))                            # 2 keywords
    assert passes_floor(t(sig("rss", kws=["optic hawk"])))                                # specific multi-word phrase in a feed title
    assert not passes_floor(t(sig("rss", kws=["free trial"])))                                # generic phrase
    assert not passes_floor(t(sig("rss", kws=["optics"])))                               # single generic word
    assert not passes_floor(t(sig("github", kws=["bird feeder"])))                            # 1 keyword, 1 family
    assert not passes_floor(t(sig("github", kws=["optics"]), sig("hn", kws=["optics"])))  # same family
    assert passes_floor(t(sig("github", kws=["optics"]), sig("google_trends", kws=["optics"])))


def test_article_entity_needs_exact_title_and_lone_signal_keeps_headline():
    from nicheclock.match import relevant
    n = Niche("n", keywords=[], entities=["Godot"])
    sigs = [Signal("wikipedia", "Waiting for Godot", extra={"article": True}),
            Signal("wikipedia", "Godot", extra={"article": True}),
            Signal("rss", "Godot 5 released", seen_at=NOW)]
    kept = relevant(sigs, n)
    assert [s.term for s in kept] == ["Godot", "Godot 5 released"]     # "Waiting for Godot" is not the entity
    topics = cluster(kept[1:], n)
    assert topics[0].label == "Godot 5 released"
    assert cluster(kept, n)[0].label == "Godot"


def test_strong_signal_passes_floor_with_one_keyword():
    from nicheclock.cluster import passes_floor
    from nicheclock.models import Topic

    def one(source, **kw):
        extra = {"keywords": ["optics"], "entities": []}
        extra.update(kw.pop("extra", {}))
        return Topic("k", "k", [Signal(source, "x", extra=extra, **kw)])

    assert passes_floor(one("hn", score=120))
    assert not passes_floor(one("hn", score=99))
    assert passes_floor(one("google_trends", rank=10)) and not passes_floor(one("google_trends", rank=11))
    assert passes_floor(one("wikipedia", extra={"wow": 2.1})) and passes_floor(one("wikipedia", extra={"ratio": 2.0}))
    assert not passes_floor(one("wikipedia", extra={"wow": 1.8}))
    assert passes_floor(one("gdelt", extra={"metric": "news volume", "ratio": 2.5}))
    assert not passes_floor(one("gdelt", extra={"metric": "news volume", "ratio": 1.4}))
    assert not passes_floor(one("hn", score=500, extra={"keywords": []}))      # strong, but no keyword hit


def test_week_over_week_topic():
    from nicheclock.sources import wikipedia
    from datetime import date as d, timedelta as td
    series = [(d(2026, 10, 1) + td(days=i), v) for i, v in enumerate([100] * 7 + [100, 100, 100, 100, 180, 180, 180])]
    this, prev, ratio = wikipedia.week_over_week(series)
    assert round(ratio, 2) == 1.34 and wikipedia.week_over_week(series[:10]) is None


def test_scoring_accepts_date_only_created_at():
    from nicheclock.cluster import score_topic
    from nicheclock.models import Topic
    t = Topic("k", "k", [Signal("federal_register", "x", rank=1, seen_at=NOW, extra={"created_at": "2026-10-28"})])
    assert score_topic(t, NOW).score >= 0


def test_context_never_passes_alone_but_counts_beside_a_keyword():
    from nicheclock.cluster import passes_floor
    from nicheclock.match import relevant
    from nicheclock.models import Topic
    n = Niche("n", keywords=["optics"], entities=["Osprey II"], context=["California", "OpenAI"])
    sigs = [Signal("hn", "California wealth tax", score=500, seen_at=NOW),            # context only: not relevant
            Signal("hn", "OpenAI ships a thing", score=500, seen_at=NOW),
            Signal("hn", "California loves Optics", score=20, seen_at=NOW),           # keyword + context
            Signal("hn", "Optics explained", score=20, seen_at=NOW)]               # keyword only, weak
    kept = relevant(sigs, n)
    assert [s.term for s in kept] == ["California loves Optics", "Optics explained"]
    assert kept[0].extra["context"] == ["California"] and kept[1].extra["context"] == []
    topics = {t.label: t for t in cluster(kept, n)}
    assert passes_floor(topics["California loves Optics"])         # 1 keyword + 1 context = 2 hits
    assert not passes_floor(topics["Optics explained"])          # 1 weak keyword hit


def test_federal_register_needs_a_keyword_in_the_title():
    from nicheclock.cluster import passes_floor
    from nicheclock.models import Topic

    def fr(kws, ents):
        return Topic("k", "k", [Signal("federal_register", "x", extra={"keywords": kws, "entities": ents, "title_match": True})])

    assert passes_floor(fr(["wildlife law"], []))
    assert not passes_floor(fr([], ["California"]))


def test_watch_feed_item_passes_on_one_keyword():
    from nicheclock.cluster import passes_floor
    from nicheclock.models import Topic
    watched = Signal("rss", "x", extra={"keywords": ["free trial"], "entities": [], "watch": True})
    plain = Signal("rss", "x", extra={"keywords": ["free trial"], "entities": []})
    assert not passes_floor(Topic("k", "k", [watched]))                  # "free trial" is generic even in a watched feed
    specific = Signal("rss", "x", extra={"keywords": ["optic hawk"], "entities": [], "watch": True})
    single = Signal("rss", "x", extra={"keywords": ["konstrari"], "entities": [], "watch": True})
    assert passes_floor(Topic("k", "k", [specific])) and passes_floor(Topic("k", "k", [single]))
    assert not passes_floor(Topic("k", "k", [plain]))


def test_generic_phrases():
    from nicheclock.generic import is_generic
    assert is_generic("small teams") and is_generic("free trial") and is_generic("party game")
    assert not is_generic("merlin bird id") and not is_generic("migratory birds") and not is_generic("bird feeder")

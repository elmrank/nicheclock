"""Regions: config, tagging per source type, combined vs per_country, outputs, store, MCP."""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from nicheclock import regions as rg
from nicheclock.cluster import cluster, passes_floor, rank_topics
from nicheclock.config import Config, Identity, Niche, Project, load_config, save_config, validate_config, validate_dict
from nicheclock.models import Event, Signal
from nicheclock.output import Feed, NicheMeta, filter_feed, write_all
from nicheclock.pipeline import run_project
from nicheclock.sources import SOURCE_NAMES, apple, federal_register, gdelt, github, google_trends, hn, load, wikipedia
from nicheclock.store import Store
from tests.conftest import fixture_text, make_client, report_data

DAY = date(2026, 10, 31)
NOW = datetime.now(timezone.utc)


# ---- config ------------------------------------------------------------------------------

def test_normalize_and_validate_regions():
    assert rg.normalize(None) == ["global"] and rg.normalize([]) == ["global"]
    assert rg.normalize(["us", "GB", "us", "Global"]) == ["US", "GB", "global"]
    assert rg.invalid(["US", "VNM", "x1", "global"]) == ["VNM", "X1"]
    raw = {"identity": {"user_agent": "a/1", "contact_email": "a@b.c"},
           "projects": [{"name": "p", "regions": ["US", "BAD"], "region_mode": "sideways",
                         "niches": [{"name": "n", "keywords": ["x"], "regions": ["VN"], "region_mode": "nope"}]}]}
    errs = "\n".join(validate_dict(raw))
    assert "BAD" in errs and "region_mode" in errs and errs.count("region_mode") >= 2


def test_config_roundtrip_and_effective(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("""
identity: {user_agent: "a/1 (+mailto:a@b.co)", contact_email: a@b.co}
projects:
  - name: p
    regions: [us, gb]
    region_mode: per_country
    niches:
      - {name: inherits, keywords: [x], sources: [hn]}
      - {name: own, keywords: [y], sources: [hn], regions: [VN], region_mode: combined}
      - {name: plain, keywords: [z], sources: [hn], regions: [global]}
""")
    cfg = load_config(f)
    assert validate_config(cfg) == []
    p = cfg.projects[0]
    assert p.regions == ["US", "GB"] and p.region_mode == "per_country"
    assert rg.effective(p, p.niche("inherits")) == (["US", "GB"], "per_country")
    assert rg.effective(p, p.niche("own")) == (["VN"], "combined")
    assert rg.effective(p, p.niche("plain")) == (["global"], "per_country")
    save_config(cfg, tmp_path / "d.yaml")
    q = load_config(tmp_path / "d.yaml").projects[0]
    assert q.regions == ["US", "GB"] and q.niche("own").regions == ["VN"] and q.niche("inherits").regions is None
    # defaults are not written out
    default = Config(projects=[Project("d", niches=[Niche("n", keywords=["k"])])])
    assert "regions" not in default.to_dict()["projects"][0]


def test_every_source_declares_region_support():
    for name in SOURCE_NAMES + ("ics",):
        assert load(name).REGION_SUPPORT in rg.SUPPORT_KINDS, name
    assert {n: load(n).REGION_SUPPORT for n in ("google_trends", "apple", "wikipedia", "gdelt", "hn", "github",
                                                "federal_register")} == {
        "google_trends": "country", "apple": "country", "wikipedia": "language", "gdelt": "source_country",
        "hn": "global", "github": "global", "federal_register": "global"}


# ---- tagging per source type -------------------------------------------------------------

@pytest.fixture
def niche():
    return Niche(name="birdwatching", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID"],
                 sources=["google_trends"])


def test_google_trends_country_exact(niche):
    c, ses = make_client([("trending/rss", "google_trends.xml")])
    sigs = google_trends.collect(c, niche, DAY, ["US", "GB"])
    assert {s.region for s in sigs} == {"US", "GB"} and {s.region_precision for s in sigs} == {"exact"}
    assert [u for u in ses.calls if "trending/rss" in u] == [
        "https://trends.google.com/trending/rss?geo=US", "https://trends.google.com/trending/rss?geo=GB"]
    # nothing requested: the default geo, tagged with that country
    c2, _ = make_client([("trending/rss", "google_trends.xml")])
    assert {(s.region, s.region_precision) for s in google_trends.collect(c2, niche, DAY)} == {("US", "exact")}


def test_apple_country_exact(niche):
    c, ses = make_client([("apps.json", "apple.json")])
    sigs = apple.collect(c, niche, DAY, ["GB", "VN"])
    assert {s.region for s in sigs} == {"GB", "VN"} and {s.region_precision for s in sigs} == {"exact"}
    assert any("/gb/apps/" in u for u in ses.calls) and any("/vn/apps/" in u for u in ses.calls)


def test_wikipedia_language_approx(niche):
    import json as _json
    routes = [("/top/", "wiki_top.json"), ("w/api.php", "mediawiki_query.json"), ("per-article", "wiki_series_flat.json")]
    niche.entities = []
    c, ses = make_client(routes)
    sigs = wikipedia.collect(c, niche, DAY, ["US", "GB", "VN", "XX"])
    by_region = {}
    for s in sigs:
        by_region.setdefault(s.region, []).append(s)
    assert set(by_region) == {"US", "GB", "VN"} and {s.region_precision for s in sigs} == {"approx"}
    tops = [u for u in ses.calls if "/top/" in u]
    assert any("/top/en.wikipedia/" in u for u in tops) and any("/top/vi.wikipedia/" in u for u in tops)
    assert len([u for u in tops if "/top/en.wikipedia/" in u]) == 1       # US and GB share one en fetch
    assert any("no language edition mapped for XX" in f for f in c.drain_failures())
    # global request: the default edition, no country claimed
    c2, _ = make_client(routes)
    g = wikipedia.collect(c2, niche, DAY, ["global"])
    assert g and {(s.region, s.region_precision) for s in g} == {("global", "global")}


def test_gdelt_source_country_approx(niche):
    routes = [("mode=timelinevol", "gdelt_timeline.json"), ("mode=artlist", "gdelt_artlist.json")]
    c, ses = make_client(routes)
    sigs = gdelt.collect(c, niche, DAY, ["GB", "VN", "DE"])      # default limit: two countries per niche
    assert {s.region for s in sigs} == {"GB", "VN"} and {s.region_precision for s in sigs} == {"approx"}
    queries = " ".join(u for u in ses.calls if "api.gdeltproject.org/api" in u)
    assert "sourcecountry%3AUK" in queries and "sourcecountry%3AVM" in queries and "sourcelang" not in queries
    notes = c.drain_failures()
    assert any("DE not queried" in n for n in notes)
    c2, ses2 = make_client(routes)
    g = gdelt.collect(c2, niche, DAY, ["global", "XX"])
    assert {(s.region, s.region_precision) for s in g} == {("global", "global")}
    assert any("sourcelang%3Aenglish" in u for u in ses2.calls)
    assert any("no country filter known for XX" in n for n in c2.drain_failures())


def test_global_sources_stay_global_and_fr_is_us(niche):
    c, _ = make_client([("hn.algolia.com", "hn.json")])
    assert {(s.region, s.region_precision) for s in hn.collect(c, niche, DAY, ["VN"])} == {("global", "global")}
    c, _ = make_client([("api.github.com", "github.json")])
    assert {(s.region, s.region_precision) for s in github.collect(c, niche, DAY, ["VN"])} == {("global", "global")}
    niche.keywords, niche.entities = ["waterfowl"], []
    c, _ = make_client([("federalregister.gov", "fedreg.json")])
    fr = federal_register.collect(c, niche, DAY, ["global"])
    assert fr and {(s.region, s.region_precision) for s in fr} == {("US", "exact")}
    ev = federal_register.collect_events(c, niche, DAY, ["US", "GB"])
    assert ev and {e.region for e in ev} == {"US"}
    assert federal_register.collect(c, niche, DAY, ["VN"]) == []        # US-only source: nothing for VN
    assert federal_register.collect_events(c, niche, DAY, ["VN"]) == []


# ---- topics: combined vs per_country -----------------------------------------------------

def _sigs():
    mk = lambda region, prec, source="google_trends", term="Snow Goose", rank=1: Signal(
        source, term, rank=rank, seen_at=NOW, region=region, region_precision=prec,
        extra={"keywords": ["x"], "entities": ["Snow Goose"], "matched": ["Snow Goose"]})
    return [mk("US", "exact"), mk("GB", "exact"), mk("global", "global", "hn", "Snow Goose sells 5M", 2),
            mk("VN", "approx", "wikipedia", "Snow Goose")]


def test_combine_region_semantics():
    s = _sigs()
    assert rg.combine(s[:1]) == ("US", "exact", ["US"])
    assert rg.combine(s[:2]) == ("multi", "exact", ["GB", "US"])
    assert rg.combine([s[2]]) == ("global", "global", [])
    assert rg.combine([s[0], s[2]]) == ("US", "exact", ["US"])             # global evidence does not dilute
    assert rg.combine([s[3]]) == ("VN", "approx", ["VN"])
    assert rg.combine([s[2], s[3]])[:2] == ("VN", "approx")                # best precision among countries


def test_combined_merges_countries_per_country_splits():
    n = Niche("n", keywords=["x"], entities=["Snow Goose"])
    combined = {t.label: t for t in cluster(_sigs(), n, "combined")}
    assert "Snow Goose" in combined
    top = combined["Snow Goose"]
    assert top.region == "multi" and top.regions == ["GB", "US", "VN"] and top.region_precision == "exact"
    per = cluster(_sigs(), n, "per_country")
    got = {(t.region, t.label): t for t in per}
    assert {r for r, _ in got} == {"US", "GB", "VN", "global"}
    assert got[("US", "Snow Goose")].region_precision == "exact"
    assert got[("VN", "Snow Goose")].region_precision == "approx"
    assert got[("global", "Snow Goose sells 5M")].region == "global"
    assert all(len(t.regions) <= 1 for t in per)                         # a topic belongs to one region


# ---- pipeline + outputs ------------------------------------------------------------------

ROUTES = [
    ("trending/rss", "google_trends.xml"),
    ("/top/", "wiki_top.json"),
    ("w/api.php", "mediawiki_query.json"),
    ("per-article", "wiki_series_rising.json"),
    ("search_by_date", "hn.json"),
]


def _cfg(tmp_path, regions, mode):
    n = Niche("birdwatching", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID (app)"],
              exclude=["tips"], sources=["google_trends", "wikipedia", "hn"])
    return Config(identity=Identity("nc-test/1 (+mailto:t@e.com)", "t@e.com"), data_dir=tmp_path / "data",
                  out_dir=tmp_path / "out",
                  projects=[Project("gb", ["https://gb.test"], [n], regions=regions, region_mode=mode)])


def _run(tmp_path, regions, mode, **kw):
    cfg = _cfg(tmp_path, regions, mode)
    client, _ = make_client(ROUTES)
    with Store.open(cfg) as st:
        r = run_project(cfg, cfg.projects[0], client=client, store=st, day=NOW.date(), notify=False, **kw)
    return cfg, r


def test_run_per_country_and_combined(tmp_path):
    cfg, r = _run(tmp_path / "a", ["US", "GB"], "per_country")
    assert not r.errors, r.errors
    items = r.feed.items
    regions = {i.region for i in items}
    assert {"US", "GB"} <= regions and regions <= {"US", "GB", "global"}
    assert r.feed.regions == ["US", "GB"] and r.feed.region_mode == "per_country"
    out = tmp_path / "a" / "out" / "gb"
    feed = json.loads((out / "feed.json").read_text())
    assert feed["regions"] == ["US", "GB"] and feed["region_mode"] == "per_country"
    assert feed["niches"][0]["regions"] == ["US", "GB"]
    assert all({"region", "region_precision", "regions"} <= set(i) for i in feed["items"])
    country_items = [i for i in feed["items"] if i["region"] in ("US", "GB")]
    assert country_items and all(i["region_precision"] in ("exact", "approx") for i in country_items)
    assert any(i["region"] == "US" and i["region_precision"] == "exact" for i in feed["items"])
    # per-country views: that country plus the global items
    for code in ("US", "GB"):
        view = json.loads((out / "regions" / code / "feed.json").read_text())
        assert view["region"] == code
        assert {i["region"] for i in view["items"]} <= {code, "global"}
        assert (out / "regions" / code / "index.html").exists() and (out / "regions" / code / "feed.xml").exists()
    html = (out / "index.html").read_text()
    assert {t["region"] for t in report_data(out / "index.html")["topics"]} >= {"US", "GB"} and "regions/GB/index.html" in html
    md = (out / "feed.md").read_text()
    assert "### US" in md and "### GB" in md and "Regions: US, GB (per_country)" in md

    cfg2, r2 = _run(tmp_path / "b", ["US", "GB"], "combined")
    assert not r2.errors
    topics2 = {i.title: i for i in r2.feed.items}
    itt = topics2.get("Merlin Bird ID (app)")
    assert itt is not None and itt.region == "multi" and itt.regions == ["GB", "US"]
    md2 = (tmp_path / "b" / "out" / "gb" / "feed.md").read_text()
    assert "### US" not in md2 and "`multiple countries`" in md2
    html2 = report_data(tmp_path / "b" / "out" / "gb" / "index.html")
    assert {t["region"] for t in html2["topics"]} >= {"multi"}
    assert len(r2.feed.items) < len(r.feed.items)           # the same story in two countries is one topic
    assert sum(1 for i in r.feed.items if i.title == "Merlin Bird ID (app)") == 2   # per_country: one each


def test_default_regions_show_no_noise(tmp_path):
    cfg, r = _run(tmp_path, ["global"], "combined")
    # google trends reads its default geo: items are honest about being US
    assert any(i.region == "US" for i in r.feed.items)
    only_global = Feed("p", items=[i for i in r.feed.items if i.region == "global"])
    assert not only_global.shows_regions()


def test_run_region_override(tmp_path):
    cfg, r = _run(tmp_path, ["US", "GB", "VN"], "per_country", region="GB")
    assert {i.region for i in r.feed.items} <= {"GB", "global"} and r.feed.regions == ["GB"]


def test_filter_feed_keeps_country_plus_global():
    from nicheclock.models import FeedItem
    mk = lambda t, region, regions=(): FeedItem("p", "n", "topic", t, None, "w", 1.0, region=region, regions=list(regions))
    feed = Feed("p", niches=[NicheMeta("n")], items=[mk("us", "US"), mk("gb", "GB"), mk("g", "global"),
                                                      mk("both", "multi", ["GB", "US"]), mk("gbvn", "multi", ["GB", "VN"])],
                upcoming=[Event("k", "t1", DAY, region="US"), Event("k", "t2", DAY, region="VN"), Event("k", "t3", DAY)])
    v = filter_feed(feed, "us")
    assert [i.title for i in v.items] == ["us", "g", "both"] and v.region == "US"
    assert [e.title for e in v.upcoming] == ["t1", "t3"]


# ---- store -------------------------------------------------------------------------------

def test_store_region_columns_filter_and_migration(tmp_path):
    with Store(tmp_path) as st:
        st.add_signals("p", "n", [Signal("google_trends", "T", region="US", region_precision="exact", seen_at=NOW),
                                  Signal("google_trends", "T", region="GB", region_precision="exact", seen_at=NOW),
                                  Signal("hn", "H", seen_at=NOW)])
        assert len(st.recent_signals("p", "n")) == 3                       # same term, two regions: both kept
        assert {s.term + s.region for s in st.recent_signals("p", "n", region="US")} == {"TUS", "Hglobal"}
        n = Niche("n", keywords=["x"])
        topics = cluster(st.recent_signals("p", "n"), n, "combined")
        for t in topics:
            t.score = 1.0
        st.save_topics(1, "p", "n", topics)
        got = {t.label: t for t in st.topics("p", "n")}
        assert got["T"].region == "multi" and got["T"].regions == ["GB", "US"]
        assert {t.label for t in st.topics("p", "n", region="US")} == {"T", "H"}
        assert {t.label for t in st.topics("p", "n", region="VN")} == {"H"}
        st.save_events("p", [Event("k", "E", DAY + timedelta(days=1), niche="n", region="US", region_precision="exact"),
                             Event("k", "F", DAY + timedelta(days=1), niche="n")])
    with Store(tmp_path) as st:
        assert {e.title for e in st.events("p", since=DAY, region="GB")} == {"F"}
        assert {e.title for e in st.events("p", since=DAY, region="US")} == {"E", "F"}

    old = tmp_path / "old"
    old.mkdir()
    db = sqlite3.connect(old / "nicheclock.db")
    db.executescript("""
      CREATE TABLE signals (id INTEGER PRIMARY KEY, project TEXT NOT NULL, niche TEXT NOT NULL, source TEXT NOT NULL,
        term TEXT NOT NULL, url TEXT, rank INTEGER, score REAL, geo TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL,
        day TEXT NOT NULL, extra TEXT NOT NULL DEFAULT '{}', run_id INTEGER, UNIQUE(project, niche, source, term, geo, day));
      CREATE TABLE topics (id INTEGER PRIMARY KEY, run_id INTEGER, project TEXT NOT NULL, niche TEXT NOT NULL,
        key TEXT NOT NULL, label TEXT NOT NULL, score REAL NOT NULL, why TEXT NOT NULL DEFAULT '', url TEXT,
        signals TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
      INSERT INTO signals(project, niche, source, term, seen_at, day) VALUES ('p','n','hn','Old','2026-10-30T00:00:00+00:00','2026-10-30');
      INSERT INTO topics(project, niche, key, label, score, created_at) VALUES ('p','n','old','Old',1,'2026-10-30T00:00:00+00:00');
    """)
    db.commit()
    db.close()
    with Store(old) as st:
        s = st.signals_since("p", "n")
        assert [(x.term, x.region) for x in s] == [("Old", "global")]
        assert st.topics("p", "n", since=datetime(2026, 10, 1, tzinfo=timezone.utc))[0].region == "global"
        st.add_signals("p", "n", [Signal("hn", "Old", region="US", seen_at=NOW)])


# ---- MCP ---------------------------------------------------------------------------------

def test_mcp_region_param(tmp_path):
    pytest.importorskip("mcp")
    from nicheclock import mcp_server

    cfg = _cfg(tmp_path, ["US", "GB"], "per_country")
    client, _ = make_client(ROUTES)
    with Store.open(cfg) as st:
        run_project(cfg, cfg.projects[0], client=client, store=st, day=NOW.date(), notify=False)
    allr = mcp_server.trends_for_niche(cfg, "gb", "birdwatching")
    assert {t["region"] for t in allr} >= {"US", "GB"} and all({"region", "region_precision", "regions"} <= set(t) for t in allr)
    us = mcp_server.trends_for_niche(cfg, "gb", "birdwatching", region="us")
    assert us and {t["region"] for t in us} <= {"US", "global"} and "GB" not in {t["region"] for t in us}
    assert len(us) < len(allr)
    assert mcp_server.trends_for_niche(cfg, "gb", "birdwatching", region="global") == allr    # no filter
    with pytest.raises(mcp_server.ToolError, match="region"):
        mcp_server.trends_for_niche(cfg, "gb", "birdwatching", region="USA")
    with pytest.raises(mcp_server.ToolError, match="region"):
        mcp_server.upcoming(cfg, "gb", region="12")
    assert isinstance(mcp_server.upcoming(cfg, "gb", region="US"), list)


def test_mcp_region_falls_back_to_feed_json(tmp_path):
    pytest.importorskip("mcp")
    from nicheclock import mcp_server

    cfg = _cfg(tmp_path, ["US", "GB"], "per_country")
    client, _ = make_client(ROUTES)
    with Store.open(cfg) as st:
        run_project(cfg, cfg.projects[0], client=client, store=st, day=NOW.date(), notify=False)
    (cfg.data_dir / "nicheclock.db").unlink()                          # no store: read feed.json
    gb = mcp_server.trends_for_niche(cfg, "gb", "birdwatching", region="GB")
    assert gb and {t["region"] for t in gb} <= {"GB", "global"}


# ---- doctor / CLI ------------------------------------------------------------------------

def test_doctor_reports_region_support(tmp_path, capsys):
    from nicheclock.cli import main
    f = tmp_path / "n.yaml"
    f.write_text("""
identity: {user_agent: "a/1 (+mailto:a@b.co)", contact_email: a@b.co}
projects:
  - name: p
    regions: [US, VN]
    niches:
      - {name: n, keywords: [x], sources: [google_trends, wikipedia, gdelt, hn, federal_register]}
""")
    assert main(["-c", str(f), "doctor", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "google_trends: per country (exact)" in out and "wikipedia: by language edition (approx.)" in out
    assert "gdelt: by publisher country (approx.)" in out and "hn: no country (global)" in out
    assert "federal_register: US only" in out


def test_run_rejects_bad_region(tmp_path, capsys):
    from nicheclock.cli import main
    f = tmp_path / "n.yaml"
    f.write_text("""
identity: {user_agent: "a/1 (+mailto:a@b.co)", contact_email: a@b.co}
projects:
  - {name: p, niches: [{name: n, keywords: [x], sources: [hn]}]}
""")
    assert main(["-c", str(f), "run", "--region", "USA"]) == 2
    assert "not a country code" in capsys.readouterr().err

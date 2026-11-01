from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from nicheclock import hosted_export as hx
from nicheclock.config import Config, Niche, Project
from nicheclock.models import Event, Signal, Topic
from nicheclock.store import Store

NOW = datetime.now(timezone.utc)
TODAY = date.today()


def sig(source, term, url=None, rank=None, score=None, extra=None):
    return Signal(source=source, term=term, url=url, rank=rank, score=score, seen_at=NOW, extra=extra or {})


@pytest.fixture
def cfg(tmp_path):
    proj = Project(name="catalog", urls=["https://example.com"],
                   niches=[Niche(name="Birdwatching", keywords=["bird watching"], entities=["Merlin Bird ID"]), Niche(name="bird photography")])
    c = Config(projects=[proj], data_dir=tmp_path / "data", out_dir=tmp_path / "out", base_dir=tmp_path)
    with Store.open(c) as s:
        s.save_topics(None, "catalog", "Birdwatching", [
            # mixed: google_trends + github + hn -> only hn survives, label came from google_trends
            Topic(key="snow goose", label="Snow Goose", niche="Birdwatching", score=95.0,
                  why="3 sources; google_trends #1 (500K+ searches); github 900 stars (new repo)",
                  signals=[sig("google_trends", "Snow Goose", "https://trends.google.com/x", rank=1),
                           sig("github", "snow-goose-mod", "https://github.com/a/b", score=900),
                           sig("hn", "Snow Goose dev interview", "https://news.ycombinator.com/item?id=9", score=120,
                               extra={"comments": 40})]),
            # entity label kept even though a dropped source also saw it
            Topic(key="merlin bird id", label="Merlin Bird ID", niche="Birdwatching", score=50.0, why="x",
                  signals=[sig("google_trends", "merlin bird id", None, rank=2),
                           sig("wikipedia", "Merlin Bird ID (app)", "https://en.wikipedia.org/wiki/Merlin_Bird_ID_(app)",
                               rank=5, extra={"ratio": 3.0, "views_3d": 900, "base_14d": 300, "views": 900})]),
            # only disallowed sources -> dropped entirely
            Topic(key="apple top", label="Apple chart", niche="Birdwatching", score=80.0, why="apple #1",
                  signals=[sig("apple", "Apple chart", "https://apps.apple.com/x", rank=1)]),
            # fully allowed -> untouched score
            Topic(key="rule", label="Bird banding rule", niche="Birdwatching", score=33.3, why="federal register rule",
                  signals=[sig("federal_register", "Bird banding rule", "https://www.federalregister.gov/d/1", score=1)]),
        ])
        s.save_events("catalog", [
            Event(kind="release", title="FR comment deadline", date=TODAY + timedelta(days=9), source="federal_register",
                  url="https://www.federalregister.gov/d/2", publish_by=TODAY + timedelta(days=6), niche="Birdwatching"),
            Event(kind="release", title="User calendar event", date=TODAY + timedelta(days=5), source="ics", niche="Birdwatching"),
            Event(kind="early riser", title="Merlin Bird ID", date=TODAY, source="wikipedia",
                  url="https://en.wikipedia.org/wiki/Merlin_Bird_ID_(app)", niche="Birdwatching"),
        ])
    return c


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_catalog_layout_and_allow_list(cfg, tmp_path):
    out = tmp_path / "hosted-out"
    s = hx.export(cfg, out, now=NOW)
    assert (out / "catalog/niches.json").is_file()
    for n in ("birdwatching", "bird-photography"):
        for f in ("trends", "evidence", "upcoming"):
            assert (out / f"catalog/{n}/{f}.json").is_file()
    blob = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.json")).lower()
    for banned in ("google_trends", "trends.google.com", "github", "apple", "user calendar event"):
        assert banned not in blob, banned
    trends = load(out / "catalog/birdwatching/trends.json")
    assert trends["schema"] == 1 and trends["niche"] == "birdwatching"
    by_title = {i["title"]: i for i in trends["items"]}
    assert set(by_title) == {"Snow Goose dev interview", "Merlin Bird ID", "Bird banding rule"}
    sf = by_title["Snow Goose dev interview"]            # relabelled: the old label came from google_trends
    assert sf["source"] == "hn" and sf["sources"] == ["hn"] and sf["score"] < 95.0 and "hn" in sf["why"]
    assert by_title["Bird banding rule"]["score"] == 33.3     # nothing dropped -> stored score kept
    assert by_title["Merlin Bird ID"]["source"] == "wikimedia_pageviews"
    assert by_title["Merlin Bird ID"]["license"] == "CC0-1.0"
    for it in trends["items"]:
        assert it["id"].startswith("birdwatching~") and it["source_url"].startswith("http")
        assert it.get("license") or it.get("attribution")
    assert [i["score"] for i in trends["items"]] == sorted((i["score"] for i in trends["items"]), reverse=True)
    ev = load(out / "catalog/birdwatching/evidence.json")["items"]
    assert set(ev) == {i["id"] for i in trends["items"]}
    assert all(e["source"] in hx.PUBLIC_MAP.values() for r in ev.values() for e in r["evidence"])
    up = load(out / "catalog/birdwatching/upcoming.json")["items"]
    assert [e["title"] for e in up] == ["Merlin Bird ID", "FR comment deadline"]
    assert up[1]["source"] == "federal_register" and up[1]["publish_by"] == (TODAY + timedelta(days=6)).isoformat()
    niches = load(out / "catalog/niches.json")["niches"]
    assert [n["id"] for n in niches] == ["birdwatching", "bird-photography"]
    assert niches[0]["counts"] == {"trends": 3, "upcoming": 2}
    assert sorted(niches[0]["sources"]) == ["federal_register", "hn", "wikimedia_pageviews"]
    assert s.dropped_topics == 1 and s.dropped_events == 1


def test_internal_sources_only_with_flag_and_only_under_custom(tmp_path):
    proj = Project(name="p", urls=["https://example.com"], niches=[Niche(name="birds")])
    c = Config(projects=[proj], data_dir=tmp_path / "d", base_dir=tmp_path)
    with Store.open(c) as s:
        s.save_topics(None, "p", "birds", [
            Topic(key="sale", label="App sale", niche="birds", score=40.0, why="apple",
                  signals=[sig("apple", "App sale", "https://apps.apple.com/us/app/x/id1")]),
            Topic(key="mixed", label="Mixed story", niche="birds", score=60.0, why="hn + google_trends",
                  signals=[sig("hn", "Mixed story", "https://news.ycombinator.com/item?id=3", score=50),
                           sig("google_trends", "mixed story", "https://trends.google.com/x", rank=2),
                           sig("github", "mixed-repo", "https://github.com/a/b", score=10)]),
            Topic(key="pub", label="Public story", niche="birds", score=30.0, why="hn",
                  signals=[sig("hn", "Public story", "https://news.ycombinator.com/item?id=4", score=30)]),
        ])
        s.save_events("p", [Event(kind="holiday", title="Bank holiday", date=TODAY + timedelta(days=3), source="nager", niche="birds")])
    key = "k_0123456789abcdef"
    # public export: no internal source at all
    off = hx.export(c, tmp_path / "a", custom_key=key, niche_id="custom-ex-com", now=NOW)
    assert off.niches["custom-ex-com"] == {"trends": 2, "upcoming": 0}
    blob = "".join(p.read_text() for p in (tmp_path / "a").rglob("*.json"))
    assert not any(x in blob for x in ("apple", "google_trends", "github", "nager", '"scope"'))
    # internal export under an internal key: tagged
    on = hx.export(c, tmp_path / "b", custom_key=key, niche_id="custom-ex-com", include_internal=True, now=NOW)
    assert on.niches["custom-ex-com"] == {"trends": 3, "upcoming": 1}
    base = tmp_path / f"b/custom/{key}/custom-ex-com"
    items = {i["title"]: i for i in load(base / "trends.json")["items"]}
    assert items["App sale"]["scope"] == "internal" and items["App sale"]["source"] == "apple"
    assert items["Mixed story"]["scope"] == "internal"            # public primary, internal evidence -> whole item internal
    assert items["Mixed story"]["source"] == "hn" and set(items["Mixed story"]["sources"]) == {"github", "google_trends", "hn"}
    assert "scope" not in items["Public story"]
    ev = load(base / "evidence.json")["items"][items["Mixed story"]["id"]]
    assert ev["scope"] == "internal"
    assert {e["source"]: e.get("scope") for e in ev["evidence"]} == {"hn": None, "google_trends": "internal", "github": "internal"}
    assert load(base / "upcoming.json")["items"][0]["scope"] == "internal"
    # internal data can never be written under catalog/ (readable by external keys)
    with pytest.raises(hx.ExportError):
        hx.export(c, tmp_path / "c", include_internal=True, now=NOW)
    assert not (tmp_path / "c").exists()
    assert hx.main(["-c", str(_write_cfg(tmp_path, c)), "--out", str(tmp_path / "d"), "--include-internal"]) == 2


def _write_cfg(tmp_path, c):
    f = tmp_path / "x.yaml"
    f.write_text("data_dir: %s\nprojects:\n  - name: p\n    niches:\n      - name: birds\n" % c.data_dir)
    return f


def test_custom_niche_merges_whole_project_under_key(cfg, tmp_path):
    out = tmp_path / "o"
    key = "k_0123456789abcdef"
    hx.export(cfg, out, project="catalog", custom_key=key, niche_id="custom-example-com", name="example.com", now=NOW)
    assert (out / f"custom/{key}/custom-example-com/trends.json").is_file()
    assert not (out / "catalog").exists()
    assert {i["title"] for i in load(out / f"custom/{key}/custom-example-com/trends.json")["items"]} >= {"Merlin Bird ID"}
    hx.export(cfg, out, project="catalog", custom_key=key, niche_id="custom-other-org", now=NOW)
    ids = [n["id"] for n in load(out / f"custom/{key}/niches.json")["niches"]]
    assert ids == ["custom-example-com", "custom-other-org"]          # merged, not overwritten
    assert load(out / f"custom/{key}/niches.json")["niches"][0]["url"] == "https://example.com"


def test_rejects_bad_ids(cfg, tmp_path):
    with pytest.raises(hx.ExportError):
        hx.export(cfg, tmp_path, custom_key="nope", niche_id="custom-x")
    with pytest.raises(hx.ExportError):
        hx.export(cfg, tmp_path, project="catalog", custom_key="k_0123456789abcdef", niche_id="catalog-x")
    clash = Config(projects=[Project(name="p", niches=[Niche(name="Custom Stuff")])], data_dir=tmp_path / "d2", base_dir=tmp_path)
    with pytest.raises(hx.ExportError):
        hx.export(clash, tmp_path / "x")


def test_upload_commands_are_wrangler_put_and_not_run(cfg, tmp_path):
    out = tmp_path / "o"
    hx.export(cfg, out, now=NOW)
    cmds = hx.upload_commands(out, "nicheclock-data")
    assert len(cmds) == 7 and all(c[:5] == ["npx", "wrangler", "r2", "object", "put"] for c in cmds)
    assert any(c[5] == "nicheclock-data/catalog/niches.json" for c in cmds)
    assert all("--remote" in c and "application/json" in c for c in cmds)


def test_cli_prints_summary_and_upload(cfg, tmp_path, capsys):
    f = tmp_path / "nicheclock.yaml"
    f.write_text("data_dir: %s\nprojects:\n  - name: catalog\n    niches:\n      - name: Birdwatching\n" % cfg.data_dir)
    assert hx.main(["-c", str(f), "--out", str(tmp_path / "cli-out"), "--print-upload", "bkt"]) == 0
    out = capsys.readouterr().out
    assert "wrangler r2 object put bkt/catalog/niches.json" in out
    assert hx.main(["-c", str(f), "--out", str(tmp_path / "x"), "--custom-key", "bad"]) == 2


def test_region_fields_pass_through_and_are_recomputed_after_dropping_signals(tmp_path):
    proj = Project(name="p", niches=[Niche(name="birds")])
    c = Config(projects=[proj], data_dir=tmp_path / "d", base_dir=tmp_path)

    def rs(source, term, region, precision, url=None):
        s = sig(source, term, url)
        s.region, s.region_precision = region, precision
        return s

    with Store.open(c) as st:
        st.save_topics(None, "p", "birds", [
            # US hn-less story: wikipedia(US, approx) + apple(GB, exact) -> combined 'multi' [GB, US]; apple is dropped when public
            Topic(key="both", label="Both countries", niche="birds", score=50.0, why="x", region="multi", region_precision="exact",
                  regions=["GB", "US"],
                  signals=[rs("wikipedia", "Both countries", "US", "approx", "https://en.wikipedia.org/wiki/B"),
                           rs("apple", "both", "GB", "exact")]),
            Topic(key="glob", label="Global story", niche="birds", score=30.0, why="hn", signals=[rs("hn", "Global story", "global", "global", "https://news.ycombinator.com/item?id=5")]),
        ])
        st.save_events("p", [Event(kind="release", title="US deadline", date=TODAY + timedelta(days=4), source="federal_register",
                                   url="https://www.federalregister.gov/d/9", region="US", region_precision="exact", niche="birds")])
    hx.export(c, tmp_path / "o", custom_key="k_0123456789abcdef", niche_id="custom-ex-com", now=NOW)
    base = tmp_path / "o/custom/k_0123456789abcdef/custom-ex-com"
    items = {i["title"]: i for i in load(base / "trends.json")["items"]}
    both = items["Both countries"]
    assert (both["region"], both["region_precision"], both["regions"]) == ("US", "approx", ["US"])   # apple (GB) was dropped -> recomputed
    assert (items["Global story"]["region"], items["Global story"]["regions"]) == ("global", [])
    ev = load(base / "evidence.json")["items"][both["id"]]["evidence"][0]
    assert (ev["region"], ev["region_precision"]) == ("US", "approx")
    up = load(base / "upcoming.json")["items"][0]
    assert (up["region"], up["region_precision"]) == ("US", "exact")
    # internal export keeps the combined 'multi' tag
    hx.export(c, tmp_path / "i", custom_key="k_0123456789abcdef", niche_id="custom-ex-com", include_internal=True, now=NOW)
    both_i = {i["title"]: i for i in load(tmp_path / "i/custom/k_0123456789abcdef/custom-ex-com/trends.json")["items"]}["Both countries"]
    assert (both_i["region"], both_i["regions"]) == ("multi", ["GB", "US"])

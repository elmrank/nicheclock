"""The digests the hosted MCP tools trending_now / upcoming_events read (nicheclock/catalog/digest.py): the format the Worker
(hosted/src/catalog-tools.ts) expects, and a size budget, because the Worker has 10 ms of CPU per request."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nicheclock.catalog import digest

REAL = Path(__file__).resolve().parents[1] / "hosted" / "site" / "catalog-data"
BUDGET = 110_000      # bytes of one digest file: about 1 to 2 ms of JSON.parse


def _topic(**o):
    base = {
        "qid": "Q42", "label": "Example", "category": "sports", "trend": "rising", "status": "active", "volume": 12345.0,
        "volume_unit": "pageviews/day (first 10 unique per device)", "wikipedia_url": "https://en.wikipedia.org/wiki/Example",
        "rising_channels": ["wikipedia"],
        "windows": {"24h": {"ratio": 3.456, "active": True}, "48h": {"ratio": 2.0, "active": False}, "7d": {"ratio": 1.2, "active": False}},
        "channels": {"wikipedia": {"value": 12345.0, "unit": "pageviews/day (first 10 unique per device)", "ratio": 3.5, "source": "wikimedia_pageviews",
                                   "license": "CC0"}},
    }
    base.update(o)
    return base


@pytest.fixture()
def catalog(tmp_path):
    c = tmp_path / "catalog-data"
    c.mkdir()
    (c / "meta.json").write_text(json.dumps({
        "regions": [{"id": "global", "label": "Worldwide"}, {"id": "ET", "label": "Ethiopia", "limited": True, "limited_note": "thin"}],
        "categories": [{"id": "sports", "label": "Sports"}],
        "sources": [{"id": "wikimedia_pageviews", "label": "Wikipedia pageviews", "url": "https://dumps.wikimedia.org/"}],
    }))
    (c / "trending.json").write_text(json.dumps({"date": "2026-11-02", "generated_at": "2026-11-03T06:43Z", "topics": [
        _topic(),
        _topic(qid="openlibrary:Some Book", label="Some Book", trend="steady", status="steady", volume=None, volume_unit=None, windows={},
               rising_channels=[], channels={"openlibrary": {"value": 3, "unit": "rank on the daily list", "source": "openlibrary", "license": "x"}}),
        _topic(qid="Q43", label="Gone", trend="fading", status="ended"),
        _topic(qid="Q44", label="Past peak", trend="fading", status="fading"),
    ]}))
    (c / "trending-ET.json").write_text(json.dumps({"date": "2026-11-02", "topics": []}))
    (c / "upcoming.json").write_text(json.dumps({"today": "2026-11-03", "lag_note": "defaults", "events": [
        {"title": "Cup", "date": "2026-12-01", "publish_by": "2026-11-24", "lag_days": 7, "kind": "sports event", "category": "sports",
         "region": "global", "source": "wikidata", "source_url": "https://www.wikidata.org/wiki/Q1", "license": "CC0"},
        {"title": "Holiday", "date": "2026-12-02", "publish_by": "2026-11-04", "lag_days": 28, "kind": "holiday", "category": "other",
         "region": "IN", "regions": ["IN"], "source": "holidays", "source_url": "https://example.org/", "license": "MIT"},
    ]}))
    return c


def test_rows_and_index(catalog, tmp_path):
    out = tmp_path / "tools"
    sizes = digest.build(catalog, out)
    assert set(sizes) == {"trending-global.json", "trending-ET.json", "events-2026-12.json", "index.json"}
    rows = json.loads((out / "trending-global.json").read_text())["rows"]
    assert rows[0] == {
        "q": "Q42", "l": "Example", "c": "sports", "s": "rising", "r": [3.46, 2.0, 1.2], "v": 12345, "u": "pageviews/day", "x": ["wikipedia"],
        "w": "Wikipedia 12,345 pageviews/day, 3.5x its 14-day median", "a": "https://en.wikipedia.org/wiki/Example", "y": ["wikipedia"],
        "z": [True, False, False],
    }
    assert [r["s"] for r in rows] == ["rising", "steady", "ended", "fading"]
    assert rows[1]["q"] is None and rows[1]["v"] is None and rows[1]["u"] is None and rows[1]["r"] == [None, None, None]
    assert rows[1]["w"].startswith("Steady top item, not rising: ")
    assert "z" not in rows[1] and "z" not in rows[2]            # only a rising row carries per-window activity
    idx = json.loads((out / "index.json").read_text())
    assert [r["id"] for r in idx["regions"]] == ["global", "ET"]
    assert idx["regions"][1] == {"id": "ET", "label": "Ethiopia", "topics": 0, "date": "2026-11-02", "limited": True, "limited_note": "thin"}
    assert idx["categories"] == {"sports": "Sports"}
    assert idx["kinds"] == ["holiday", "sports event"]
    assert idx["licenses"]["wikipedia"] == {"source": "wikimedia_pageviews", "label": "Wikipedia pageviews", "license": "CC0", "url": "https://dumps.wikimedia.org/"}
    assert idx["units"]["pageviews/day"].startswith("pageviews/day (first 10")


def test_events(catalog, tmp_path):
    out = tmp_path / "tools"
    digest.build(catalog, out)
    doc = json.loads((out / "events-2026-12.json").read_text())
    idx = json.loads((out / "index.json").read_text())
    assert idx["events_today"] == "2026-11-03" and idx["lag_note"] == "defaults" and idx["event_months"] == {"2026-12": 2}
    assert doc["events"][1] == {"t": "Holiday", "d": "2026-12-02", "p": "2026-11-04", "g": 28, "k": "holiday", "c": "other", "e": ["IN"], "o": "IN",
                                "n": "holidays", "a": "https://example.org/", "i": "MIT"}
    assert doc["events"][0]["e"] is None


def test_the_sample_fixture_has_no_digest(tmp_path):
    c = tmp_path / "c"
    c.mkdir()
    (c / "meta.json").write_text(json.dumps({"regions": ["all", "global"], "categories": []}))
    (c / "upcoming.json").write_text(json.dumps({"today": "2026-11-03", "events": []}))
    assert set(digest.build(c, tmp_path / "t")) == {"index.json"}


@pytest.mark.skipif(not (REAL / "meta.json").is_file(), reason="no catalog-data in this checkout")
def test_real_catalog_fits_the_cpu_budget(tmp_path):
    sizes = digest.build(REAL, tmp_path / "tools")
    assert sizes, "nothing built"
    assert max(sizes.values()) <= BUDGET, {k: v for k, v in sizes.items() if v > BUDGET}

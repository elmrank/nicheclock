"""Catalog upcoming: kinds, publish_by lags, holidays, Federal Register (no network)."""
from datetime import date

import pytest

from nicheclock.catalog import upcoming as up
from nicheclock.catalog.categories import Classification

TODAY = date(2026, 11, 1)


def cls(category, via="Q1", kind="class"):
    return Classification(category, kind, via, "", 0)


@pytest.mark.parametrize("prop,c,kind", [
    ("release", cls("entertainment", "Q11424"), "film release"),
    ("release", cls("entertainment", "Q7366"), "single release"),
    ("release", cls("entertainment", "Q5398426"), "tv premiere"),
    ("release", cls("games", "Q7889"), "game release"),
    ("release", cls("technology", "Q7397"), "software release"),
    ("release", cls("other"), "release"),
    ("event", cls("sports", "P641", "property"), "sports event"),
    ("event", cls("politics", "Q40231"), "election"),
    ("event", cls("entertainment", "Q132241"), "festival"),
    ("event", cls("entertainment", "Q4504495"), "award ceremony"),
    ("event", cls("other"), "event"),
])
def test_kind_from_property_and_class(prop, c, kind):
    assert up.kind_for(prop, c) == kind


def test_every_kind_has_a_lag_and_unknown_kinds_get_the_event_default():
    for kind in ("film release", "sports event", "election", "holiday", "comment deadline", "effective date"):
        assert up.lag_for(kind) > 0
    assert up.lag_for("something new") == up.LAG_DAYS["event"]
    assert up.lag_for("election") > up.lag_for("sports event")        # lead time differs by type


def test_event_publish_by_is_date_minus_lag_and_carries_provenance():
    e = up._event("Some election", "2026-11-21", "election", up.lag_for("election"), "politics", "global",
                  "wikidata", "https://www.wikidata.org/wiki/Q1", up.WD_LICENSE, qid="Q1")
    assert e["publish_by"] == "2026-10-31" and e["lag_days"] == 21 and e["date_precision"] == "day"
    assert e["category_label"] == "Politics" and e["source"] == "wikidata" and e["license"] and e["source_url"]


def test_query_uses_exact_dates_so_the_public_endpoint_does_not_time_out():
    q = up._query("P585", [date(2026, 11, 1), date(2026, 11, 2)])
    assert '"2026-11-01T00:00:00Z"^^xsd:dateTime "2026-11-02T00:00:00Z"^^xsd:dateTime' in q
    assert "wikibase:timePrecision 11" in q and f"?sl >= {up.MIN_SITELINKS}" in q and "FILTER(?d" not in q


def test_holidays_group_countries_and_keep_only_the_next_year():
    pytest.importorskip("holidays")
    ev, st = up.holiday_events(TODAY, ["US", "GB"])
    assert st["ok"] and ev
    assert all(TODAY.isoformat() <= e["date"] <= "2027-11-01" for e in ev)
    assert all(e["kind"] == "holiday" and e["source"] == "holidays" and e["license"] and e["source_url"] for e in ev)
    xmas = [e for e in ev if e["date"] == "2026-12-25"]
    assert len(xmas) == 1 and xmas[0]["regions"] == ["GB", "US"] and xmas[0]["region"] == "global"
    thanks = [e for e in ev if e["date"] == "2026-11-26"]
    assert thanks and thanks[0]["regions"] == ["US"] and thanks[0]["region"] == "US"
    assert thanks[0]["publish_by"] == "2026-10-29"


class FakeClassifier:
    def classify_agency(self, slugs):
        return "health" if "food-and-drug-administration" in slugs else "law-government"


class FakeFR:
    def __init__(self, pages):
        self.pages, self.params = pages, []

    def get_json(self, url, params=None, **kw):
        self.params.append(params)
        return self.pages.pop(0)


def test_federal_register_events_deadlines_and_effective_dates():
    doc = {"title": "Labeling of Widgets", "html_url": "https://www.federalregister.gov/d/2026-1",
           "comments_close_on": "2026-11-20", "effective_on": "2026-12-30",
           "agencies": [{"slug": "food-and-drug-administration", "name": "Food and Drug Administration"}]}
    stale = dict(doc, html_url="https://www.federalregister.gov/d/2026-2", comments_close_on="2026-10-01", effective_on=None)
    c = FakeFR([{"results": [doc, stale], "total_pages": 1}, {"results": [doc], "total_pages": 1}])
    ev, st = up.federal_register_events(c, FakeClassifier(), TODAY)
    assert st["ok"]
    by_kind = {e["kind"]: e for e in ev}
    assert set(by_kind) == {"comment deadline", "effective date"} and len(ev) == 2
    d = by_kind["comment deadline"]
    assert d["date"] == "2026-11-20" and d["publish_by"] == "2026-11-06" and d["category"] == "health"
    assert d["region"] == "US" and d["license"].startswith("US government work") and d["source_url"].startswith("https://")
    assert by_kind["effective date"]["date"] == "2026-12-30"
    assert c.params[0]["conditions[comment_date][gte]"] == "2026-11-01" and c.params[0]["conditions[significant]"] == 1

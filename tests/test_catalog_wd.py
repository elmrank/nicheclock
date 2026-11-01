"""Wikidata wrapper: compact records, title -> QID, API errors, caching (fake client, no network)."""
import pytest

from nicheclock.catalog.cache import Cache
from nicheclock.catalog.wd import Wikidata, compact_entity
from nicheclock.http import FetchError


def raw(qid, label, p31=(), sitelinks=None, rank="normal"):
    claims = {"P31": [{"rank": rank, "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": q}}}} for q in p31]}
    return {"id": qid, "labels": {"en": {"language": "en", "value": label}},
            "descriptions": {"en": {"value": "d"}}, "aliases": {"en": [{"value": "alias"}]},
            "sitelinks": {s: {"site": s, "title": t} for s, t in (sitelinks or {}).items()}, "claims": claims}


class FakeClient:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get_json(self, url, params=None, **kw):
        self.calls.append(params)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_compact_entity_keeps_tracked_statements_and_counts_sitelinks():
    r = compact_entity(raw("Q1968", "Formula One", p31=["Q13406554"], sitelinks={"enwiki": "Formula One", "eswiki": "Fórmula 1", "xxwiki": "x"}))
    assert r["claims"] == {"P31": ["Q13406554"]} and r["sitelinks"] == 3
    assert r["titles"] == {"enwiki": "Formula One", "eswiki": "Fórmula 1"} and r["aliases"] == ["alias"]
    assert compact_entity({"id": "Q9", "missing": ""}) is None
    assert compact_entity(raw("Q2", "x", p31=["Q5"], rank="deprecated"))["claims"] == {}


def test_entities_batches_caches_and_marks_missing():
    c = FakeClient([{"entities": {"Q1": raw("Q1", "One"), "Q2": {"id": "Q2", "missing": ""}}}])
    wd = Wikidata(c, Cache(":memory:"))
    out = wd.entities(["Q1", "Q2", "Q1"])
    assert out["Q1"]["label"] == "One" and out["Q2"] is None
    assert wd.entities(["Q1", "Q2"]) == out and len(c.calls) == 1
    assert c.calls[0]["ids"] == "Q1|Q2"


def test_qids_for_titles_maps_titles_without_normalize_and_caches_misses():
    c = FakeClient([{"entities": {"Q1968": raw("Q1968", "Formula One", sitelinks={"enwiki": "Formula One"}),
                                  "-1": {"site": "enwiki", "title": "Nope", "missing": ""}}}])
    wd = Wikidata(c, Cache(":memory:"))
    out = wd.qids_for_titles("enwiki", ["Formula_One", "Nope"])
    assert out == {"Formula_One": "Q1968", "Nope": None}
    assert "normalize" not in c.calls[0] and c.calls[0]["titles"] == "Formula One|Nope"   # many titles: normalize is illegal
    assert wd.qids_for_titles("enwiki", ["Nope", "Formula_One"]) == {"Nope": None, "Formula_One": "Q1968"} and len(c.calls) == 1


def test_error_object_in_a_200_response_is_a_failure_not_an_empty_result():
    c = FakeClient([{"error": {"code": "params-illegal", "info": "bad"}}])
    wd = Wikidata(c, Cache(":memory:"))
    assert wd.qids_for_titles("enwiki", ["A", "B"]) == {}                 # nothing cached as "no QID"
    assert wd.cache.get("sl2:enwiki", "A")[0] is False


def test_search_returns_match_info_and_survives_http_errors():
    c = FakeClient([{"search": [{"id": "Q1", "label": "Mercury", "description": "planet", "match": {"type": "label", "text": "Mercury"}}]},
                    FetchError("boom")])
    wd = Wikidata(c, Cache(":memory:"))
    assert wd.search("Mercury")[0] == {"id": "Q1", "label": "Mercury", "description": "planet", "match": "label", "match_text": "Mercury"}
    assert wd.search("Other") == []


def test_sparql_flattens_bindings_and_caches():
    c = FakeClient([{"results": {"bindings": [{"item": {"value": "http://www.wikidata.org/entity/Q1"}, "sl": {"value": "9"}}]}}])
    wd = Wikidata(c, Cache(":memory:"))
    assert wd.sparql("SELECT 1") == [{"item": "http://www.wikidata.org/entity/Q1", "sl": "9"}]
    assert wd.sparql("SELECT 1") and len(c.calls) == 1

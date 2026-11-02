"""The niche pool (pool.py), the bulk Wikidata reader (wdbulk.py) and recurring observances (observances.py): no network."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta

import pytest

from nicheclock.catalog import observances, pool
from nicheclock.catalog.wdbulk import MISSING, BulkFetcher, EntityStore, Throttled, lean_entity


def rec(label, c=None, d="", a=None):
    return {"l": label, "d": d, "a": a or [], "c": c or {}}


def store_with(records):
    s = EntityStore(":memory:")
    s.put_many(records)
    return s


def test_handles_round_trip_and_stay_exact_for_big_qids():
    h = pool.encode("Q141756646", pool.KIND_TAXON, 12)
    assert pool.decode(h) == ("Q141756646", pool.KIND_TAXON, 12)
    assert h > 2 ** 32


def _samples(views_by_day):
    """{day: {"US": {...}}} with a page per qid; ``views_by_day[qid]`` lists 22 daily values (None = not published)."""
    end = date(2026, 10, 31)
    out = {}
    for i in range(22):
        d = end - timedelta(days=21 - i)
        pages = {q: [v[i], f"T{q}", "en.wikipedia"] for q, v in views_by_day.items() if v[i] is not None}
        out[d] = {"US": {"rows": len(pages), "floor": 90.0, "pages": pages}}
    return out, end


def test_pool_has_rising_and_steady_pages_and_drops_the_rest():
    flat = [100] * 21
    samples, end = _samples({
        "Q1": flat + [260],            # rising: 260 views, 2.6x its median
        "Q2": [250] * 22,              # steady: 250 views, ratio 1
        "Q3": [150] * 22,              # neither: 150 views is under the steady floor and not rising
        "Q4": [100] * 21 + [119],      # rising ratio but under 120 views
        "Q5": [None] * 21 + [130],     # new: 130 views against the floor, so a lower-bound ratio
    })
    got = {p["qid"]: p for p in pool.select_pages(samples, end, "US")}
    assert set(got) == {"Q1", "Q2", "Q5"}
    assert got["Q1"]["rising"] and got["Q1"]["ratio"] == 2.6 and got["Q1"]["series"][-1] == 260 and len(got["Q1"]["series"]) == 7
    assert not got["Q2"]["rising"] and got["Q2"]["series"] is None
    assert got["Q5"]["rising"] and got["Q5"]["ratio"] == pytest.approx(130 / 90, abs=0.01)


def test_ratio_is_capped_and_extra_pages_are_kept():
    samples, end = _samples({"Q1": [100] * 21 + [5000], "Q9": [100] * 22})
    got = {p["qid"]: p for p in pool.select_pages(samples, end, "US", extra=["Q9"])}
    assert got["Q1"]["ratio"] == pool.RATIO_CAP
    assert "Q9" in got and got["Q9"]["keep"]


def test_walk_reads_classes_occupations_subjects_taxa_and_flags():
    s = store_with({
        "Q1": rec("Boris", {"P31": ["Q5"], "P106": ["Q2", "Q3"], "P641": ["Q718"]}),
        "Q5": rec("human"), "Q2": rec("chess player", {"P279": ["Q4"]}), "Q3": rec("writer"), "Q4": rec("puzzle player"),
        "Q718": rec("chess"),
        "Q10": rec("Milkshake Duck", {"P31": ["Q11"]}), "Q11": rec("fictional bird", {"P279": ["Q12"]}), "Q12": rec("bird"),
        "Q20": rec("House sparrow", {"P171": ["Q21"], "P105": ["Q7432"]}), "Q21": rec("Passer", {"P171": ["Q22"], "P105": ["Q34740"]}),
        "Q22": rec("Passeridae", {"P171": ["Q23"], "P105": ["Q35409"]}), "Q23": rec("Passeriformes", {"P171": ["Q24"], "P105": ["Q36602"]}),
        "Q24": rec("Aves", {"P105": ["Q37517"]}),
    })
    miss: set = set()
    w = pool.walk(s, "Q1", miss)
    assert not miss
    assert w.person and w.nocc == 2 and w.occupations == {"Q2": 0, "Q3": 0, "Q4": 1}
    assert w.subjects == [("Q718", 0)]
    f = pool.walk(s, "Q10", miss)
    assert f.fictional and f.classes == {"Q11": 1, "Q12": 2}
    t = pool.walk(s, "Q20", miss)
    assert [x[:2] for x in t.taxa] == [("Q21", 1), ("Q22", 2), ("Q23", 3), ("Q24", 4)]
    assert not t.fictional and t.is_taxon and t.self_rank == "Q7432"
    assert pool.flags_of(t, True) & pool.FLAG_CLADE == 0
    assert pool.flags_of(pool.walk(s, "Q24", miss), False) & pool.FLAG_CLADE


def test_walk_reports_what_it_still_needs():
    s = store_with({"Q1": rec("A", {"P31": ["Q2"]})})
    miss: set = set()
    w = pool.walk(s, "Q1", miss)
    assert w is not None and miss == {"Q2"}
    missing: set = set()
    assert pool.walk(s, "Q404", missing) is None and missing == {"Q404"}
    assert pool.walk(store_with({"Q5": None}), "Q5", set()) is None


def test_handles_drop_generic_classes_and_keep_the_rarest_first():
    s = store_with({
        "Q1": rec("Thing", {"P31": ["Q100", "Q101"], "P641": ["Q718"], "P921": ["Q719"]}),
        "Q100": rec("film"), "Q101": rec("chess opening"), "Q718": rec("chess"), "Q719": rec("openings"),
    })
    w = pool.walk(s, "Q1", set())
    df = Counter({"Q100": 40, "Q101": 3, "Q718": 9, "Q719": 2})
    h = pool.select_handles(w, df, 1000)                  # film is on 4% of the pool: generic, dropped
    got = [pool.decode(x) for x in h]
    assert ("Q719", pool.KIND_SUBJECT, 1) in got and ("Q718", pool.KIND_SUBJECT, 0) in got
    assert got.index(("Q719", pool.KIND_SUBJECT, 1)) < got.index(("Q718", pool.KIND_SUBJECT, 0))     # rarer first
    assert ("Q101", pool.KIND_CLASS, 1) in got and not any(g[0] == "Q100" for g in got)


def test_taxon_handles_prefer_class_order_family_and_skip_clades_without_a_rank():
    s = store_with({
        "Q20": rec("House sparrow", {"P171": ["Q21"]}), "Q21": rec("Passer", {"P171": ["Q22"], "P105": ["Q34740"]}),
        "Q22": rec("Passeridae", {"P171": ["Q23"], "P105": ["Q35409"]}), "Q23": rec("Passeriformes", {"P171": ["Q25"], "P105": ["Q36602"]}),
        "Q25": rec("Neognathae", {"P171": ["Q24"]}), "Q24": rec("Aves", {"P105": ["Q37517"]}),
    })
    w = pool.walk(s, "Q20", set())
    got = [pool.decode(x) for x in pool.select_handles(w, Counter(), 100)]
    assert [g[0] for g in got if g[1] == pool.KIND_TAXON] == ["Q24", "Q23", "Q22"]      # class, order, family (3 handles)
    assert ("Q25", pool.KIND_TAXON, 4) not in got                                       # Neognathae has no rank


def test_resolve_fetches_what_walks_miss_round_by_round():
    world = {"Q1": rec("A", {"P31": ["Q2"]}), "Q2": rec("B", {"P279": ["Q3"]}), "Q3": rec("C")}
    s = EntityStore(":memory:")

    class F:
        calls = 0
        failed: list = []

        def fetch(self, qids):
            self.calls += 1
            new = {q: world.get(q) for q in qids if s.get(q) is MISSING}
            s.put_many(new)
            return len(new)

    f = F()
    walks = pool.resolve(s, f, ["Q1"], log=lambda m: None)
    assert walks["Q1"].classes == {"Q2": 1, "Q3": 2}
    assert f.calls == 3


def test_row_layout_flags_and_budget_floor():
    s = store_with({"Q1": rec("Magnus", {"P31": ["Q5"], "P106": ["Q2"]}), "Q2": rec("chess player"), "Q5": rec("human")})
    w = pool.walk(s, "Q1", set())
    region = pool.Region("US", [{"qid": "Q1", "title": "Magnus Carlsen", "views": 942, "ratio": 1.6, "rising": True, "series": [1, 2, 3, 4, 5, 6, 7]}], {"Q1": w})
    rows, stats = pool.region_rows(region)
    assert rows == [[1, "Magnus", 942, 1.6, [pool.encode("Q2", 1, 0)], [1, 2, 3, 4, 5, 6, 7], 1 | 8 | (1 << 4)]]
    assert stats["rising"] == 1 and stats["with_handles"] == 1
    # a steady page under the floor is left out, a rising one is not
    steady = {"qid": "Q1", "title": "x", "views": 210, "ratio": 1.0, "rising": False, "series": None}
    assert pool.region_rows(pool.Region("US", [steady], {"Q1": w}), 250)[0] == []
    assert len(pool.region_rows(pool.Region("US", [steady], {"Q1": w}), 200)[0]) == 1


def test_junk_never_reaches_the_pool():
    s = store_with({"Q1": rec("Pornhub", {"P31": ["Q2"]}), "Q2": rec("website"), "Q3": rec("List of chess players", {"P31": ["Q4"]}), "Q4": rec("Wikimedia list article"),
                    "Q6": rec("Chess", {"P31": ["Q7"]}), "Q7": rec("puzzle")})
    pages = [{"qid": q, "title": t, "views": 500, "ratio": 1.0, "rising": False, "series": None} for q, t in (("Q1", "Pornhub"), ("Q3", "List of chess players"), ("Q6", "Chess"))]
    region = pool.Region("US", pages, {p["qid"]: pool.walk(s, p["qid"], set()) for p in pages})
    rows, stats = pool.region_rows(region)
    assert [r[1] for r in rows] == ["Chess"]
    assert sum(stats["dropped"].values()) == 2


def test_fit_budget_raises_the_steady_floor_until_the_file_fits(monkeypatch):
    s = store_with({f"Q{i}": rec(f"Page {i}") for i in range(1, 400)})
    pages = [{"qid": f"Q{i}", "title": f"Page {i}", "views": 200 + i, "ratio": 1.0, "rising": False, "series": None} for i in range(1, 400)]
    region = pool.Region("BR", pages, {p["qid"]: pool.walk(s, p["qid"], set()) for p in pages})
    monkeypatch.setitem(pool.BUDGET_GZ, "BR", 2500)
    rows, stats, size = pool.fit_budget(region, log=lambda m: None)
    assert size <= 2500 and len(rows) < 399 and "steady floor raised" in stats["note"]


# -- wdbulk ---------------------------------------------------------------------------------------------

class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, script):
        self.script, self.headers, self.seen = script, {}, []

    def get(self, url, params=None, timeout=None):
        self.seen.append(params)
        return self.script(params)


def entity_body(ids):
    return {"entities": {i: {"id": i, "labels": {"en": {"value": "L" + i}}, "descriptions": {"en": {"value": "d"}}, "aliases": {},
                             "claims": {"P31": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5"}}}}],
                                        "P279": [{"rank": "deprecated", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q9"}}}}]}}
                         for i in ids.split("|")}, "success": 1}


def make_fetcher(script, sleeps, threads=1):
    store = EntityStore(":memory:")
    f = BulkFetcher(store, "nicheclock/0.1 (+https://example.org)", "oss@example.org", threads=threads, sleep=sleeps.append, log=lambda m: None,
                    session_factory=lambda: FakeSession(script))
    return f, store


def test_lean_entity_keeps_tracked_claims_and_skips_deprecated():
    e = lean_entity(entity_body("Q1")["entities"]["Q1"])
    assert e["l"] == "LQ1" and e["c"] == {"P31": ["Q5"]}
    assert lean_entity({"id": "Q2", "missing": ""}) is None


def test_fetch_batches_of_50_with_identity_and_maxlag():
    seen = []

    def script(params):
        seen.append(params)
        return Resp(200, entity_body(params["ids"]))

    f, store = make_fetcher(script, [])
    assert f.fetch([f"Q{i}" for i in range(1, 121)]) == 120
    assert sorted(len(p["ids"].split("|")) for p in seen) == [20, 50, 50]
    assert all(p["maxlag"] == 5 and p["action"] == "wbgetentities" for p in seen)
    assert store.get("Q77")["l"] == "LQ77"
    assert f.fetch(["Q1", "Q2"]) == 0                         # already in the store: no call
    assert f._session().headers["From"] == "oss@example.org"


def test_retry_after_is_honoured_and_four_throttles_in_a_row_stop_the_run():
    state = {"n": 0}

    def script(params):
        state["n"] += 1
        return Resp(429, None, {"Retry-After": "7"}) if state["n"] == 1 else Resp(200, entity_body(params["ids"]))

    sleeps: list = []
    f, store = make_fetcher(script, sleeps)
    assert f.fetch(["Q1"]) == 1
    assert sum(sleeps) >= 5 and state["n"] == 2

    f2, _ = make_fetcher(lambda p: Resp(429, None, {"Retry-After": "1"}), [])
    with pytest.raises(Throttled):
        f2.fetch(["Q1"])
    f3, _ = make_fetcher(lambda p: Resp(429, None, {"Retry-After": "9999"}), [])
    with pytest.raises(Throttled):
        f3.fetch(["Q1"])


def test_maxlag_answer_backs_off_and_one_bad_batch_does_not_lose_the_others():
    state = {"n": 0}

    def script(params):
        state["n"] += 1
        if state["n"] == 1:
            return Resp(200, {"error": {"code": "maxlag", "info": "lag"}}, {"Retry-After": "3"})
        if "Q60" in params["ids"]:
            return Resp(200, {"error": {"code": "badids", "info": "x"}})
        return Resp(200, entity_body(params["ids"]))

    f, store = make_fetcher(script, [])
    f.fetch([f"Q{i}" for i in range(1, 61)])
    assert store.get("Q1")["l"] == "LQ1" and store.get("Q55") is MISSING
    assert len(f.failed) == 10 and state["n"] >= 3


def test_store_keeps_none_for_missing_entities_and_expires_by_ttl():
    t = {"now": 1000.0}
    s = EntityStore(":memory:", clock=lambda: t["now"], ttl=100)
    s.put_many({"Q1": None, "Q2": rec("x")})
    assert s.get("Q1") is None and s.get("Q3") is MISSING
    t["now"] = 1050.0
    assert s.load() == 2                                    # fresh: read back into memory
    t["now"] = 1200.0
    assert s.load() == 0 and s.get("Q1") is MISSING         # older than the ttl: read again


# -- observances -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("label,rule", [
    ("July 20", ("fixed", 7, 20)), ("20 July", ("fixed", 7, 20)), ("second Saturday in May", ("nth", 2, 5, 5)),
    ("Last Sunday in October", ("nth", -1, 6, 10)), ("first Monday of September", ("nth", 1, 0, 9)),
    ("Easter + 60 days", None), ("August", None), ("15th day of the 8th month of the Chinese lunisolar calendar", None), ("Smarch 3", None), ("", None),
])
def test_rule_labels(label, rule):
    assert observances.parse_rule(label) == rule


def test_next_occurrence_of_fixed_and_weekday_rules_and_leap_day():
    assert observances.next_occurrence(("fixed", 7, 20), date(2026, 11, 2)) == date(2027, 7, 20)
    assert observances.next_occurrence(("fixed", 7, 20), date(2026, 7, 20)) == date(2026, 7, 20)
    assert observances.next_occurrence(("nth", 2, 5, 5), date(2026, 11, 2)) == date(2027, 5, 8)           # second Saturday in May 2027
    assert observances.next_occurrence(("nth", -1, 6, 10), date(2026, 11, 2)) == date(2027, 10, 31)       # last Sunday in October 2027
    assert observances.next_occurrence(("fixed", 2, 29), date(2026, 11, 2)) is None                       # 2027 has no 29 Feb; never guessed
    assert observances.next_occurrence(("fixed", 11, 1), date(2026, 11, 2), horizon=30) is None          # next one is 364 days away


def test_upcoming_picks_the_earliest_parseable_statement():
    labels = {"V1": "July 20", "V2": "second Saturday in May", "V3": "Easter + 60 days"}
    got = observances.upcoming([("Q1", "V1", 28), ("Q1", "V2", 28), ("Q2", "V3", 5), ("Q3", "V2", 3)], labels.get, date(2026, 11, 2))
    assert got["Q1"] == {"date": "2027-05-08", "rule": "second Saturday in May", "sitelinks": 28}
    assert "Q2" not in got and got["Q3"]["sitelinks"] == 3

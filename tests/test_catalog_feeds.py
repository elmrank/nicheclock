"""M2 feeds: GDACS, TVmaze, Stack Exchange, PyPI, Open Library, built from fake payloads (no network)."""
import json
from datetime import date

from nicheclock.catalog import feeds, regions as rg
from nicheclock.http import FetchError

DAY = date(2026, 11, 1)
END = "2026-10-31"


class Client:
    """get_json(url, params, **kw) answers from {url-substring: payload}; records the calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, params, kw))
        for k, v in self.routes.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return v(url, params) if callable(v) else v
        raise AssertionError(url)


def feat(name, level, iso3, etype="EQ", current="true", frm="2026-11-01T08:00:00"):
    return {"properties": {"eventtype": etype, "eventid": "1", "name": name, "htmldescription": f"{level} &amp; {name}", "alertlevel": level,
                           "iscurrent": current, "country": "X", "fromdate": frm, "iso3": iso3, "url": {"report": f"https://www.gdacs.org/r/{name}"}}}


def test_gdacs_current_events_most_severe_first_and_country_events_reach_their_region():
    data = {"features": [feat("Quake A", "Green", "TWN"), feat("Cyclone B", "Red", "IND", "TC"), feat("Old flood", "Orange", "BGD", "FL", current="false"),
                         feat("Flood C", "Orange", "BGD", "FL")]}
    out, st = feeds.gdacs_rows(Client({"gdacs.org": data}), END)
    assert st["status"] == "ok" and [r["label"] for r in out["global"]] == ["Cyclone B", "Flood C", "Quake A"]
    assert [r["label"] for r in out["IN"]] == ["Cyclone B"] and [r["label"] for r in out["BD"]] == ["Flood C"] and "TW" not in out
    r = out["global"][0]
    assert r["category"] == "climate" and r["trend"] == "steady" and r["rank_score"] == 0 and r["channels"]["gdacs"]["alert"] == "Red"
    assert r["source_url"] == "https://www.gdacs.org/r/Cyclone B" and r["license"].startswith("GDACS") and "&amp;" not in r["description"]
    assert r["channels"]["gdacs"]["source"] == "gdacs" and r["qid"] is None


def test_tvmaze_ranks_shows_by_its_own_weight_per_country_and_unions_us_and_web_for_worldwide():
    def ep(show_id, name, weight, net="Net"):
        return {"show": {"id": show_id, "name": name, "weight": weight, "type": "Scripted", "language": "English", "network": {"name": net}, "url": f"https://www.tvmaze.com/shows/{show_id}"}}

    def route(url, params):
        if url.endswith("/web"):
            return [ep(3, "Web Show", 70)]
        return {"US": [ep(1, "Big", 95), ep(2, "Small", 40), ep(1, "Big", 95)], "IN": [ep(4, "Hindi Show", 79)], "CN": []}.get(params["country"], [])

    regions = [r for r in rg.regions() if r["id"] in ("global", "US", "IN", "CN")]
    out, st = feeds.tvmaze_rows(Client({"api.tvmaze.com": route}), DAY, regions, END)
    assert [r["label"] for r in out["US"]] == ["Big", "Small"] and [r["label"] for r in out["IN"]] == ["Hindi Show"]
    assert [r["label"] for r in out["global"]] == ["Big", "Web Show", "Small"] and "CN" not in out        # empty schedule: no rows, no zeros
    big = out["US"][0]
    assert big["category"] == "entertainment" and big["volume"] == 95.0 and big["channels"]["tvmaze"]["airing_today"] and "tvmaze.com" in big["source_url"]
    assert "CC BY-SA" in big["license"] and st["status"] == "ok"


def test_stackexchange_keeps_only_tag_names_counts_and_links():
    items = [{"tags": ["python", "pandas"], "title": "SECRET QUESTION TEXT"}, {"tags": ["python"], "title": "x"}, {"tags": ["rust"]}]
    out, st = feeds.stackexchange_rows(Client({"stackexchange.com": {"items": items}}), END)
    rows = out["global"]
    assert [(r["label"], r["volume"]) for r in rows][:2] == [("python", 2.0), ("pandas", 1.0)]
    assert rows[0]["source_url"] == "https://stackoverflow.com/questions/tagged/python" and rows[0]["category"] == "technology"
    assert "SECRET" not in json.dumps(rows) and st["status"] == "ok"


def stats_payload(values, start=date(2026, 10, 3)):
    from datetime import timedelta

    return {"data": [{"category": "without_mirrors", "date": (start + timedelta(days=i)).isoformat(), "downloads": v} for i, v in enumerate(values)]
            + [{"category": "with_mirrors", "date": start.isoformat(), "downloads": 1}]}


def test_pypi_rows_have_a_real_ratio_and_only_rising_when_it_is():
    top = {"rows": [{"project": "boom"}, {"project": "steady"}, {"project": "old"}]}

    def route(url, params):
        if "top-pypi" in url:
            return top
        assert params == {"mirrors": "false"}
        if "boom" in url:
            return stats_payload([1000] * 27 + [3000, 500])          # last (partial) day is ignored: 3000 vs 1000
        if "steady" in url:
            return stats_payload([5000] * 29)
        return stats_payload([900] * 29, start=date(2026, 9, 1))      # stale series: skipped

    c = Client({"": route})
    out, st = feeds.pypi_rows(c, date(2026, 10, 31), END, top=3)
    rows = out["global"]
    assert [r["label"] for r in rows] == ["boom", "steady"]
    assert (rows[0]["trend"], rows[0]["ratio"], rows[0]["rise"]) == ("rising", 3.0, "strong") and rows[0]["rank_score"] > 0
    assert (rows[1]["trend"], rows[1]["ratio"], rows[1]["rank_score"]) == ("steady", 1.0, 0.0) and rows[1]["volume"] == 5000
    assert all(kw.get("min_interval") == 1.0 for url, p, kw in c.calls if "pypistats" in url)       # gentle with a small site
    assert rows[0]["channels"]["pypi"]["unit"] == "downloads/day" and rows[0]["windows"]["24h"]["active"]


def test_openlibrary_rows_and_each_source_fails_alone():
    from nicheclock.http import FetchError

    works = {"works": [{"key": "/works/OL1W", "title": "Atomic Habits", "author_name": ["James Clear"]}, {"key": "", "title": "no key"}]}
    out, st = feeds.openlibrary_rows(Client({"openlibrary.org": works}), END)
    r = out["global"][0]
    assert r["label"] == "Atomic Habits" and "James Clear" in r["description"] and r["source_url"] == "https://openlibrary.org/works/OL1W"
    assert r["category"] == "hobbies-leisure" and r["channels"]["openlibrary"]["rank"] == 1 and len(out["global"]) == 1
    for fn in (lambda c: feeds.gdacs_rows(c, END), lambda c: feeds.stackexchange_rows(c, END), lambda c: feeds.openlibrary_rows(c, END),
               lambda c: feeds.pypi_rows(c, DAY, END)):
        rows, status = fn(Client({"": FetchError("boom")}))
        assert rows == {} and status["status"] == "no_data"


def test_ledger_has_terms_for_every_new_source_and_the_exclusions():
    by = {e["id"]: e for e in rg.ledger()}
    for k in ("gdacs", "tvmaze", "stackexchange", "pypi", "openlibrary"):
        assert by[k]["status"] == ("experimental" if k in feeds.EXPERIMENTAL else "used") and by[k]["terms_url"].startswith("http") and len(by[k]["terms_quote"]) > 60, k
    assert by["coingecko"]["status"] == "excluded" and "commercial rights" in by["coingecko"]["terms_quote"]
    assert by["npm"]["status"] == "excluded" and "npm_pypi" not in by


def test_experimental_sources_are_off_unless_named_and_the_ledger_says_so():
    from types import SimpleNamespace

    from nicheclock.catalog import trending as tr
    from nicheclock.catalog.build import _ledger_runtime

    assert set(feeds.EXPERIMENTAL) == {"stackexchange", "pypi", "gharchive"}
    rt = SimpleNamespace(client=Client({"": FetchError("boom")}))
    now = None

    def ctx(on):
        return tr.Ctx(rt, None, None, None, date(2026, 10, 31), now, lambda *_: None, [], experimental=frozenset(on))

    off = ctx(())
    tr.load_feeds(off, DAY)
    assert off.feed_status["stackexchange"]["status"] == off.feed_status["pypi"]["status"] == "disabled"
    assert off.feed_status["gdacs"]["status"] == "no_data"                 # a normal source was tried
    assert tr.build_github(off, DAY) == (None, tr.DISABLED)
    on = ctx(("pypi",))
    tr.load_feeds(on, DAY)
    assert on.feed_status["pypi"]["status"] == "no_data" and on.feed_status["stackexchange"]["status"] == "disabled"

    by = {e["id"]: e for e in rg.ledger()}
    off_stat = {"global": {"github": tr.DISABLED}}
    for sid in feeds.EXPERIMENTAL:
        assert _ledger_runtime(by[sid], off_stat, {}) == ("experimental", None, "off by default (experimental source)")
    on_stat = {"global": {"github": {"status": "ok", "detail": "x"}, "pypi": {"status": "ok", "detail": "y"}}}
    assert _ledger_runtime(by["gharchive"], on_stat, {})[0] == "used" and _ledger_runtime(by["pypi"], on_stat, {}) == ("used", True, "y")

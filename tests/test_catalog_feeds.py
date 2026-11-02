"""M2 feeds: GDACS, TVmaze, Stack Exchange, PyPI, Open Library, built from fake payloads (no network)."""
import json
from datetime import date

from nicheclock.catalog import feeds, regions as rg

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


class FakeCache:
    def __init__(self):
        self.d = {}

    def get(self, ns, k, ttl=None):
        return (True, self.d[(ns, k)]) if (ns, k) in self.d else (False, None)

    def put(self, ns, k, v):
        self.d[(ns, k)] = v

    def commit(self):
        pass


def test_stackexchange_needs_a_key_credits_the_network_and_keeps_only_tag_name_count_link():
    items = [{"tags": ["python", "pandas"], "title": "SECRET QUESTION TEXT"}, {"tags": ["python"], "title": "x"}, {"tags": ["rust"]}]
    c = Client({"stackexchange.com": {"items": items}})
    out, st = feeds.stackexchange_rows(c, END, None, FakeCache())
    assert out == {} and st["status"] == "pending_key" and c.calls == []                       # no key: no request at all
    out, st = feeds.stackexchange_rows(c, END, "k3y", FakeCache())
    rows = out["global"]
    assert c.calls[0][1]["key"] == "k3y" and c.calls[0][1]["site"] == "stackoverflow"
    assert [(r["label"], r["volume"]) for r in rows][:2] == [("python", 2.0), ("pandas", 1.0)]
    assert rows[0]["source_url"] == "https://stackoverflow.com/questions/tagged/python" and rows[0]["category"] == "technology"
    assert rows[0]["credit"] == "Stack Exchange Network" and rows[0]["evidence"][0]["credit"] == "Stack Exchange Network"
    assert "Stack Exchange Network" in rows[0]["description"] and "SECRET" not in json.dumps(rows) and "k3y" not in json.dumps(rows)
    assert st["status"] == "ok"


def test_stackexchange_obeys_backoff_between_builds_and_never_leaks_the_key():
    from nicheclock.http import FetchError

    cache = FakeCache()
    c = Client({"stackexchange.com": {"items": [{"tags": ["go"]}], "backoff": 30}})
    out, st = feeds.stackexchange_rows(c, END, "k3y", cache, now=1000.0)
    assert out["global"] and "backoff 30 s" in st["detail"] and cache.d[("sebackoff", "until")] == 1030.0
    out, st = feeds.stackexchange_rows(c, END, "k3y", cache, now=1010.0)                        # still inside the backoff window
    assert out == {} and st["status"] == "no_data" and "backoff" in st["detail"] and len(c.calls) == 1
    out, st = feeds.stackexchange_rows(c, END, "k3y", cache, now=1031.0)
    assert out["global"] and len(c.calls) == 2
    bad = Client({"": FetchError("https://api.stackexchange.com/x?key=k3y: HTTP 400")})
    assert "k3y" not in feeds.stackexchange_rows(bad, END, "k3y", FakeCache())[1]["detail"]


def test_stackexchange_key_comes_from_env_or_the_secrets_file(tmp_path):
    f = tmp_path / "se.env"
    f.write_text("STACKEXCHANGE_KEY=abc123\n")
    assert feeds.stackexchange_key({"STACKEXCHANGE_KEY": " fromenv "}) == "fromenv"
    assert feeds.stackexchange_key({"STACKEXCHANGE_KEY_FILE": str(f)}) == "abc123"
    assert feeds.stackexchange_key({"STACKEXCHANGE_KEY_FILE": str(tmp_path / "missing")}) is None


def test_openlibrary_rows_and_each_source_fails_alone():
    from nicheclock.http import FetchError

    works = {"works": [{"key": "/works/OL1W", "title": "Atomic Habits", "author_name": ["James Clear"]}, {"key": "", "title": "no key"}]}
    out, st = feeds.openlibrary_rows(Client({"openlibrary.org": works}), END)
    r = out["global"][0]
    assert r["label"] == "Atomic Habits" and "James Clear" in r["description"] and r["source_url"] == "https://openlibrary.org/works/OL1W"
    assert r["category"] == "hobbies-leisure" and r["channels"]["openlibrary"]["rank"] == 1 and len(out["global"]) == 1
    for fn in (lambda c: feeds.gdacs_rows(c, END), lambda c: feeds.stackexchange_rows(c, END, "k", FakeCache()),
               lambda c: feeds.openlibrary_rows(c, END)):
        rows, status = fn(Client({"": FetchError("boom")}))
        assert rows == {} and status["status"] == "no_data"


def test_ledger_has_terms_for_every_new_source_and_the_exclusions():
    by = {e["id"]: e for e in rg.ledger()}
    for k in ("gdacs", "tvmaze", "stackexchange", "pypi", "openlibrary"):
        assert by[k]["status"] in ("used", "pending_key", "excluded") and by[k]["terms_url"].startswith("http") and len(by[k]["terms_quote"]) > 60, k
    assert by["stackexchange"]["status"] == "pending_key" and by["stackexchange"]["credit"] == "Stack Exchange Network"
    assert by["pypi"]["status"] == "excluded" and "BigQuery" in by["pypi"]["terms_quote"]
    assert by["coingecko"]["status"] == "excluded" and "commercial rights" in by["coingecko"]["terms_quote"]
    assert by["npm"]["status"] == "excluded" and "npm_pypi" not in by

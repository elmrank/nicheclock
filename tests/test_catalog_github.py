"""GitHub rising repos: GH Archive aggregation, windows, ranking, trending rows (no network)."""
import gzip
import json
from datetime import date, timedelta

from nicheclock.catalog import github as gh

END = date(2026, 10, 31)


def line(t, repo, actor="someone"):
    return json.dumps({"id": "1", "type": t, "actor": {"login": actor}, "repo": {"id": 5, "name": repo, "url": "x"}, "payload": {}}).encode() + b"\n"


def test_parse_hour_counts_stars_and_forks_and_keeps_no_actor_data():
    raw = gzip.compress(line("WatchEvent", "a/b") * 3 + line("ForkEvent", "a/b") + line("PushEvent", "a/b") + line("WatchEvent", "c/d"))
    out = gh.parse_hour(raw)
    assert out == {"a/b": [3, 1], "c/d": [1, 0]}
    assert "someone" not in json.dumps(out)


def samples(per_day):
    """{date: {"repos": {...}}} from {offset: {repo: [stars, forks]}} (0 = END)."""
    return {END - timedelta(days=o): {"day": "x", "hours": 24, "repos": r} for o, r in per_day.items()}


def test_window_totals_sum_daily_weekly_monthly():
    s = samples({o: {"a/b": [10, 2]} for o in range(30)})
    t = gh.window_totals(s, END)["a/b"]
    assert t["stars"] == [10, 70, 300] and t["forks"] == [2, 14, 60]
    assert gh.daily_series(s, END, "a/b", 0, 5) == [10] * 5


def meta(name, lang="Python", created="2020-01-01", archived=False):
    return {"full_name": name, "url": f"https://github.com/{name}", "description": "d", "language": lang, "topics": ["t"],
            "stars": 1000, "forks": 50, "created_at": created, "archived": archived, "fork": False}


def test_rows_ranking_language_filter_and_minimums():
    per = {o: {"big/one": [20, 3], "small/two": [1, 0], "gone/three": [500, 0]} for o in range(1, 30)}
    per[0] = {"big/one": [400, 40], "small/two": [60, 1], "gone/three": [500, 0]}
    s = samples(per)
    rows = gh.build_rows(s, END, {"big/one": meta("big/one"), "small/two": meta("small/two", "Rust"),
                                  "gone/three": meta("gone/three", archived=True), "nometa/x": None})
    names = [r["repo"] for r in rows]
    assert "gone/three" not in names and "nometa/x" not in names                       # archived / unknown are skipped
    daily = gh.rank(rows, "daily")
    assert [r["repo"] for r in daily] == ["big/one", "small/two"]                      # 400 then 60 stars (both >= 50)
    assert [r["repo"] for r in gh.rank(rows, "daily", "rust")] == ["small/two"]
    doc = gh.rank_doc(rows, END, "2026-11-01T05:00Z")
    assert doc["ranks"]["daily"] == ["big/one", "small/two"] and doc["windows"] == {"daily": 1, "weekly": 7, "monthly": 30}
    assert {r["repo"] for r in doc["repos"]} == {"big/one", "small/two"} and all(r["source"] == "github" and r["license"] for r in doc["repos"])


def test_trending_rows_are_technology_topics_without_a_qid():
    per = {o: {"big/one": [20, 3]} for o in range(1, 30)}
    per[0] = {"big/one": [400, 40]}
    rows = gh.build_rows(samples(per), END, {"big/one": meta("big/one", created="2026-10-20")})
    t = gh.trending_topics(rows, END, "2026-11-01T05:00Z")[0]
    assert t["id"] == "gh:big/one" and t["qid"] is None and t["category"] == "technology"
    assert t["trend"] == "rising" and t["rise"] == "strong" and t["ratio"] == 20.0
    c = t["channels"]["github"]
    assert c["unit"] == "stars/day" and c["value"] == 400.0 and c["language"] == "Python" and t["rising_channels"] == ["github"]
    assert t["source_url"] == "https://github.com/big/one" and t["license"]
    assert gh.trending_topics([], END, "x") == []


def test_token_never_comes_from_the_output(monkeypatch):
    assert gh.load_token({"GITHUB_TOKEN": " tok "}) == "tok"


def test_new_repo_search_returns_metadata_and_caches_it():
    from nicheclock.catalog.cache import Cache

    item = {"full_name": "new/hot", "html_url": "https://github.com/new/hot", "description": "d", "language": "Go", "topics": [],
            "stargazers_count": 900, "forks_count": 10, "created_at": "2026-10-25T00:00:00Z", "private": False}

    class R:
        status_code = 200

        def json(self):
            return {"items": [item]}

    class C:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, headers=None):
            self.calls.append(params)
            return R()

    c, cache = C(), Cache(":memory:")
    out = gh.search_new_repos(c, "tok", END, cache, pages=2)
    assert out["new/hot"]["stars"] == 900 and len(c.calls) == 2 and c.calls[0]["q"] == "created:>2026-10-01" and c.calls[0]["sort"] == "stars"
    assert cache.get("ghmeta", "new/hot")[1]["full_name"] == "new/hot"

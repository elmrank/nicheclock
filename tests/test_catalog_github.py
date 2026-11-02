"""GitHub rising repos from the official REST API: daily snapshots, windows, ranking, trending rows (no network)."""
from datetime import date, timedelta

from nicheclock.catalog import github as gh

TODAY = date(2026, 11, 2)


def snaps(per_day):
    """{date: {repo: [stars, forks]}} from {days_ago: {repo: [stars, forks]}}."""
    return {TODAY - timedelta(days=o): r for o, r in per_day.items()}


def meta(name, lang="Python", created="2020-01-01", archived=False, stars=1000, forks=50):
    return {"full_name": name, "url": f"https://github.com/{name}", "description": "d", "language": lang, "topics": ["t"],
            "stars": stars, "forks": forks, "created_at": created, "archived": archived, "fork": False}


def test_windows_are_differences_between_snapshots_scaled_and_null_when_unknown():
    s = snaps({0: {"a/x": [1100, 60]}, 1: {"a/x": [1000, 50]}, 7: {"a/x": [700, 40]}, 30: {"a/x": [100, 10]}})
    assert gh.window_gain(s, "a/x", TODAY, "daily", "2020-01-01") == (100, "snapshot")
    assert gh.window_gain(s, "a/x", TODAY, "weekly", "2020-01-01") == (400, "snapshot")
    assert gh.window_gain(s, "a/x", TODAY, "monthly", "2020-01-01") == (1000, "snapshot")
    assert gh.window_gain(s, "a/x", TODAY, "daily", "2020-01-01", idx=1) == (10, "snapshot")           # forks the same way
    assert gh.window_gain(s, "zzz/none", TODAY, "daily", None) == (None, None)
    only_today = snaps({0: {"a/x": [1100, 60]}})
    assert gh.window_gain(only_today, "a/x", TODAY, "daily", "2020-01-01") == (None, None)             # never 0: unknown
    late = snaps({0: {"a/x": [1100, 60]}, 9: {"a/x": [200, 10]}})                                       # 2 days older than a week: scaled 7/9
    assert gh.window_gain(late, "a/x", TODAY, "weekly", "2020-01-01") == (700, "snapshot")


def test_a_repo_younger_than_the_window_counts_all_its_stars_in_it():
    s = snaps({0: {"new/repo": [900, 30]}})
    assert gh.window_gain(s, "new/repo", TODAY, "weekly", "2026-10-30") == (900, "since_creation")
    assert gh.window_gain(s, "new/repo", TODAY, "daily", "2026-10-30") == (None, None)                 # older than a day: needs yesterday
    assert gh.window_gain(s, "new/repo", TODAY, "daily", "2026-11-02") == (900, "since_creation")


def test_snapshots_roundtrip_and_old_files_are_pruned(tmp_path):
    for o in (0, 1, 45):
        gh.save_snapshot(tmp_path, TODAY - timedelta(days=o), {"a/x": [o, 0]})
    loaded = gh.load_snapshots(tmp_path, TODAY)
    assert set(loaded) == {TODAY, TODAY - timedelta(days=1)} and loaded[TODAY]["a/x"] == [0, 0]
    gh.save_snapshot(tmp_path, TODAY, {"a/x": [5, 0]})
    assert not (tmp_path / "ghsnap" / f"{TODAY - timedelta(days=45)}.json.gz").exists()                # older than 40 days: pruned


def test_rows_ranking_language_filter_minimums_and_null_windows():
    s = snaps({0: {"big/one": [1400, 40], "small/two": [160, 1], "quiet/three": [5000, 9], "new/four": [200, 2]},
               1: {"big/one": [1000, 30], "small/two": [140, 1], "quiet/three": [4998, 9]},
               7: {"big/one": [600, 20], "small/two": [100, 1], "quiet/three": [4980, 8]}})
    m = {"big/one": meta("big/one"), "small/two": meta("small/two", "Rust"), "quiet/three": meta("quiet/three"),
         "new/four": meta("new/four", created="2026-10-30"), "gone/x": None, "arch/y": meta("arch/y", archived=True)}
    rows = gh.build_rows(s, TODAY, m)
    names = [r["repo"] for r in rows]
    assert "quiet/three" not in names and "gone/x" not in names and "arch/y" not in names              # below minimums / unknown / archived
    big = next(r for r in rows if r["repo"] == "big/one")
    assert big["stars"] == {"daily": 400, "weekly": 800, "monthly": None} and big["basis"]["monthly"] is None      # no 30-day baseline yet
    assert big["forks"]["daily"] == 10 and big["stars_per_day"]["weekly"] == 114.3 and big["series"] == [400]
    new = next(r for r in rows if r["repo"] == "new/four")
    assert new["stars"] == {"daily": None, "weekly": 200, "monthly": 200} and new["basis"]["weekly"] == "since_creation" and new["new_repo"]
    assert [r["repo"] for r in gh.rank(rows, "daily")] == ["big/one", "small/two"]                                  # new/four has no daily figure: not ranked there
    assert [r["repo"] for r in gh.rank(rows, "weekly")] == ["big/one", "new/four", "small/two"]
    assert [r["repo"] for r in gh.rank(rows, "weekly", "rust")] == ["small/two"]                       # 60 stars in the week, language filter
    doc = gh.rank_doc(rows, TODAY, "2026-11-02T05:00Z", history_days=3)
    assert doc["history_days"] == 3 and doc["ranks"]["monthly"] == ["new/four"] and "null" in doc["note"]
    assert all(r["source"] == "github" and r["license"] and r["source_url"] for r in doc["repos"])


def test_case_variants_collapse_into_one_row():
    s = snaps({0: {"Owner/Repo": [1100, 5]}, 1: {"Owner/Repo": [1000, 5]}})
    m = {"owner/repo": meta("Owner/Repo"), "Owner/Repo": meta("Owner/Repo")}
    assert [r["repo"] for r in gh.build_rows(s, TODAY, m)] == ["Owner/Repo"]


def test_trending_rows_are_technology_topics_without_a_qid_and_without_inventing_a_ratio():
    s = snaps({o: {"big/one": [1000 + 20 * (30 - o), 5]} for o in range(0, 20)})
    s[TODAY] = {"big/one": [2000, 9]}
    s[TODAY - timedelta(days=1)] = {"big/one": [1600, 8]}
    rows = gh.build_rows(s, TODAY, {"big/one": meta("big/one", created="2026-10-25")})
    t = gh.trending_topics(rows, TODAY, "x")[0]
    assert t["id"] == "gh:big/one" and t["qid"] is None and t["category"] == "technology" and t["channels"]["github"]["unit"] == "stars/day"
    assert t["trend"] == "rising" and t["volume"] == 400.0 and t["source_url"] == "https://github.com/big/one"
    only_new = gh.build_rows(snaps({0: {"n/r": [300, 1]}}), TODAY, {"n/r": meta("n/r", created="2026-10-31")})
    t2 = gh.trending_topics(only_new, TODAY, "x")[0]
    assert t2["ratio"] is None and t2["windows"] == {} and t2["trend"] == "rising" and t2["rank_score"] == 0.0     # a new repo: listed, no made-up ratio
    assert gh.trending_topics([], TODAY, "x") == []


class Resp:
    def __init__(self, status=200, body=None, remaining="4000"):
        self.status_code, self._b, self.headers = status, body or {}, {"x-ratelimit-remaining": remaining}

    def json(self):
        return self._b


def item(name, stars=900):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "description": "d", "language": "Go", "topics": [],
            "stargazers_count": stars, "forks_count": 10, "created_at": "2026-10-25T00:00:00Z", "private": False, "archived": False, "fork": False}


def test_search_candidates_uses_three_queries_and_skips_archived_and_forks():
    calls = []

    class C:
        def get(self, url, params=None, headers=None, min_interval=None):
            calls.append((url, params["q"], params["page"], min_interval, headers["Authorization"]))
            return Resp(200, {"items": [item("new/hot"), dict(item("old/arch"), archived=True), dict(item("a/fork"), fork=True)]})

    out = gh.search_candidates(C(), "tok", TODAY)
    assert sorted(out) == ["new/hot"] and len(calls) == 6 and all(c[3] == 2.0 for c in calls)
    assert calls[0][1] == "created:>2026-10-03" and "stars:>1500" in calls[2][1] and calls[0][4] == "Bearer tok"


def test_search_budget_is_judged_on_its_own_small_limit():
    search = Resp(200, remaining="20")
    search.headers["x-ratelimit-resource"] = "search"
    assert gh._rate_ok(search) is True                          # 20 of 30 left on the search resource: fine
    search.headers["x-ratelimit-remaining"] = "2"
    assert gh._rate_ok(search) is False
    assert gh._rate_ok(Resp(200, remaining="20")) is False      # the same 20 on the core resource is below the floor
    assert gh._rate_ok(Resp(200, remaining="4000")) is True


def test_refresh_skips_known_drops_404_and_stops_at_the_rate_limit():
    seen = []

    class C:
        def get(self, url, headers=None):
            n = url.split("/repos/")[1]
            seen.append(n)
            if n == "gone/x":
                return Resp(404)
            return Resp(200, item(n), remaining="100" if n == "b/two" else "4000")

    known = {"k/known": gh.parse_repo(item("k/known"))}
    out, complete = gh.refresh(C(), "tok", ["k/known", "gone/x", "a/one", "b/two", "c/three"], known)
    assert seen == ["gone/x", "a/one", "b/two"]                       # known not re-fetched; stopped after the call that left < 150 requests
    assert sorted(out) == ["a/one", "b/two", "k/known"] and complete is False


def test_tracked_names_best_recent_gain_first():
    s = snaps({0: {"a/x": [1100, 0], "b/y": [5000, 0], "c/z": [50, 0]}, 1: {"a/x": [1000, 0], "b/y": [4999, 0], "c/z": [10, 0]}})
    assert gh.tracked_names(s, TODAY) == ["a/x", "c/z", "b/y"] and gh.tracked_names({}, TODAY) == []


def test_token_comes_from_the_environment_first():
    assert gh.load_token({"GITHUB_TOKEN": " tok "}) == "tok"

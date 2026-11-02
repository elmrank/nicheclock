"""GitHub rising repositories: the same columns and windows as github.com/trending, from the official REST API.

Public repository data only, read-only, with the operator's token (never printed) and the rate limit respected:
stars, forks, created_at, language, topics, description. The github.com/trending page is not fetched, and nothing
comes from GH Archive.

How a star count per window is obtained without the (unavailable) star history endpoint: the build takes a
**snapshot** of ``stargazers_count`` / ``forks_count`` of every tracked repo once a day (state dir, one small file
per day) and a window's stars are the difference between snapshots:

* ``daily`` needs yesterday's snapshot, ``weekly`` one from 7 days ago, ``monthly`` one from 30 days ago (a baseline
  within 2 days of that is scaled to the window; older or missing: the window is null, never 0);
* a repo created inside a window has its whole star count in that window ("since creation", flagged per window),
  so brand-new repos appear in the weekly and monthly lists from the first day.

Tracked repos = every repo seen in the last 30 days of snapshots (top ``TRACK_MAX`` by recent gain) plus the daily
candidates: GitHub search for repos created in the last 30 days, the most-starred recently pushed repos and
mid-age repos still being pushed to. So the lists get fuller each day; ``history_days`` says how many snapshots
exist.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "github"
LICENSE = "GitHub REST API (public repository data); daily star snapshots by nicheclock"
API = "https://api.github.com/repos/{full}"
SEARCH = "https://api.github.com/search/repositories"
UNIT = "stars/day"
UNIT_FORKS = "forks/day"
WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
BASELINE_TOLERANCE = {"daily": 0, "weekly": 2, "monthly": 3}      # days a baseline snapshot may be older than the window
KEEP = 100                   # rows per window in the output
TRACK_MAX = 400              # repos refreshed per day
SEARCH_PAGES = 2             # x 50 repos per search
MIN_STARS = {"daily": 8, "weekly": 35, "monthly": 120}      # lowest stars for a repo to be listed in a window
TRENDING_FLOOR = 20.0        # stars/day for a repo to enter the Trending now table
RATE_FLOOR = 150             # stop calling when this few requests are left in the hour
NEW_REPO_DAYS = 30
SNAP_KEEP_DAYS = 40

def load_token(env: dict[str, str] | None = None) -> str | None:
    """GITHUB_TOKEN, else the token of the operator's ``gh`` login. Never printed."""
    env = env if env is not None else os.environ
    if env.get("GITHUB_TOKEN"):
        return env["GITHUB_TOKEN"].strip()
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def parse_repo(data: dict[str, Any]) -> dict[str, Any] | None:
    if not data.get("full_name") or data.get("private"):
        return None
    return {"full_name": data["full_name"], "url": data.get("html_url") or f"https://github.com/{data['full_name']}",
            "description": (data.get("description") or "").strip()[:200] or None, "language": data.get("language"),
            "topics": (data.get("topics") or [])[:8], "stars": int(data.get("stargazers_count") or 0),
            "forks": int(data.get("forks_count") or 0), "created_at": (data.get("created_at") or "")[:10] or None,
            "archived": bool(data.get("archived")), "fork": bool(data.get("fork"))}
# -- daily snapshots ----------------------------------------------------------------------------

def _snap_path(state_dir: Path, day: date) -> Path:
    return state_dir / "ghsnap" / f"{day.isoformat()}.json.gz"


def save_snapshot(state_dir: Path, day: date, repos: dict[str, list[int]]) -> None:
    """{repo: [stars, forks]} taken on ``day`` (one file per day; older than SNAP_KEEP_DAYS are deleted)."""
    p = _snap_path(state_dir, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(repos).encode()))
    for f in p.parent.glob("*.json.gz"):
        try:
            if date.fromisoformat(f.name[:10]) < day - timedelta(days=SNAP_KEEP_DAYS):
                f.unlink()
        except ValueError:
            pass


def load_snapshots(state_dir: Path, today: date, days: int = 31) -> dict[date, dict[str, list[int]]]:
    out: dict[date, dict[str, list[int]]] = {}
    for i in range(days):
        d = today - timedelta(days=i)
        p = _snap_path(state_dir, d)
        if p.exists():
            out[d] = json.loads(gzip.decompress(p.read_bytes()))
    return out


def baseline(snaps: dict[date, dict[str, list[int]]], repo: str, today: date, window: str) -> tuple[date, list[int]] | None:
    """The snapshot to subtract for a window: exactly ``days`` back, or up to the window's tolerance older."""
    days = WINDOW_DAYS[window]
    for extra in range(BASELINE_TOLERANCE[window] + 1):
        d = today - timedelta(days=days + extra)
        if d in snaps and repo in snaps[d]:
            return d, snaps[d][repo]
    return None


def window_gain(snaps: dict[date, dict[str, list[int]]], repo: str, today: date, window: str, created: str | None,
                idx: int = 0) -> tuple[int | None, str | None]:
    """(gain, basis) of stars (idx 0) or forks (idx 1) in a window; (None, None) when it cannot be known.
    basis: "snapshot" or "since_creation" (the repo is younger than the window: all of it happened inside)."""
    cur = snaps.get(today, {}).get(repo)
    if cur is None:
        return None, None
    days = WINDOW_DAYS[window]
    if created and created > (today - timedelta(days=days)).isoformat():
        return int(cur[idx]), "since_creation"
    b = baseline(snaps, repo, today, window)
    if b is None:
        return None, None
    gap = (today - b[0]).days
    return max(0, round((cur[idx] - b[1][idx]) * days / gap)), "snapshot"


def daily_gains(snaps: dict[date, dict[str, list[int]]], repo: str, today: date, days: int = 14) -> list[int]:
    """Stars gained per day over consecutive snapshots, oldest first (only days with both snapshots)."""
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        a, b = snaps.get(d - timedelta(days=1), {}).get(repo), snaps.get(d, {}).get(repo)
        if a is not None and b is not None:
            out.append(max(0, b[0] - a[0]))
    return out


# -- GitHub REST API ----------------------------------------------------------------------------

def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _rate_ok(resp: Any) -> bool:
    """False once the hour's budget is nearly used. The search API has its own small budget (30 a minute): 3 left
    is the floor there, RATE_FLOOR for the core API."""
    try:
        floor = 3 if resp.headers.get("x-ratelimit-resource") == "search" else RATE_FLOOR
        return int(resp.headers.get("x-ratelimit-remaining", "9999")) > floor
    except (ValueError, AttributeError):
        return True


def search_candidates(client: Any, token: str, today: date) -> dict[str, dict[str, Any]]:
    """Daily candidates from GitHub search (3 queries x SEARCH_PAGES pages): created in the last 30 days (most
    stars), most-starred repos pushed in the last day, and mid-age repos (up to 120 days) still pushed to."""
    since = lambda n: (today - timedelta(days=n)).isoformat()      # noqa: E731
    queries = [f"created:>{since(NEW_REPO_DAYS)}", f"pushed:>{since(1)} stars:>1500", f"created:>{since(120)} stars:>300 pushed:>{since(3)}"]
    out: dict[str, dict[str, Any]] = {}
    for q in queries:
        for page in range(1, SEARCH_PAGES + 1):
            try:
                resp = client.get(SEARCH, params={"q": q, "sort": "stars", "order": "desc", "per_page": 50, "page": page},
                                  headers=_headers(token), min_interval=2.0)
            except (FetchError, RobotsDisallowed):
                break
            if resp.status_code != 200:
                break
            for it in resp.json().get("items", []):
                m = parse_repo(it)
                if m and not m["archived"] and not m["fork"]:
                    out[m["full_name"]] = m
            if not _rate_ok(resp):
                return out
    return out


def refresh(client: Any, token: str, names: list[str], known: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], bool]:
    """Fresh metadata + counts of tracked repos (1 request each, skipped for repos search just returned).
    Returns ({repo: meta}, complete); a repo that answers 404/451 is dropped; the rate limit stops the loop."""
    out = dict(known)
    complete = True
    for n in names:
        if n in out:
            continue
        try:
            resp = client.get(API.format(full=n), headers=_headers(token))
        except (FetchError, RobotsDisallowed):
            continue
        if resp.status_code == 200:
            m = parse_repo(resp.json())
            if m:
                out[m["full_name"]] = m
        if resp.status_code in (401, 403) or not _rate_ok(resp):
            complete = False
            break
    return out, complete


def tracked_names(snaps: dict[date, dict[str, list[int]]], today: date) -> list[str]:
    """Repos seen in recent snapshots, best recent gain first, at most TRACK_MAX."""
    last = max(snaps) if snaps else None
    if last is None:
        return []
    prev = snaps.get(last - timedelta(days=1)) or snaps.get(min(snaps))
    score = {r: v[0] - (prev.get(r, v)[0] if prev else v[0]) for r, v in snaps[last].items()}
    return [r for r, _g in sorted(score.items(), key=lambda kv: -kv[1])][:TRACK_MAX]


# -- rows ---------------------------------------------------------------------------------------

def build_rows(snaps: dict[date, dict[str, list[int]]], today: date, meta: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
    """One row per repo with metadata and a known gain in at least one window above its minimum. A window that
    cannot be known is null (never 0); ``basis`` says whether each came from snapshots or from the repo's age."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, m in meta.items():
        if not m or m["archived"] or m["full_name"].casefold() in seen:
            continue
        stars, forks, basis = {}, {}, {}
        for w in WINDOW_DAYS:
            stars[w], basis[w] = window_gain(snaps, m["full_name"], today, w, m["created_at"], 0)
            forks[w], _b = window_gain(snaps, m["full_name"], today, w, m["created_at"], 1)
        if not any(stars[w] is not None and stars[w] >= MIN_STARS[w] for w in WINDOW_DAYS):
            continue
        seen.add(m["full_name"].casefold())
        series = daily_gains(snaps, m["full_name"], today)
        per_day = {w: (round(stars[w] / WINDOW_DAYS[w], 1) if stars[w] is not None else None) for w in WINDOW_DAYS}
        rows.append({
            "repo": m["full_name"], "url": m["url"], "description": m["description"], "language": m["language"],
            "topics": m["topics"], "total_stars": m["stars"], "total_forks": m["forks"], "created_at": m["created_at"],
            "stars": stars, "forks": forks, "stars_per_day": per_day, "basis": basis,
            "series": series[-14:], "series_unit": UNIT, "series_end": today.isoformat(),
            "age_days": (today - date.fromisoformat(m["created_at"])).days if m["created_at"] else None,
            "new_repo": bool(m["created_at"] and m["created_at"] >= (today - timedelta(days=NEW_REPO_DAYS)).isoformat()),
            "baseline": round(statistics.median(series[-15:-1]), 1) if len(series) >= 15 else None,
            "source": NAME, "source_url": m["url"], "license": LICENSE})
    return rows


def rank(rows: list[dict[str, Any]], window: str, language: str | None = None, limit: int = KEEP) -> list[dict[str, Any]]:
    """Rows of one window sorted by stars in it (ties: forks); rows without a figure for the window are not ranked."""
    out = [r for r in rows if r["stars"][window] is not None and r["stars"][window] >= MIN_STARS[window]
           and (language is None or (r["language"] or "").casefold() == language.casefold())]
    out.sort(key=lambda r: (-r["stars"][window], -(r["forks"][window] or 0), r["repo"]))
    return out[:limit]


def rank_doc(rows: list[dict[str, Any]], today: date, now_iso: str, history_days: int) -> dict[str, Any]:
    """github-rank.json: ``repos`` once, ``ranks[window]`` as ordered repo names, ``languages`` by monthly stars."""
    ranks = {w: [r["repo"] for r in rank(rows, w)] for w in WINDOW_DAYS}
    keep = {n for names in ranks.values() for n in names}
    from collections import defaultdict

    by_lang: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["language"] and r["repo"] in keep:
            by_lang[r["language"]] += r["stars"]["monthly"] or r["stars"]["weekly"] or 0
    langs = [{"language": k, "repos": sum(1 for r in rows if r["language"] == k and r["repo"] in keep)}
             for k, _v in sorted(by_lang.items(), key=lambda kv: -kv[1])[:40]]
    return {"schema": 2, "date": today.isoformat(), "generated_at": now_iso, "history_days": history_days,
            "note": ("Stars and forks of public repositories from the GitHub REST API, as differences between daily snapshots "
                     f"({history_days} day(s) of history so far); a window we cannot know yet is null. Repos younger than a window "
                     "count all their stars in it (basis since_creation). Not a copy of github.com/trending."),
            "windows": WINDOW_DAYS, "minimum_stars": MIN_STARS, "unit": UNIT, "languages": langs, "ranks": ranks,
            "repos": [r for r in rows if r["repo"] in keep]}


def trending_topics(rows: list[dict[str, Any]], end: date, now_iso: str) -> list[dict[str, Any]]:
    """Rows for trending.json (Worldwide): repos with a known daily gain (or younger than a week) at or above
    TRENDING_FLOOR stars/day, as Technology topics with a ``github`` channel (unit stars/day). They have no Wikidata
    item, so ``id`` is ``gh:owner/name`` and ``qid`` is null."""
    out = []
    for r in rows:
        day = r["stars_per_day"]["daily"]
        week = r["stars_per_day"]["weekly"]
        best = max([x for x in (day, week) if x is not None], default=None)
        if best is None or best < TRENDING_FLOOR:
            continue
        base = r["baseline"]
        month = r["stars_per_day"]["monthly"]
        ref = base if base is not None else month
        ratio = round(day / max(ref or 1.0, 1.0), 2) if day is not None and ref is not None else None
        rising = (ratio is not None and ratio >= 1.5) or (r["new_repo"] and best >= TRENDING_FLOOR)
        windows = {}
        if ratio is not None:
            windows["24h"] = {"ratio": ratio, "active": ratio >= 1.5}
        if week is not None and month and r["basis"]["weekly"] == "snapshot" and r["basis"]["monthly"] == "snapshot":
            windows["7d"] = {"ratio": round(week / max(month, 1.0), 2), "active": week / max(month, 1.0) >= 1.5}
        ch = {"value": best, "unit": UNIT, "baseline": base, "ratio": ratio, "windows": windows, "series": r["series"],
              "total_stars": r["total_stars"], "language": r["language"], "topics": r["topics"], "basis": r["basis"],
              "source": NAME, "source_url": r["url"], "license": LICENSE}
        rs = round(math.log10(max(best, 10.0)) ** 2 * math.log10(min(max(ratio or 1.0, 1.0), 10.0)), 3) if rising and ratio else 0.0
        out.append({
            "id": f"gh:{r['repo']}", "qid": None, "label": r["repo"], "native_label": None,
            "description": r["description"] or "GitHub repository", "category": "technology", "category_label": "Technology",
            "category_via": {"kind": "source", "class": "github", "label": "GitHub repository"},
            "region": "global", "trend": "rising" if rising else "steady",
            "rise": ("strong" if ratio and ratio >= 1.5 else ("mild" if rising else None)), "status": "active" if rising else "steady",
            "ratio": ratio, "ratio_window": "24h" if ratio is not None else None, "ratio_channel": "github" if ratio is not None else None,
            "volume": best, "volume_unit": UNIT, "rank_score": rs, "score": min(100, round(2.5 * rs)),
            "source_families": ["developer"] if rising else [], "multi_source": False,
            "rising_channels": ["github"] if rising else [], "seasonal": "unknown", "seasonal_detail": None,
            "first_seen": end.isoformat(), "series_end": end.isoformat(), "windows": windows, "peak": None,
            "channels": {"github": ch},
            "evidence": [{"source": NAME, "source_url": r["url"], "license": LICENSE, "title": r["repo"], "value": best, "unit": UNIT}],
            "source": NAME, "source_url": r["url"], "license": LICENSE, "sources": [NAME], "aliases": [],
            "breakdown": r["topics"][:8], "wikipedia_url": None})
    return out

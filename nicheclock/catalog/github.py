"""GitHub rising repositories: the same columns and windows as github.com/trending, built from open data.

* Stars and forks per repo per day come from GH Archive's hourly files (WatchEvent / ForkEvent), streamed and
  aggregated on the fly: only ``repo name -> (stars, forks)`` per day is kept, never raw events, actors or
  payloads. GH Archive: https://www.gharchive.org/ ("GH Archive is a project to record the public GitHub timeline,
  archive it, and make it easily accessible for further analysis"); its README notes the dataset "may be subject to
  third party rights", which is why nothing but aggregate counts of public events is stored or published.
* Description, language, topics, total stars and forks come from the official GitHub REST API (read-only, token of
  the operator, never printed). github.com/trending HTML is NOT fetched for the product (GitHub AUP).

Windows: ``daily`` (stars in the last full UTC day), ``weekly`` (last 7 days), ``monthly`` (last 30 days).
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "github"
LICENSE = "GitHub public events (GH Archive) and REST API metadata; aggregate counts of public events"
ARCHIVE = "https://data.gharchive.org/{day}-{hour}.json.gz"
API = "https://api.github.com/repos/{full}"
UNIT = "stars/day"
UNIT_FORKS = "forks/day"
DAYS = 30
MIN_DAY_STARS = 2            # a repo is stored for a day only when it got at least this many stars that day
WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
POOL = 300                   # repos per window that get metadata from the API
KEEP = 100                   # rows per window in the output
MIN_STARS = {"daily": 8, "weekly": 35, "monthly": 120}      # lowest stars for a repo to be listed in a window: enough for 25 rows each on the archive data we have (the Trending now table still needs 20 stars/day)
TRENDING_FLOOR = 20.0        # stars/day for a repo to enter the Trending now table
SEARCH = "https://api.github.com/search/repositories"
NEW_REPO_DAYS = 30
NEW_REPO_PAGES = 2           # 100 repos created in the last 30 days, most stars first
REPO = re.compile(r'"repo":\{"id":\d+,"name":"([^"]+)"')


def parse_hour(raw_gz: bytes) -> dict[str, list[int]]:
    """{repo: [stars, forks]} for one hourly archive. Streams the lines; a star is a WatchEvent (GitHub only
    emits action "started"); the repo name is read before the line is parsed as JSON."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with gzip.GzipFile(fileobj=io.BytesIO(raw_gz)) as f:
        for line in f:
            if b'"WatchEvent"' in line[:200] or b'"ForkEvent"' in line[:200]:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t, name = ev.get("type"), (ev.get("repo") or {}).get("name")
                if not name:
                    continue
                if t == "WatchEvent":
                    out[name][0] += 1
                elif t == "ForkEvent":
                    out[name][1] += 1
    return dict(out)


def _path(state_dir: Path, day: date) -> Path:
    return state_dir / "gharchive" / f"{day.isoformat()}.json.gz"


def load_day(state_dir: Path, day: date) -> dict[str, Any] | None:
    p = _path(state_dir, day)
    return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


def fetch_day(client: Any, state_dir: Path, day: date) -> dict[str, Any] | None:
    """{"hours": n, "repos": {repo: [stars, forks]}} for one UTC day (24 files), cached. A day with fewer than
    20 of its 24 hours is returned but not cached (the missing files may appear later) and counts are scaled."""
    cached = load_day(state_dir, day)
    if cached is not None:
        return cached
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    hours = 0
    for h in range(24):
        try:
            resp = client.get(ARCHIVE.format(day=day.isoformat(), hour=h), timeout=120)
        except (FetchError, RobotsDisallowed):
            continue
        if resp.status_code != 200:
            continue
        try:
            for repo, (s, f) in parse_hour(resp.content).items():
                total[repo][0] += s
                total[repo][1] += f
        except (OSError, EOFError):
            continue
        finally:
            client.forget()
        hours += 1
    if hours == 0:
        return None
    scale = 24 / hours
    data = {"day": day.isoformat(), "hours": hours,
            "repos": {r: [round(v[0] * scale), round(v[1] * scale)] for r, v in total.items() if v[0] * scale >= MIN_DAY_STARS}}
    if hours >= 20:
        p = _path(state_dir, day)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(gzip.compress(json.dumps(data).encode()))
    return data


def gather(client: Any, state_dir: Path, end: date, days: int = DAYS) -> dict[date, dict[str, Any]]:
    out = {}
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        data = fetch_day(client, state_dir, d)
        if data:
            out[d] = data
    return out


def latest_complete_day(client: Any, today: date) -> date | None:
    """Yesterday if its last hourly file (23:00) exists, else the day before."""
    for back in (1, 2):
        d = today - timedelta(days=back)
        try:
            resp = client.get(ARCHIVE.format(day=d.isoformat(), hour=23), timeout=120)
        except (FetchError, RobotsDisallowed):
            continue
        client.forget()
        if resp.status_code == 200:
            return d
    return None


# -- ranking ----------------------------------------------------------------------------------

def daily_series(samples: dict[date, dict[str, Any]], end: date, repo: str, idx: int, days: int = DAYS) -> list[int]:
    """Stars (idx 0) or forks (idx 1) per day, oldest first, ``days`` values ending on ``end``; a day with no
    stored row counts as 0 (below the storage threshold: fewer than MIN_DAY_STARS stars), days without a file
    are skipped, so the list can be shorter than ``days``."""
    out = []
    for i in range(days - 1, -1, -1):
        d = samples.get(end - timedelta(days=i))
        if d is None:
            continue
        out.append(int((d["repos"].get(repo) or [0, 0])[idx]))
    return out


def window_totals(samples: dict[date, dict[str, Any]], end: date) -> dict[str, dict[str, list[int]]]:
    """{repo: {"stars": [daily, weekly, monthly], "forks": [...]}} sums over the windows."""
    res: dict[str, dict[str, list[int]]] = {}
    for w_i, days in enumerate(WINDOW_DAYS.values()):
        for i in range(days):
            d = samples.get(end - timedelta(days=i))
            if not d:
                continue
            for repo, (s, f) in d["repos"].items():
                r = res.setdefault(repo, {"stars": [0, 0, 0], "forks": [0, 0, 0]})
                r["stars"][w_i] += s
                r["forks"][w_i] += f
    return res


def pool(totals: dict[str, dict[str, list[int]]]) -> list[str]:
    """Repos that need metadata: the best POOL of each window (by stars)."""
    names: list[str] = []
    for i in range(3):
        names += sorted(totals, key=lambda r: -totals[r]["stars"][i])[:POOL]
    return list(dict.fromkeys(names))


# -- metadata (GitHub REST API) -----------------------------------------------------------------

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


def fetch_meta(client: Any, token: str, names: list[str], cache: Any, ttl: float = 86400.0) -> dict[str, dict[str, Any] | None]:
    """{repo: metadata or None}. Cached for a day; a 404 (renamed/deleted) is cached as None."""
    out: dict[str, dict[str, Any] | None] = {}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    for n in names:
        found, hit = cache.get("ghmeta", n, ttl)
        if found:
            out[n] = hit
            continue
        try:
            resp = client.get(API.format(full=n), headers=headers)
        except (FetchError, RobotsDisallowed):
            continue
        if resp.status_code == 200:
            out[n] = parse_repo(resp.json())
        elif resp.status_code in (404, 451):
            out[n] = None
        else:
            if resp.status_code in (401, 403):      # bad token or rate limit: stop, keep what we have
                break
            continue
        cache.put("ghmeta", n, out[n])
    cache.commit()
    return out


def search_new_repos(client: Any, token: str, end: date, cache: Any, pages: int = NEW_REPO_PAGES
                     ) -> dict[str, dict[str, Any] | None]:
    """Repos created in the last NEW_REPO_DAYS days, most stars first (GitHub Search API, 1 call per 50). Their
    metadata is returned (and cached for fetch_meta) so a brand-new repo cannot fall out of the pool just because
    the archive pool is capped."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    since = (end - timedelta(days=NEW_REPO_DAYS)).isoformat()
    out: dict[str, dict[str, Any] | None] = {}
    for page in range(1, pages + 1):
        try:
            resp = client.get(SEARCH, params={"q": f"created:>{since}", "sort": "stars", "order": "desc", "per_page": 50, "page": page},
                              headers=headers)
        except (FetchError, RobotsDisallowed):
            break
        if resp.status_code != 200:
            break
        for it in resp.json().get("items", []):
            m = parse_repo(it)
            if m:
                out[m["full_name"]] = m
                cache.put("ghmeta", m["full_name"], m)
    cache.commit()
    return out


# -- output -----------------------------------------------------------------------------------

def build_rows(samples: dict[date, dict[str, Any]], end: date, meta: dict[str, dict[str, Any] | None]
               ) -> list[dict[str, Any]]:
    """One row per repo that has metadata and passes a window minimum, with series and window numbers."""
    totals = window_totals(samples, end)
    rows = []
    seen: dict[str, int] = {}          # GitHub's canonical name (case-folded) -> index in rows
    for name, m in meta.items():
        if not m or m["archived"] or name not in totals:
            continue
        # the archive spells a repo as it was typed (Owner/Repo, owner/repo), the API answers with one canonical
        # name: one row per canonical name, the archive spelling that matches it exactly wins, else the biggest
        key = m["full_name"].casefold()
        if key in seen:
            prev = rows[seen[key]]
            better = name == m["full_name"] or totals[name]["stars"][2] > prev["stars"]["monthly"]
            if not better:
                continue
        t = totals[name]
        if not any(t["stars"][i] >= list(MIN_STARS.values())[i] for i in range(3)):
            continue
        series = daily_series(samples, end, name, 0)
        forks_series = daily_series(samples, end, name, 1)
        row = {
            "repo": m["full_name"], "url": m["url"], "description": m["description"], "language": m["language"],
            "topics": m["topics"], "total_stars": m["stars"], "total_forks": m["forks"], "created_at": m["created_at"],
            "stars": {"daily": t["stars"][0], "weekly": t["stars"][1], "monthly": t["stars"][2]},
            "forks": {"daily": t["forks"][0], "weekly": t["forks"][1], "monthly": t["forks"][2]},
            "stars_per_day": {"daily": float(t["stars"][0]), "weekly": round(t["stars"][1] / 7, 1), "monthly": round(t["stars"][2] / 30, 1)},
            "series": series[-14:], "series_unit": UNIT, "series_end": end.isoformat(),
            "age_days": (end - date.fromisoformat(m["created_at"])).days if m["created_at"] else None,
            "new_repo": bool(m["created_at"] and m["created_at"] >= (end - timedelta(days=30)).isoformat()),
            "baseline": round(statistics.median(series[-15:-1]), 1) if len(series) >= 15 else None,
            "source": NAME, "source_url": m["url"], "license": LICENSE, "forks_series": forks_series[-14:]}
        if key in seen:
            rows[seen[key]] = row
        else:
            seen[key] = len(rows)
            rows.append(row)
    return rows


def rank(rows: list[dict[str, Any]], window: str, language: str | None = None, limit: int = KEEP) -> list[dict[str, Any]]:
    """Rows of one window sorted by stars in it (ties: forks), at least the window minimum; ``language`` filters
    on GitHub's primary language (case-insensitive)."""
    out = [r for r in rows if r["stars"][window] >= MIN_STARS[window]
           and (language is None or (r["language"] or "").casefold() == language.casefold())]
    out.sort(key=lambda r: (-r["stars"][window], -r["forks"][window], r["repo"]))
    return out[:limit]


def rank_doc(rows: list[dict[str, Any]], end: date, now_iso: str) -> dict[str, Any]:
    """github-rank.json: ``repos`` once, ``ranks[window]`` as ordered repo names, ``languages`` by weekly stars."""
    ranks = {w: [r["repo"] for r in rank(rows, w)] for w in WINDOW_DAYS}
    keep = {n for names in ranks.values() for n in names}
    by_lang: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["language"] and r["repo"] in keep:
            by_lang[r["language"]] += r["stars"]["weekly"]
    langs = [{"language": k, "repos": sum(1 for r in rows if r["language"] == k and r["repo"] in keep)}
             for k, _v in sorted(by_lang.items(), key=lambda kv: -kv[1])[:40]]
    return {"schema": 1, "date": end.isoformat(), "generated_at": now_iso,
            "note": ("Stars and forks are counts of public WatchEvent / ForkEvent in GH Archive for the UTC days up to date; "
                     "description, language, topics and totals come from the GitHub REST API. Not a copy of github.com/trending."),
            "windows": WINDOW_DAYS, "minimum_stars": MIN_STARS, "unit": UNIT, "languages": langs, "ranks": ranks,
            "repos": [r for r in rows if r["repo"] in keep]}


def trending_topics(rows: list[dict[str, Any]], end: date, now_iso: str) -> list[dict[str, Any]]:
    """Rows for trending.json (Worldwide): repos whose stars/day beat their 14-day median, as Technology topics
    with a ``github`` channel (unit stars/day). They have no Wikidata item, so ``id`` is ``gh:owner/name`` and
    ``qid`` is null."""
    out = []
    for r in rows:
        day = r["stars_per_day"]["daily"]
        base = r["baseline"]
        week = r["stars_per_day"]["weekly"]
        if day < TRENDING_FLOOR and week < TRENDING_FLOOR:
            continue
        value = max(day, week)
        ratio = round(day / max(base or 1.0, 1.0), 2) if base is not None else None
        if ratio is None:
            ratio = round(day / max(r["stars_per_day"]["monthly"], 1.0), 2)
        windows = {"24h": {"ratio": ratio, "active": ratio >= 1.5}}
        windows["7d"] = {"ratio": round(week / max(r["stars_per_day"]["monthly"], 1.0), 2),
                         "active": week / max(r["stars_per_day"]["monthly"], 1.0) >= 1.5}
        rising = ratio >= 1.5 or r["new_repo"]
        ch = {"value": day, "unit": UNIT, "baseline": base, "ratio": ratio, "windows": windows, "series": r["series"],
              "forks_per_day": float(r["forks"]["daily"]), "total_stars": r["total_stars"],
              "language": r["language"], "topics": r["topics"], "source": NAME, "source_url": r["url"], "license": LICENSE}
        rank_score = round(math.log10(max(value, 10.0)) ** 2 * math.log10(min(max(ratio, 1.0), 10.0)), 3) if rising else 0.0
        out.append({
            "id": f"gh:{r['repo']}", "qid": None, "label": r["repo"], "native_label": None,
            "description": r["description"] or "GitHub repository", "category": "technology", "category_label": "Technology",
            "category_via": {"kind": "source", "class": "github", "label": "GitHub repository"},
            "region": "global", "trend": "rising" if rising else "steady", "rise": "strong" if ratio >= 1.5 else ("mild" if rising else None),
            "status": "active" if rising else "steady", "ratio": ratio, "volume": value, "volume_unit": UNIT,
            "rank_score": rank_score, "score": min(100, round(2.5 * rank_score)),
            "source_families": ["developer"] if rising else [], "multi_source": False,
            "rising_channels": ["github"] if rising else [], "seasonal": "unknown", "seasonal_detail": None,
            "first_seen": end.isoformat(), "series_end": end.isoformat(), "windows": windows,
            "channels": {"github": ch},
            "evidence": [{"source": NAME, "source_url": r["url"], "license": LICENSE, "title": r["repo"],
                          "value": day, "unit": UNIT}],
            "source": NAME, "source_url": r["url"], "license": LICENSE, "sources": [NAME], "aliases": [],
            "breakdown": r["topics"][:8], "wikipedia_url": None})
    return out

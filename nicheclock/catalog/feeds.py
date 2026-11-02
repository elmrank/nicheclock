"""Milestone-2 sources that fill the weak categories, each with its terms in data/sources.json:

* ``gdacs``         UN/EC Global Disaster Alert and Coordination System: current disaster events   -> Climate
* ``tvmaze``        TVmaze schedule (CC BY-SA, attribution by link): shows airing today, by country -> Entertainment
* ``stackexchange`` Stack Overflow hot questions: tag counts only (no question text)              -> Technology
* ``openlibrary``   Open Library trending books (daily)                                          -> Hobbies and Leisure

Rows are built here, not by ``assemble_topic``: these channels have no Wikipedia series. A row is "rising" only
when its channel carries a real ratio (PyPI); everything else is a present/active "steady" row with the raw number
the source reports. Never a search volume, never a derived score.
"""

from __future__ import annotations

import html
import statistics
from collections import Counter
from datetime import date, timedelta
from typing import Any

from nicheclock.catalog.categories import category_label
from nicheclock.catalog.scoring import RATIO_CAP, SCORE_SCALE, channel_windows, rank_score, window_stats
from nicheclock.http import FetchError, RobotsDisallowed

# Experimental: off by default until the owner has re-read the terms (CLI --experimental enables them).
# Empty now: the GitHub rank runs on the official REST API, Stack Overflow tags wait for a key, PyPI was removed.
EXPERIMENTAL: tuple[str, ...] = ()
PER_SOURCE = 15                # rows kept per source and region
GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
TVMAZE_SCHEDULE = "https://api.tvmaze.com/schedule"
TVMAZE_WEB = "https://api.tvmaze.com/schedule/web"
SE_QUESTIONS = "https://api.stackexchange.com/2.3/questions"
OPENLIBRARY = "https://openlibrary.org/trending/daily.json"

LICENSES = {
    "gdacs": "GDACS (UN/EC JRC); reuse of Commission documents under CC BY 4.0, source acknowledged",
    "tvmaze": "CC BY-SA (TVmaze API); attribution by link to tvmaze.com",
    "stackexchange": "Stack Exchange API (data: Stack Exchange Network); tag names, counts and links only, content is not copied",
    "openlibrary": "Open Library API; titles, authors and links only",
}
UNITS = {
    "gdacs": "alert level as reported by GDACS (Green, Orange, Red)",
    "tvmaze": "TVmaze popularity weight 0-100 (as reported)",
    "stackexchange": "hot questions carrying the tag (of the 100 hottest)",
    "openlibrary": "rank on Open Library's daily trending list",
}
ISO3_TO_REGION = {"IND": "IN", "CHN": "CN", "USA": "US", "IDN": "ID", "PAK": "PK", "NGA": "NG", "BRA": "BR", "BGD": "BD",
                  "RUS": "RU", "ETH": "ET"}
GDACS_TYPES = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano", "DR": "Drought", "WF": "Wildfire"}
GDACS_LEVEL = {"Red": 3, "Orange": 2, "Green": 1}


def feed_row(key: str, rid: str, label: str, description: str, category: str, url: str, end: str, channel: dict[str, Any],
             native_label: str | None = None, trend: str = "steady", ratio: float | None = None, volume: float | None = None,
             windows: dict[str, Any] | None = None, extra_evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One catalog row for a feed item. ``channel`` is stored under ``channels[key]`` with provenance added."""
    ch = dict(channel, unit=UNITS[key], source=key, source_url=url, license=LICENSES[key])
    rising = trend == "rising"
    rs = round(rank_score(volume, ratio, 1), 3) if rising else 0.0
    return {
        "id": f"{key}:{label}", "qid": None, "label": label,
        "native_label": native_label, "description": description, "category": category, "category_label": category_label(category),
        "category_via": {"kind": "source", "class": key, "label": key}, "region": rid, "trend": trend,
        "rise": ("strong" if rising else None), "status": "active" if rising else "steady", "ratio": ratio, "volume": volume,
        "volume_unit": UNITS[key] if volume is not None else None, "rank_score": rs, "score": min(100, round(SCORE_SCALE * rs)),
        "source_families": [key] if rising else [], "multi_source": False, "rising_channels": [key] if rising else [],
        "seasonal": "unknown", "seasonal_detail": None, "first_seen": end, "series_end": end, "windows": windows or {},
        "channels": {key: ch},
        "evidence": [{"source": key, "source_url": url, "license": LICENSES[key], "title": label, "value": volume if volume is not None else ch.get("rank"),
                      "unit": UNITS[key]}] + (extra_evidence or []),
        "source": key, "source_url": url, "license": LICENSES[key], "sources": [key], "aliases": [], "breakdown": [],
        "wikipedia_url": None}


# -- GDACS ------------------------------------------------------------------------------------

def gdacs_rows(client: Any, end: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """{region id or "global": rows}: current disaster events; a country event also lands in its region."""
    try:
        data = client.get_json(GDACS_URL)
    except (FetchError, RobotsDisallowed) as e:
        return {}, {"status": "no_data", "detail": f"{type(e).__name__}: {str(e)[:80]}"}
    events = []
    for f in data.get("features", []):
        p = f.get("properties") or {}
        if p.get("iscurrent") not in (True, "true", "True") or not p.get("name"):
            continue
        level = p.get("alertlevel") or "Green"
        url = (p.get("url") or {}).get("report") if isinstance(p.get("url"), dict) else None
        url = url or f"https://www.gdacs.org/report.aspx?eventtype={p.get('eventtype')}&eventid={p.get('eventid')}"
        events.append((GDACS_LEVEL.get(level, 0), p.get("fromdate") or "", p, url))
    events.sort(key=lambda e: (e[0], e[1]), reverse=True)           # Red first, then the most recent
    out: dict[str, list[dict[str, Any]]] = {}
    for _lv, _d, p, url in events:
        kind = GDACS_TYPES.get(p.get("eventtype"), p.get("eventtype") or "Event")
        desc = html.unescape(p.get("htmldescription") or p.get("description") or "").strip()
        for rid in {"global", ISO3_TO_REGION.get(str(p.get("iso3") or "").upper(), "global")}:
            rows = out.setdefault(rid, [])
            if len(rows) < PER_SOURCE:
                rows.append(feed_row("gdacs", rid, p["name"], desc or kind, "climate", url, end,
                                     {"active": True, "alert": p.get("alertlevel") or "Green", "event_type": kind,
                                      "country": p.get("country"), "from": (p.get("fromdate") or "")[:10] or None}))
    return out, {"status": "ok", "detail": f"{len(events)} current events"}


# -- TVmaze -----------------------------------------------------------------------------------

def tvmaze_rows(client: Any, day: date, regions: list[dict[str, Any]], end: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Shows with an episode airing on ``day``: per country (TVmaze country schedules) and, for Worldwide, the
    union of the US schedule and web channels. Ordered by TVmaze's own popularity weight."""
    out: dict[str, list[dict[str, Any]]] = {}
    seen_world: dict[int, dict[str, Any]] = {}
    ok = 0
    jobs = [(r["id"], TVMAZE_SCHEDULE, {"country": r["iso2"], "date": day.isoformat()}) for r in regions if r["id"] != "global"]
    jobs.append(("global", TVMAZE_WEB, {"date": day.isoformat()}))
    for rid, url, params in jobs:
        try:
            eps = client.get_json(url, params=params)
        except (FetchError, RobotsDisallowed):
            continue
        ok += 1
        shows: dict[int, dict[str, Any]] = {}
        for e in eps if isinstance(eps, list) else []:
            sh = e.get("show") or e.get("_embedded", {}).get("show") or {}
            if sh.get("id") and sh.get("name") and sh.get("weight") is not None:
                shows[sh["id"]] = sh
        if rid == "US" or rid == "global":
            seen_world.update(shows)
        if shows:
            out[rid] = _tv_rows(rid, shows, end)
    if seen_world:
        out["global"] = _tv_rows("global", seen_world, end)
    return out, {"status": "ok" if ok else "no_data", "detail": f"{ok} schedules read, {sum(len(v) for v in out.values())} rows"}


def _tv_rows(rid: str, shows: dict[int, dict[str, Any]], end: str) -> list[dict[str, Any]]:
    best = sorted(shows.values(), key=lambda s: -float(s["weight"]))[:PER_SOURCE]
    rows = []
    for s in best:
        net = (s.get("network") or s.get("webChannel") or {}).get("name")
        rows.append(feed_row("tvmaze", rid, s["name"], f"{s.get('type') or 'TV'}{', ' + s['language'] if s.get('language') else ''}"
                             f"{' on ' + net if net else ''}: episode airing today", "entertainment", s.get("url") or "https://www.tvmaze.com/", end,
                             {"airing_today": True, "value": float(s["weight"]), "network": net}, volume=float(s["weight"])))
    return rows


# -- Stack Exchange ---------------------------------------------------------------------------

SE_CREDIT = "Stack Exchange Network"
SE_KEY_FILE = "~/.config/nicheclock/stackexchange.env"


def stackexchange_key(env: dict[str, str] | None = None) -> str | None:
    """The API key of the registered Stack Apps application: STACKEXCHANGE_KEY, else the one-line env file
    ``~/.config/nicheclock/stackexchange.env`` (override: STACKEXCHANGE_KEY_FILE). Never printed."""
    import os
    from pathlib import Path

    env = env if env is not None else os.environ
    if env.get("STACKEXCHANGE_KEY"):
        return env["STACKEXCHANGE_KEY"].strip()
    path = Path(env.get("STACKEXCHANGE_KEY_FILE") or SE_KEY_FILE).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("STACKEXCHANGE_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        return None
    return None


def stackexchange_rows(client: Any, end: str, key: str | None, cache: Any, now: float | None = None
                       ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Tags of the 100 hottest Stack Overflow questions, counted. Four conditions of the terms are code here:
    an API ``key`` (without one the source is off: ``pending_key``), the ``backoff`` field is obeyed (a response
    that carries one blocks every request until it has passed, remembered in the cache between builds), the
    "Stack Exchange Network" credit is on every row and its evidence, and only tag name + count + tag link are kept."""
    import time

    now = now if now is not None else time.time()
    if not key:
        return {}, {"status": "pending_key", "detail": "no STACKEXCHANGE_KEY (a Stack Apps application key is required by the terms)"}
    found, until = cache.get("sebackoff", "until")
    if found and until and now < until:
        return {}, {"status": "no_data", "detail": f"backoff requested by the API: next request allowed in {int(until - now)} s"}
    try:
        data = client.get_json(SE_QUESTIONS, params={"order": "desc", "sort": "hot", "site": "stackoverflow", "pagesize": 100, "key": key})
    except (FetchError, RobotsDisallowed) as e:
        return {}, {"status": "no_data", "detail": f"{type(e).__name__}: {str(e)[:80]}".replace(key, "***")}
    if data.get("backoff"):
        cache.put("sebackoff", "until", now + float(data["backoff"]))
        cache.commit()
    n = Counter()
    for q in data.get("items", []):
        for t in q.get("tags", []):
            n[t] += 1
    rows = []
    for t, c in n.most_common(PER_SOURCE):
        r = feed_row("stackexchange", "global", t, f"Tag on {c} of the 100 hottest Stack Overflow questions (data: {SE_CREDIT})", "technology",
                     f"https://stackoverflow.com/questions/tagged/{t}", end, {"value": float(c), "hot_questions": c, "credit": SE_CREDIT},
                     volume=float(c))
        r["credit"] = SE_CREDIT
        r["evidence"][0]["credit"] = SE_CREDIT
        rows.append(r)
    return ({"global": rows} if rows else {}), {"status": "ok" if rows else "no_data",
            "detail": f"{len(data.get('items', []))} hot questions, {len(rows)} tags" + (f"; backoff {data['backoff']} s noted" if data.get("backoff") else "")}


# -- Open Library -----------------------------------------------------------------------------

def openlibrary_rows(client: Any, end: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Open Library's daily trending works (one request a day, identifying User-Agent from the shared client)."""
    try:
        data = client.get_json(OPENLIBRARY, params={"limit": PER_SOURCE})
    except (FetchError, RobotsDisallowed) as e:
        return {}, {"status": "no_data", "detail": f"{type(e).__name__}: {str(e)[:80]}"}
    rows = []
    for i, w in enumerate(data.get("works", [])[:PER_SOURCE], start=1):
        if not w.get("title") or not w.get("key"):
            continue
        authors = ", ".join((w.get("author_name") or [])[:2])
        rows.append(feed_row("openlibrary", "global", w["title"], f"Book{' by ' + authors if authors else ''}, trending on Open Library",
                             "hobbies-leisure", f"https://openlibrary.org{w['key']}", end, {"rank": i, "value": float(i)}))
    return ({"global": rows} if rows else {}), {"status": "ok" if rows else "no_data", "detail": f"{len(rows)} trending works"}

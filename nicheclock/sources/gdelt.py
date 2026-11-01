"""GDELT DOC 2.0 API: news-volume spikes (timelinevol) and recent articles (artlist, title + link)."""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.i18n import t
from nicheclock.models import Signal
from nicheclock.regions import COUNTRY_FIPS, GLOBAL, countries, normalize
from nicheclock.sources._common import CACHE_TTL, opt, phrase, search_terms, stamp

NAME = "gdelt"
REGION_SUPPORT = "source_country"   # publisher country filter; global = English-language sources
TERMS_URL = "https://www.gdeltproject.org/about.html"
TERMS_NOTE = ('"all datasets released by the GDELT Project are available for unlimited and '
              'unrestricted use for any academic, commercial, or governmental use of any kind '
              'without fee." Any use or redistribution "must include a citation to the GDELT '
              'Project and a link to this website (https://www.gdeltproject.org/)."')
HOST = "api.gdeltproject.org"
API = f"https://{HOST}/api/v2/doc/doc"
MIN_INTERVAL = 10.0  # GDELT answers 429 when called faster than about every 5-6 s; one queue, 10 s apart
MIN_RATIO = 2.0      # news volume of the last 2 days vs the 4-week mean
PHRASES_PER_QUERY = 3


def parse_timeline(data: dict[str, Any]) -> list[float]:
    try:
        return [float(p["value"]) for p in data["timeline"][0]["data"]]
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def spike(values: list[float], days: int = 28) -> tuple[float, float, float] | None:
    """(mean of the last 2 days, mean of the earlier window, ratio) of a daily-or-finer series."""
    if len(values) < 5:
        return None
    per_day = max(1, len(values) // days)
    recent = statistics.fmean(values[-2 * per_day:])
    base = statistics.fmean(values[:-2 * per_day])
    if base <= 0:
        return (recent, base, float("inf")) if recent > 0 else None
    return recent, base, recent / base


def parse_articles(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for a in data.get("articles", []):
        title, url = (a.get("title") or "").strip(), a.get("url")
        if not title or not url:
            continue
        seen = a.get("seendate") or ""
        try:
            when = datetime.strptime(seen, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            when = None
        out.append({"title": title, "url": url, "domain": a.get("domain"), "created_at": when})
    return out


def _groups(niche: Any) -> list[list[str]]:
    terms = search_terms(niche, int(opt(niche, "gdelt_max_queries", 2)) * PHRASES_PER_QUERY)
    return [terms[i:i + PHRASES_PER_QUERY] for i in range(0, len(terms), PHRASES_PER_QUERY)]


def _query(group: list[str], scope_filter: str) -> str:
    body = " OR ".join(phrase(t) for t in group)
    return (f"({body})" if len(group) > 1 else body) + " " + scope_filter


def _scopes(client: Any, niche: Any, regions: list[str] | None) -> list[tuple[str, str]]:
    """[(region, query filter)]. Global: English-language sources. A country: publishers located
    there (GDELT ``sourcecountry``, FIPS code); at most ``gdelt_max_regions`` countries per niche
    (default 2) because every extra country is two more requests at 10 s apart. Countries without a
    known code, or over the limit, are reported and left for later (planned)."""
    regs = normalize(regions)
    scopes: list[tuple[str, str]] = [(GLOBAL, "sourcelang:english")] if GLOBAL in regs else []
    limit = int(opt(niche, "gdelt_max_regions", 2))
    for c in countries(regs):
        fips = COUNTRY_FIPS.get(c)
        if fips is None:
            client.note(t("source.gdelt.no_country", country=c))
        elif sum(1 for r, _ in scopes if r != GLOBAL) >= limit:
            client.note(t("source.gdelt.country_limit", country=c, limit=limit))
        else:
            scopes.append((c, f"sourcecountry:{fips}"))
    return scopes


def _get(client: Any, params: dict[str, Any]) -> dict[str, Any] | None:
    """One GDELT request through the shared client (10 s apart, cached 24 h). A response already in
    the cache is still served after the host got rate-limited."""
    try:
        resp = client.get(API, params=params, min_interval=MIN_INTERVAL, cache_ttl=CACHE_TTL)
        return resp.json() if resp.status_code == 200 else None
    except (FetchError, RobotsDisallowed, ValueError):
        return None


def _requests(client: Any, niche: Any, regions: list[str] | None) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """[(region, timeline params, articles params)] per scope and phrase group, in priority order
    (identical params = same cache key)."""
    out = []
    for region, flt in _scopes(client, niche, regions):
        for group in _groups(niche):
            q = _query(group, flt)
            out.append((region, {"query": q, "mode": "timelinevol", "format": "json", "timespan": "28d"},
                        {"query": q, "mode": "artlist", "format": "json", "maxrecords": 25,
                         "sort": "datedesc", "timespan": "7d"}))
    return out


def _key(niche: Any, params: dict[str, Any]) -> str:
    return f"{niche.name}|{params['mode']}|{params['query']}"


def _queue_file(client: Any) -> Path | None:
    d = getattr(client, "cache_dir", None)
    return Path(d) / "gdelt_queue.json" if d else None


def load_queue(client: Any) -> list[str]:
    p = _queue_file(client)
    try:
        return list(json.loads(p.read_text(encoding="utf-8"))) if p and p.exists() else []
    except (OSError, ValueError):
        return []


def _save_queue(client: Any, keys: list[str]) -> None:
    p = _queue_file(client)
    if p is None:
        return
    if keys:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(keys), encoding="utf-8")
    elif p.exists():
        p.unlink()


def prime(client: Any, niches: list[Any], day: date,
          regions: dict[str, list[str]] | None = None) -> str | None:
    """Run the GDELT requests of all ``niches`` as ONE queue, 10 s apart. Requests that failed in an
    earlier run go first. When GDELT rate-limits, the remaining requests are saved and go first in
    the next run (the finished ones sit in the 24 h cache, so `collect` reads them for free).
    ``regions`` maps niche name -> its effective regions."""
    regions = regions or {}
    pending = set(load_queue(client))
    work = [(_key(n, p), p) for n in niches if "gdelt" in n.sources
            for _, t, a in _requests(client, n, regions.get(n.name)) for p in (t, a)]
    work.sort(key=lambda kp: kp[0] not in pending)          # stable: queued ones first
    left: list[str] = []
    for i, (key, params) in enumerate(work):
        if client.tripped(HOST):
            left += [k for k, _ in work[i:]]
            break
        if _get(client, params) is None and client.tripped(HOST):
            left += [k for k, _ in work[i:]]                 # this one included: it was not answered
            break
    known = {k for k, _ in work}
    _save_queue(client, left + [k for k in pending if k not in known])
    if left:
        return t("source.gdelt.queued", count=len(left))
    return None


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    """Per scope (global or a country) and group of up to 3 OR-joined phrases: one volume timeline
    (spike vs the 4-week mean) and one list of the last 7 days' articles. Reads what `prime`
    fetched; never waits on GDELT twice. Country scopes are tagged ``approx``: the publisher's
    country, not the story's."""
    min_ratio = float(opt(niche, "gdelt_min_ratio", MIN_RATIO))
    seen = stamp(day)
    out: list[Signal] = []
    groups = _groups(niche)
    reqs = _requests(client, niche, regions)
    for i, (region, spike_req, art_req) in enumerate(reqs):
        group = groups[i % len(groups)]
        prec = "global" if region == GLOBAL else "approx"
        data = _get(client, spike_req)
        sp = spike(parse_timeline(data)) if data else None
        if sp and sp[2] >= min_ratio:
            recent, base, ratio = sp
            shown = None if ratio == float("inf") else round(ratio, 2)
            out.append(Signal(NAME, " / ".join(group), "https://www.gdeltproject.org/", score=shown or 99.0,
                              seen_at=seen, region=region, region_precision=prec,
                              extra={"query": group[0], "ratio": shown, "metric": "news volume",
                                     "recent": round(recent, 4), "base": round(base, 4)}))
        data = _get(client, art_req)
        for a in parse_articles(data or {}):
            out.append(Signal(NAME, a["title"], a["url"], seen_at=seen, region=region, region_precision=prec,
                              extra={"query": group[0], "strict": True, "domain": a["domain"],
                                     "created_at": a["created_at"]}))
    return out

"""Hacker News via the public Algolia HN Search API (titles + links + points only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.models import Signal
from nicheclock.sources._common import CACHE_TTL, opt, phrase, search_terms, stamp

REGION_SUPPORT = "global"            # no country concept: signals stay region=global
NAME = "hn"
TERMS_URL = "https://hn.algolia.com/api"
TERMS_NOTE = ('"This API is built on top of Algolia Search\'s API. It enables developers to access '
              'HN data programmatically" (title, link and points only are kept; links go to the '
              'story or its HN page).')
API = "https://hn.algolia.com/api/v1/search_by_date"


def parse(data: dict[str, Any], query: str, seen: datetime) -> list[Signal]:
    out = []
    for h in data.get("hits", []):
        title = (h.get("title") or h.get("story_title") or "").strip()
        oid = h.get("objectID")
        if not title or not oid:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={oid}"
        out.append(Signal(
            NAME, title, h.get("url") or hn_url, score=float(h.get("points") or 0), seen_at=seen,
            extra={"query": query, "strict": True, "comments": h.get("num_comments") or 0, "hn_url": hn_url,
                   "id": str(oid), "created_at": h.get("created_at")}))
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    days = int(opt(niche, "hn_days", 7))
    min_points = float(opt(niche, "hn_min_points", 10))
    since = int((datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                 - timedelta(days=days)).timestamp())
    seen = stamp(day)
    best: dict[str, Signal] = {}
    for q in search_terms(niche, int(opt(niche, "hn_max_queries", 8))):
        try:
            data = client.get_json(API, params={
                "query": phrase(q), "tags": "story", "hitsPerPage": 20,
                "restrictSearchableAttributes": "title",
                "numericFilters": f"created_at_i>{since},points>={int(min_points)}"}, cache_ttl=CACHE_TTL)
        except (FetchError, RobotsDisallowed):
            continue
        for s in parse(data, q, seen):
            best.setdefault(s.extra["id"], s)
    ranked = sorted(best.values(), key=lambda s: -(s.score or 0))
    for i, s in enumerate(ranked, start=1):
        s.rank = i
    return ranked

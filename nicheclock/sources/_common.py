"""Helpers shared by source modules."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any


CACHE_TTL = 24 * 3600     # query-driven sources cache each response on disk for 24 h


def stamp(day: date) -> datetime:
    """seen_at for signals collected for ``day``: now when day is today (UTC), else its midnight."""
    now = datetime.now(timezone.utc)
    if day == now.date():
        return now
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def search_terms(niche: Any, limit: int) -> list[str]:
    """Keywords and entities interleaved (keyword first), deduplicated, at most ``limit``, so a
    long keyword list cannot starve the entities."""
    from itertools import zip_longest

    seen, out = set(), []
    for pair in zip_longest(niche.keywords, niche.entities):
        for t in pair:
            t = re.sub(r"\s*\([^)]*\)", "", t or "").strip()      # "Merlin (bird)" -> "Merlin"
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out[:limit]


def opt(niche: Any, key: str, default: Any) -> Any:
    return (niche.options or {}).get(key, default)


def phrase(term: str) -> str:
    return f'"{term}"' if " " in term.strip() else term.strip()


_TRAFFIC = re.compile(r"([\d.,]+)\s*([KMB]?)", re.I)


def parse_traffic(text: str | None) -> float | None:
    """'200+' -> 200, '2K+' -> 2000, '1M+' -> 1e6."""
    if not text:
        return None
    m = _TRAFFIC.search(text)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return n * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()]

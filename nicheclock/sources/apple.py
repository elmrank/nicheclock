"""Apple Marketing Tools RSS: top app charts (the official feed generator, not App Store pages)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.models import Signal
from nicheclock.regions import countries
from nicheclock.sources._common import CACHE_TTL, opt, stamp

NAME = "apple"
REGION_SUPPORT = "country"      # one storefront chart per country: exact
TERMS_URL = "https://performance-partners.apple.com/resources/documentation/tools-overview"
TERMS_NOTE = ('"Use the RSS Feed Generator to incorporate lists for top music, apps, books, and '
              'more. You can add these feeds to your website or view them using a standard RSS '
              'feed reader."')
URL = "https://rss.marketingtools.apple.com/api/v2/{cc}/apps/{chart}/100/apps.json"


def parse(data: dict[str, Any], chart: str, cc: str, seen: datetime) -> list[Signal]:
    out = []
    for i, r in enumerate((data.get("feed") or {}).get("results", []), start=1):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        out.append(Signal(NAME, name, r.get("url"), rank=i, geo=cc, seen_at=seen, extra={
            "chart": chart, "artist": r.get("artistName"),
            "genres": [g.get("name") for g in r.get("genres", []) if g.get("name")]}))
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    seen = stamp(day)
    out: list[Signal] = []
    ccs = [c.lower() for c in countries(regions or [])] or opt(niche, "apple_countries", ["us"])
    for cc in ccs:
        for chart in opt(niche, "apple_charts", ["top-free"]):
            try:
                data = client.get_json(URL.format(cc=cc, chart=chart), cache_ttl=CACHE_TTL)
            except (FetchError, RobotsDisallowed):
                continue
            for s in parse(data, chart, cc, seen):
                s.region, s.region_precision = cc.upper(), "exact"
                out.append(s)
    return out

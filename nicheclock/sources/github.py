"""GitHub REST API: newly created repositories matching the niche, by stars."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.models import Signal
from nicheclock.sources._common import CACHE_TTL, opt, search_terms, stamp

REGION_SUPPORT = "global"            # no country concept: signals stay region=global
NAME = "github"
TERMS_URL = "https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies"
TERMS_NOTE = ('"Scraping refers to extracting information from our Service via an automated '
              'process, such as a bot or webcrawler. Scraping does not refer to the collection of '
              'information through our API." (use your own token; "You may not share API tokens '
              'to exceed GitHub\'s rate limitations.")')
API = "https://api.github.com/search/repositories"


def parse(data: dict[str, Any], query: str, seen: datetime) -> list[Signal]:
    out = []
    for r in data.get("items", []):
        name = r.get("full_name")
        if not name:
            continue
        out.append(Signal(NAME, name, r.get("html_url"), score=float(r.get("stargazers_count") or 0),
                          seen_at=seen, extra={
                              "query": query, "strict": True,
                              "description": (r.get("description") or "")[:200],
                              "text": (r.get("description") or "")[:200],
                              "language": r.get("language"), "created_at": r.get("created_at")}))
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    days = int(opt(niche, "github_days", 30))
    since = (day - timedelta(days=days)).isoformat()
    token = os.environ.get(opt(niche, "github_token_env", "")) if opt(niche, "github_token_env", "") else None
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    seen = stamp(day)
    best: dict[str, Signal] = {}
    for q in search_terms(niche, int(opt(niche, "github_max_queries", 5))):
        term = f'"{q}"' if " " in q else q
        try:
            data = client.get_json(API, params={
                "q": f"{term} in:name,description created:>{since}", "sort": "stars",
                "order": "desc", "per_page": 10}, headers=headers, cache_ttl=CACHE_TTL)
        except (FetchError, RobotsDisallowed):
            continue
        for s in parse(data, q, seen):
            best.setdefault(s.term, s)
    ranked = sorted(best.values(), key=lambda s: -(s.score or 0))
    for i, s in enumerate(ranked, start=1):
        s.rank = i
    return ranked

"""Federal Register API: new documents (and upcoming comment deadlines / effective dates)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.match import match_text
from nicheclock.models import Event, Signal
from nicheclock.regions import GLOBAL, normalize
from nicheclock.sources._common import CACHE_TTL, opt, phrase, search_terms, stamp

NAME = "federal_register"
REGION_SUPPORT = "global"       # no country selection ...
FIXED_REGION = "US"             # ... because everything in it is US federal: tagged US, exact
TERMS_URL = "https://www.federalregister.gov/developers/documentation/api/v1"
TERMS_NOTE = ('"Due to aggressive automated scraping of FederalRegister.gov and eCFR.gov, '
              'programmatic access to these sites is limited to access to our extensive developer '
              'APIs." (US government works carry no copyright: 17 U.S.C. 105.)')
API = "https://www.federalregister.gov/api/v1/documents.json"
FIELDS = ["title", "html_url", "publication_date", "type", "agency_names", "comments_close_on",
          "effective_on", "document_number", "abstract"]
WINDOW_DAYS = 45     # one fetch serves both signals (recent) and events (future dates)


def parse(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in data.get("results", []) if r.get("title") and r.get("html_url")]


def _fetch(client: Any, niche: Any, day: date) -> list[tuple[str, dict[str, Any], bool]]:
    """[(query, document, title_match)] for documents whose title (True) or only whose abstract
    (False) names a keyword/entity of the niche; full-text hits matching neither are dropped."""
    out: list[tuple[str, dict[str, Any], bool]] = []
    seen_docs: set[str] = set()
    since = (day - timedelta(days=WINDOW_DAYS)).isoformat()
    for q in search_terms(niche, int(opt(niche, "fr_max_queries", 6))):
        try:
            data = client.get_json(API, params={
                "conditions[term]": phrase(q), "conditions[publication_date][gte]": since,
                "order": "relevance", "per_page": 20, "fields[]": FIELDS}, cache_ttl=CACHE_TTL)
        except (FetchError, RobotsDisallowed):
            continue
        for r in parse(data):
            if r["html_url"] in seen_docs:
                continue
            if match_text(r["title"], niche).ok:
                title_match = True
            elif match_text(r["title"] + " " + (r.get("abstract") or ""), niche).ok:
                title_match = False
            else:
                continue
            seen_docs.add(r["html_url"])
            out.append((q, r, title_match))
    return out


def _applies(regions: list[str] | None) -> bool:
    """A US-only source: skipped when only other countries were requested."""
    regs = normalize(regions)
    return GLOBAL in regs or FIXED_REGION in regs


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    """Title matches are normal signals. Abstract-only matches are flagged ``low_confidence`` (the
    pipeline keeps them out of the default feed)."""
    if not _applies(regions):
        return []
    cutoff = (day - timedelta(days=int(opt(niche, "fr_days", 7)))).isoformat()
    seen = stamp(day)
    out: list[Signal] = []
    for q, r, title_match in _fetch(client, niche, day):
        if (r.get("publication_date") or "") < cutoff:
            continue
        extra = {"query": q, "strict": True, "type": r.get("type"), "agencies": r.get("agency_names") or [],
                 "published": r.get("publication_date"), "created_at": r.get("publication_date"),
                 "comments_close_on": r.get("comments_close_on"), "effective_on": r.get("effective_on"),
                 "title_match": title_match}
        if not title_match:
            extra["low_confidence"] = True
            extra["text"] = (r.get("abstract") or "")[:500]
        out.append(Signal(NAME, r["title"], r["html_url"], seen_at=seen, extra=extra,
                          region=FIXED_REGION, region_precision="exact"))
    out.sort(key=lambda s: s.extra["published"] or "", reverse=True)
    for i, s in enumerate(out, start=1):
        s.rank = i
    return out


def collect_events(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Event]:
    events: dict[tuple[str, str, str], Event] = {}
    if not _applies(regions):
        return []
    for _, r, title_match in _fetch(client, niche, day):
        if not title_match:          # events only from documents whose title names the niche
            continue
        for field, kind in (("comments_close_on", "comment deadline"), ("effective_on", "effective date")):
            v = r.get(field)
            if not v:
                continue
            d = date.fromisoformat(v)
            if d >= day:
                events.setdefault((r["html_url"], kind, v), Event(
                    kind=kind, title=r["title"], date=d, source=NAME, url=r["html_url"], niche=niche.name,
                    region=FIXED_REGION, region_precision="exact"))
    return sorted(events.values(), key=lambda e: e.date)

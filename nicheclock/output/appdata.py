"""The data the report page (``app/app.js``) draws, in the schema of ``hosted/site/demo-data/README.md`` (``"schema": 1``).

The site's demo.json and the report that ``nicheclock run`` writes are the same shape, so one page renders both.
Fields a self-hosted run does not have (series, Search Console numbers) are ``null`` and the page leaves them out.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from nicheclock.i18n import t
from nicheclock.models import Event, FeedItem
from nicheclock.output import Feed
from nicheclock.output.common import CITED_SOURCES, llm_line

SCHEMA = 1

# Event kinds of a self-hosted run -> the schema's three kinds (event | deadline | rising); the page draws only the first two.
EVENT_KIND = {"early riser": "rising", "comment deadline": "deadline", "effective date": "deadline"}

# Display names of the sources (ids are the module names in nicheclock.sources).
SOURCE_NAMES = {
    "wikipedia": "Wikimedia pageviews", "mediawiki": "Wikipedia", "wikidata": "Wikidata", "hn": "Hacker News",
    "gdelt": "GDELT", "google_trends": "Google Trends", "github": "GitHub", "apple": "Apple charts",
    "federal_register": "Federal Register", "rss": "RSS feed", "ics": "Calendar", "nager": "Public holidays",
}


def _id(*parts: str | None) -> str:
    return hashlib.sha1("\0".join(p or "" for p in parts).encode()).hexdigest()[:10]


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def source_name(source: str) -> str:
    return SOURCE_NAMES.get(source, source.replace("_", " ").capitalize())


def _attribution(feed: Feed, source: str) -> str | None:
    return t(f"out.cite.{source}") if source in CITED_SOURCES else None


def _topic(feed: Feed, it: FeedItem) -> dict:
    sources = [{
        "id": e["source"], "name": source_name(e["source"]), "title": e["title"], "url": e.get("url"),
        "at": e.get("at"), "note": e.get("note"), "attribution": _attribution(feed, e["source"]),
        "license": None, "terms_url": (feed.sources.get(e["source"]) or (None, None))[0],
    } for e in it.evidence or []]
    return {
        "id": _id(it.niche, it.title), "niche": it.niche, "title": it.title, "score": round(it.score, 1),
        "rule_score": it.rule_score, "fit": it.llm_fit,
        "reason": it.llm_reason or it.llm_note or it.why, "why": it.why,
        "region": it.region, "regions": it.regions,
        "first_seen": None, "last_seen": _stamp(it.at)[:10],
        "series": None, "url": it.url, "sources": sources,
    }


def _event(feed: Feed, e: Event) -> dict:
    return {
        "id": _id(e.niche, e.title, e.date.isoformat()), "niche": e.niche, "title": e.title,
        "date": e.date.isoformat(), "date_precision": e.date_precision,
        "publish_by": e.publish_by.isoformat() if e.publish_by else None,
        "kind": EVENT_KIND.get(e.kind, "event"), "region": e.region, "url": e.url, "source": e.source,
        "attribution": _attribution(feed, e.source),
    }


def build(feed: Feed) -> dict:
    """The page data for one feed (or one per-country view of it)."""
    gen = feed.generated_at
    niches = [n.name for n in feed.niches]
    topics = [_topic(feed, i) for i in feed.items]
    upcoming = [_event(feed, e) for e in feed.upcoming]
    return {
        "schema": SCHEMA,
        "project": feed.project,
        "generated_at": _stamp(gen),
        "today": gen.astimezone(timezone.utc).date().isoformat(),
        "lag_days": None,
        "niches": [{
            "id": n, "name": n, "blurb": None, "regions": next(m.regions for m in feed.niches if m.name == n),
            "topic_count": sum(1 for x in topics if x["niche"] == n),
            "upcoming_count": sum(1 for x in upcoming if x["niche"] == n), "site": None,
        } for n in niches],
        "topics": topics,
        "upcoming": upcoming,
        "gaps": {
            "sample": False, "label": None, "note": None, "sites": [],
            "rows": [{
                "niche": g.niche, "query": g.title, "page": g.url, "clicks": None, "impressions": None,
                "position": None, "status": None, "why": g.why, "angle": g.angle, "questions": g.questions,
                "score": g.score,
            } for g in feed.gaps],
        },
        "llm": ({"provider": feed.llm.get("provider"), "model": feed.llm.get("model"), "note": llm_line(feed),
                 "fit_weight": feed.llm.get("fit_weight")} if feed.llm else None),
        "region": feed.region,
    }

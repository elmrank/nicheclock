"""User-supplied RSS / Atom feeds (titles + links only). The user is responsible for the feeds they add."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.models import Event, Signal
from nicheclock.sources._common import opt, stamp

REGION_SUPPORT = "global"            # your own feeds carry no country: region=global
NAME = "rss"
TERMS_URL = "https://www.rssboard.org/rss-specification"
TERMS_NOTE = ("Feeds are supplied by the user and used under each publisher's own terms (the user "
              "is responsible for them); only item titles, links and dates are kept, never summaries "
              "or content.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, *names: str) -> str | None:
    for c in el:
        if _local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return None


def parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse(xml_text: str) -> list[dict[str, Any]]:
    """RSS 2.0, RSS 1.0 (RDF) and Atom -> [{title, link, published: datetime|None}]."""
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _child_text(el, "title")
        link = _child_text(el, "link")
        if not link:   # Atom: <link href="..."/>
            for c in el:
                if _local(c.tag) == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    link = c.get("href")
                    break
        if not title:
            continue
        items.append({"title": title, "link": link,
                      "published": parse_date(_child_text(el, "pubDate", "published", "updated", "date"))})
    return items


def _fetch_all(client: Any, niche: Any) -> list[tuple[str, list[dict[str, Any]], bool]]:
    """[(url, items, is_watch)]: ``rss`` feeds, then the feeds you asked to ``watch``."""
    out = []
    feeds = [(u, False) for u in niche.rss] + [(u, True) for u in getattr(niche, "watch", []) if u not in niche.rss]
    for url, watch in feeds:
        try:
            out.append((url, parse(client.get_text(url, cache_ttl=3 * 3600)), watch))
        except (FetchError, RobotsDisallowed, ET.ParseError):
            continue
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    max_age = timedelta(days=int(opt(niche, "rss_days", 7)))
    seen = stamp(day)
    horizon = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    out: list[Signal] = []
    for url, items, watch in _fetch_all(client, niche):
        rank = 0
        for it in items:
            pub = it["published"]
            if pub is not None and (pub > horizon or seen - pub > max_age):
                continue
            rank += 1
            extra = {"feed": url, "published": pub.isoformat() if pub else None}
            if watch:
                extra["watch"] = True
            out.append(Signal(NAME, it["title"], it["link"], rank=rank, seen_at=seen, extra=extra))
    return out


def collect_events(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Event]:
    """Feed items dated in the future (event / release feeds) become upcoming events."""
    out = []
    for url, items, _ in _fetch_all(client, niche):
        for it in items:
            pub = it["published"]
            if pub is not None and pub.date() > day:
                out.append(Event(kind="scheduled", title=it["title"], date=pub.date(), source=NAME,
                                 url=it["link"], niche=niche.name))
    return out

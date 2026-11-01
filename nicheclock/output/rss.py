"""RSS 2.0 feed of the project's topics, upcoming events and gaps."""

from __future__ import annotations

import hashlib
from email.utils import format_datetime
from xml.sax.saxutils import escape

from nicheclock.i18n import t
from nicheclock.output import Feed
from nicheclock.output.common import citations, fit_text, publish_by, region_text, when


def _guid(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def render(feed: Feed) -> str:
    day = feed.generated_at.date().isoformat()
    items = []

    def add(title: str, link: str | None, desc: str, guid: str, cat: str | None) -> None:
        link = link or (feed.urls[0] if feed.urls else "https://example.invalid/")
        items.append(
            "<item>"
            f"<title>{escape(title)}</title><link>{escape(link)}</link>"
            f"<description>{escape(desc)}</description>"
            + (f"<category>{escape(cat)}</category>" if cat else "")
            + f'<guid isPermaLink="false">{guid}</guid>'
            f"<pubDate>{format_datetime(feed.generated_at)}</pubDate></item>")

    show = feed.shows_regions()
    for it in feed.items:
        tag = f"({region_text(it.region, it.region_precision)}) " if show else ""
        add(it.title, it.url, f"[{it.score:.0f}] {tag}{it.why}" + (f" | {fit_text(it)}" if fit_text(it) else ""),
            _guid(feed.project, it.niche or "", it.region, it.title, day), it.niche)
    for e in feed.upcoming:
        pb = publish_by(e)
        add(t("out.rss.upcoming", when=when(e), title=e.title), e.url,
            t("out.rss.event", kind=e.kind, by=pb) if pb else t("out.rss.event_nodate", kind=e.kind),
            _guid(feed.project, "event", e.niche or "", e.kind, e.title, e.date.isoformat()), e.niche)
    for g in feed.gaps:
        add(t("out.rss.gap", title=g.title), g.url, g.why + (" | " + t("out.angle", angle=g.angle) if g.angle else ""),
            _guid(feed.project, "gap", g.title, day), None)
    footer = " ".join(citations(feed))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{escape(t('out.title', project=feed.project))}</title>"
            f"<link>{escape(feed.urls[0] if feed.urls else 'https://example.invalid/')}</link>"
            f"<description>{escape(t('out.rss.description', footer=footer))}</description>"
            f"<lastBuildDate>{format_datetime(feed.generated_at)}</lastBuildDate>"
            + "".join(items) + "</channel></rss>\n")

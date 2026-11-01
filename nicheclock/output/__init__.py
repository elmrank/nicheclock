"""Per-project outputs under ``<out_dir>/<project>/``: feed.json, feed.md, index.html, feed.xml."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nicheclock.models import Event, FeedItem, utcnow
from nicheclock.regions import GLOBAL, MULTI, keeps


@dataclass
class NicheMeta:
    name: str
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=lambda: [GLOBAL])   # what was asked for this niche
    region_mode: str = "combined"


@dataclass
class Feed:
    project: str
    urls: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)
    niches: list[NicheMeta] = field(default_factory=list)
    items: list[FeedItem] = field(default_factory=list)       # topics, best first
    upcoming: list[Event] = field(default_factory=list)       # soonest first
    gaps: list[FeedItem] = field(default_factory=list)        # GSC gaps
    low_confidence: list[FeedItem] = field(default_factory=list)  # abstract-only matches: JSON only
    sources: dict[str, tuple[str, str]] = field(default_factory=dict)  # attribution of sources used
    llm: dict | None = None            # model usage of this run (provider, model, calls, tokens, cost...)
    regions: list[str] = field(default_factory=lambda: [GLOBAL])   # asked for, whole project
    region_mode: str = "combined"
    region: str | None = None          # set on a filtered view (see filter_feed)

    def countries(self) -> list[str]:
        """Country codes that actually appear in items and events, sorted."""
        codes = {c for i in self.items for c in (i.regions or [i.region])} | {e.region for e in self.upcoming}
        return sorted(c for c in codes if c not in (GLOBAL, MULTI))

    def shows_regions(self) -> bool:
        """Worth printing region labels: something in the feed is tied to a country."""
        return bool(self.countries())


def filter_feed(feed: Feed, region: str) -> Feed:
    """The feed as read for one country: that country's items plus the global ones."""
    return dataclasses.replace(
        feed, region=region.upper(),
        items=[i for i in feed.items if keeps(i.region, region, i.regions)],
        upcoming=[e for e in feed.upcoming if keeps(e.region, region)],
        low_confidence=[i for i in feed.low_confidence if keeps(i.region, region, i.regions)])


def _write(feed: Feed, d: Path, assets: str) -> dict[str, Path]:
    from nicheclock.output import html, jsonout, md, rss

    d.mkdir(parents=True, exist_ok=True)
    files = {
        "feed.json": jsonout.render(feed), "feed.md": md.render(feed),
        "index.html": html.render(feed, assets), "feed.xml": rss.render(feed),
    }
    paths = {}
    for name, text in files.items():
        p = d / name
        p.write_text(text, encoding="utf-8")
        paths[name] = p
    return paths


def write_all(feed: Feed, out_dir: Path) -> dict[str, Path]:
    """The project's files, plus one filtered copy per country under ``regions/<CC>/`` when the
    feed spans two or more countries."""
    from nicheclock.output import page

    page.install_assets(Path(out_dir) / "assets")       # the report page's css, js and font, shared by every project
    d = Path(out_dir) / feed.project
    paths = _write(feed, d, "../assets/")
    codes = feed.countries()
    if len(codes) >= 2:
        for code in codes:
            _write(filter_feed(feed, code), d / "regions" / code, "../../../assets/")
    return paths


def write_index(projects: list[str], out_dir: Path) -> Path:
    """``<out_dir>/index.html`` linking the projects (what `serve` shows first)."""
    from html import escape

    from nicheclock.i18n import t
    links = "\n".join(f'<li><a href="{escape(p)}/index.html">{escape(p)}</a> '
                      f'(<a href="{escape(p)}/feed.xml">{escape(t("out.link.rss"))}</a>, '
                      f'<a href="{escape(p)}/feed.json">{escape(t("out.link.json"))}</a>)</li>'
                      for p in projects)
    p = Path(out_dir) / "index.html"
    p.write_text(f'<!doctype html><meta charset="utf-8"><title>{escape(t("out.index.title"))}</title>'
                 f'<body style="font-family:system-ui;max-width:40rem;margin:2rem auto">'
                 f"<h1>{escape(t('out.index.title'))}</h1><ul>\n{links}\n</ul></body>\n", encoding="utf-8")
    return p

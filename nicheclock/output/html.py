"""The HTML report: the shared page (``output/app/``) with this feed's data embedded, so it also opens from disk."""

from __future__ import annotations

from html import escape

from nicheclock.i18n import t
from nicheclock.output import Feed, appdata, page
from nicheclock.output.common import citations, llm_line


def _bar(feed: Feed) -> str:
    links = [f'<a href="feed.xml">{escape(t("out.link.rss"))}</a>',
             f'<a href="feed.json">{escape(t("out.link.json"))}</a>',
             f'<a href="feed.md">{escape(t("out.link.markdown"))}</a>']
    note = ""
    if feed.shows_regions() or feed.region:
        line = t("out.regions_line", regions=", ".join(feed.regions), mode=feed.region_mode)
        if feed.region:
            line += " · " + t("out.region_view", region=feed.region)
        note = f'<p class="bar-note">{escape(line)}'
        if not feed.region and len(feed.countries()) >= 2:       # links to the per-country views
            note += f" · {escape(t('out.region_views'))} " + " ".join(
                f'<a href="regions/{c}/index.html">{c}</a>' for c in feed.countries())
        note += "</p>"
    return ('<header class="bar"><div class="bar-top">'
            f'<strong class="brand">{escape(feed.project)}</strong><span class="pitch"></span>'
            f'<nav class="bar-links" aria-label="Formats">{" ".join(links)}</nav></div>'
            f'<div class="bar-sub"><p id="stamp"></p>{note}</div></header>')


def render(feed: Feed, assets: str = "assets/") -> str:
    """``assets`` is the path from this file to the folder holding app.css, app.js and the font."""
    foot = "".join(f"<p>{escape(x)}</p>" for x in [llm_line(feed), *citations(feed), t("out.built_with")] if x)
    return page.page(
        title=t("out.title", project=feed.project), bar=_bar(feed), foot=foot,
        css=f"{assets}app.css", js=f"{assets}app.js",
        head='<link rel="alternate" type="application/rss+xml" href="feed.xml">',
        data=appdata.build(feed), stamp_fmt=t("out.generated", when="{time}"))

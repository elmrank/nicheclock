"""MediaWiki action API: facts only (canonical title, short description, URL).

Never returns article prose: Wikipedia text is CC BY-SA; titles and short descriptions are facts.
The API (api.php) is the interface Wikimedia documents for bots, while en.wikipedia.org/robots.txt
disallows /w/ for crawlers. The shared client therefore exempts exactly en.wikipedia.org/w/api.php
from the robots check (http.ROBOTS_EXEMPT); the identity User-Agent, the 1 req/s/host limit and
Retry-After handling still apply. Other wiki languages are not exempt and follow their robots.txt."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "mediawiki"
TERMS_URL = "https://www.mediawiki.org/wiki/API:Etiquette"
TERMS_NOTE = ('MediaWiki API:Etiquette (https://www.mediawiki.org/wiki/API:Etiquette): "There is no hard '
              'speed limit on read requests, but be considerate and try not to take a site down." and '
              '"API requests to Wikimedia wikis must include a meaningful User-Agent header." '
              'Reading of those pages: the Action API at en.wikipedia.org/w/api.php is documented for '
              'bots, so only that endpoint skips the robots.txt check. '
              '"Please note that these licenses do allow commercial uses of your contributions, as '
              'long as such uses are compliant with the terms of the respective licenses." and '
              '"facts you contribute to the projects may be reused freely without attribution"; '
              'bots must "Accurately identify your User-Agent" '
              '(https://wikitech.wikimedia.org/wiki/Robot_policy).')
BATCH = 50


@dataclass
class Page:
    title: str          # canonical, spaces not underscores
    url: str
    description: str    # short description (a fact), may be empty
    disambiguation: bool = False


def _url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":()_,")


def parse_query(data: dict[str, Any], names: list[str], lang: str = "en") -> dict[str, Page | None]:
    """Map requested names to pages through the API's ``normalized`` / ``redirects`` chains."""
    q = data.get("query", {})
    alias = {n["from"]: n["to"] for n in q.get("normalized", [])}
    alias.update({r["from"]: r["to"] for r in q.get("redirects", [])})
    pages = {p["title"]: p for p in q.get("pages", []) if not p.get("missing") and not p.get("invalid")}
    out: dict[str, Page | None] = {}
    for name in names:
        t = name
        for _ in range(4):       # normalized -> redirect -> normalized ...
            if t in alias:
                t = alias[t]
        p = pages.get(t)
        if p is None:
            out[name] = None
            continue
        pp = p.get("pageprops") or {}
        out[name] = Page(title=p["title"], url=_url(lang, p["title"]),
                         description=(p.get("description") or "").strip(),
                         disambiguation="disambiguation" in pp)
    return out


def resolve_titles(client: Any, names: list[str], lang: str = "en") -> dict[str, Page | None]:
    """Entity names -> Wikipedia pages. Missing and disambiguation pages count as unresolved (None)."""
    out: dict[str, Page | None] = {n: None for n in names}
    for i in range(0, len(names), BATCH):
        chunk = names[i:i + BATCH]
        try:
            resp = client.get(f"https://{lang}.wikipedia.org/w/api.php", params={
                "action": "query", "titles": "|".join(chunk), "redirects": 1,
                "prop": "pageprops|description", "ppprop": "disambiguation", "format": "json",
                "formatversion": 2}, cache_ttl=24 * 3600)
            if resp.status_code != 200:
                continue
            found = parse_query(resp.json(), chunk, lang)
        except (FetchError, RobotsDisallowed, ValueError):
            continue
        for n, p in found.items():
            out[n] = None if p is None or p.disambiguation else p
    return out

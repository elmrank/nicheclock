"""Offline fakes for the onboarding tests: a fake http Client serving an in-memory site + Wikipedia API."""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nicheclock.http import FetchError, RobotsDisallowed

FIX = Path(__file__).parent / "fixtures" / "onboard"
HOST = "https://birdsite.test"

BIRDS = ["snowy-owl", "peregrine-falcon", "kestrel", "nuthatch", "chickadee"]
BIRD_FORMATS = ["best-builds", "tier-list", "patch-notes-2025", "beginner-tips", "how-to-attract-rare-visitors",
                "complete-guide", "review", "field-guide"]
GEAR = ["trailview", "ridgeline", "meadowlark", "harborview"]
GEAR_FORMATS = ["beginner-checklist", "waterproofing-guide", "model-review", "spring-sale-drop",
                "best-budget-picks", "optics-tier-list"]


def urlset(urls: list[str]) -> bytes:
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + body + "</urlset>").encode()


def species_urls() -> list[str]:
    return [f"{HOST}/birds/{g}-{f}" for g in BIRDS for f in BIRD_FORMATS]


def gear_urls() -> list[str]:
    return [f"{HOST}/gear/{d}-{f}" for d in GEAR for f in GEAR_FORMATS]


def title_for(url: str) -> str:
    slug = urlsplit(url).path.rsplit("/", 1)[-1]
    return " ".join(w.capitalize() for w in slug.split("-")) + " | Birdsite"


class Resp:
    def __init__(self, status: int = 200, content: bytes = b"", headers: dict | None = None, url: str = ""):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
        self.url = url

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    @property
    def ok(self) -> bool:
        return self.status_code == 200

    def json(self) -> Any:
        return json.loads(self.content)


class FakeClient:
    """Serves `files` (url -> bytes | Resp). Records every request; 404 for the rest.

    `disallow`: URL prefixes that raise RobotsDisallowed (as Core's Client does).
    """

    def __init__(self, files: dict[str, Any], disallow: tuple[str, ...] = (), wiki: "FakeWiki | None" = None):
        self.files = dict(files)
        self.disallow = disallow
        self.requests: list[str] = []
        self.wiki = wiki
        self.views: dict[str, list[int]] = {}      # article title -> daily views served by the pageviews API

    def get(self, url: str, params: dict | None = None, *, headers: dict | None = None, robots: bool = True,
            min_interval: float | None = None, timeout: float | None = None):
        if self.wiki is not None and "wikipedia.org/w/api.php" in url:
            self.requests.append(url + "?" + json.dumps(params, sort_keys=True))
            return self.wiki.handle(params or {})
        self.requests.append(url)
        if "metrics/pageviews/per-article" in url:
            from urllib.parse import unquote
            title = unquote(url.split("/user/")[1].split("/daily/")[0]).replace("_", " ")
            if title not in self.views:
                return Resp(404, b"{}", url=url)
            return Resp(200, json.dumps({"items": [{"views": v} for v in self.views[title]]}).encode(), url=url)
        if robots and any(url.startswith(d) for d in self.disallow):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        hit = self.files.get(url)
        if hit is None:
            return Resp(404, b"not found", url=url)
        if isinstance(hit, Resp):
            return hit
        ctype = "text/html; charset=utf-8" if url.endswith((".html", "/")) or "/birds/" in url or "/gear/" in url \
            else "application/octet-stream"
        return Resp(200, hit, {"content-type": ctype}, url)

    def get_text(self, url: str, params: dict | None = None, **kw):
        r = self.get(url, params, **kw)
        if r.status_code != 200:
            raise FetchError(f"{url}: HTTP {r.status_code}")
        return r.text

    def get_json(self, url: str, params: dict | None = None, **kw):
        return json.loads(self.get_text(url, params, **kw))


def site_files(*, with_sitemap: bool = True) -> dict[str, Any]:
    birds, gear = species_urls(), gear_urls()
    files: dict[str, Any] = {f"{HOST}/robots.txt": (FIX / "robots.txt").read_bytes()}
    if with_sitemap:
        files[f"{HOST}/sitemap_index.xml"] = (FIX / "sitemap_index.xml").read_bytes()
        files[f"{HOST}/birds-sitemap.xml"] = urlset(birds)
        files[f"{HOST}/gear-sitemap.xml.gz"] = gzip.compress(urlset(gear))
        for bad in ("image", "category", "author"):
            files[f"{HOST}/{bad}-sitemap.xml"] = urlset([f"{HOST}/{bad}/x-{i}" for i in range(3)])
    for u in birds + gear:
        files[u] = f"<html><head><title>{title_for(u)}</title></head><body><h1>{title_for(u).split(' | ')[0]}</h1></body></html>".encode()
    links = "".join(f'<a href="/birds/{g}-tier-list">{g}</a>' for g in BIRDS)
    files[f"{HOST}/"] = (f'<html><head><title>Birdsite | Birds and gear</title>'
                         f'<meta property="og:site_name" content="Birdsite"></head><body><h1>Birdsite</h1>{links}</body></html>').encode()
    return files


class FakeWiki:
    """Minimal MediaWiki API: `pages` maps exact title -> canonical title ('!' = disambiguation, 'ok' = exists without short description, else = the short description)."""

    def __init__(self, pages: dict[str, str], redirects: dict[str, str] | None = None, opensearch: dict | None = None,
                 extracts: dict[str, str] | None = None):
        self.extracts = extracts or {}
        self.pages = pages
        self.redirects = redirects or {}
        self.opensearch = opensearch or {}
        self.calls: list[dict] = []

    def handle(self, params: dict) -> Resp:
        self.calls.append(dict(params))
        if params.get("action") == "opensearch":
            q = params["search"]
            return Resp(200, json.dumps([q, self.opensearch.get(q, []), [], []]).encode())
        titles = params["titles"].split("|")
        pages, redirects = [], []
        seen: set[str] = set()
        for t in titles:
            tgt = self.redirects.get(t, t)
            if tgt != t:
                redirects.append({"from": t, "to": tgt})
            if tgt in seen:
                continue
            seen.add(tgt)
            state = self.pages.get(tgt)
            if state is None:
                pages.append({"title": tgt, "missing": True})
            elif state == "!":
                pages.append({"title": tgt, "pageprops": {"disambiguation": ""}})
            else:
                pg: dict[str, Any] = {"title": tgt}
                if params.get("prop") == "extracts":
                    pg["extract"] = self.extracts.get(tgt, "")
                if state not in ("ok", ""):
                    pg["pageprops"] = {"wikibase-shortdesc": state}
                pages.append(pg)
        q: dict[str, Any] = {"pages": pages}
        if redirects:
            q["redirects"] = redirects
        return Resp(200, json.dumps({"query": q}).encode())

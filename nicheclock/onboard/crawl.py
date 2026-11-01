"""Read a user's own site: robots.txt -> sitemap(s) -> URL list -> <title>/<h1> of <=50 sampled pages.

All fetching goes through Core's shared `http.Client` (identity UA, 1 req/s/host, robots check).
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

import gzip
import html
import re
import time
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from math import gcd
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from nicheclock.http import HttpError

from .extract import clean_titles, fold, host_of, is_content_url, section_of

MAX_PAGES = 50
MAX_URLS = 5000
MAX_SITEMAPS = 25
MAX_SITEMAP_BYTES = 30 * 1024 * 1024
MAX_HTML_CHARS = 400_000

_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s][^<\]]*?)\s*(?:\]\]>)?\s*</loc>", re.I)
_ROBOTS_SITEMAP = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.I | re.M)
_GUESS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml", "/sitemap.xml.gz")
# child sitemaps that carry no articles
_SKIP_CHILD = ("image", "video", "author", "user", "tag", "taxonom", "categor", "attachment")


@dataclass
class Page:
    url: str
    title: str = ""
    h1: str = ""
    links: list[str] = field(default_factory=list)   # distinct outbound (other-site) http(s) links


@dataclass
class SiteCrawl:
    base: str                                  # https://example.com
    host: str
    sitemaps: list[str] = field(default_factory=list)       # sitemap files read
    urls: list[str] = field(default_factory=list)           # content URLs (deduped, capped)
    pages: list[Page] = field(default_factory=list)         # fetched pages
    site_names: list[str] = field(default_factory=list)     # og:site_name values
    description: str = ""                                    # home page <meta name="description">
    fetches: int = 0                                         # page fetches (<= MAX_PAGES)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def link_counts(self, urls: set[str] | None = None) -> Counter:
        """Outbound link count per external host (www. stripped), over all sampled pages or only `urls`."""
        c: Counter = Counter()
        for pg in self.pages:
            if urls is None or pg.url in urls:
                c.update(host_of(u).removeprefix("www.") for u in pg.links)
        return c

    def titles(self) -> dict[str, tuple[str, str]]:
        """URL -> (title, h1) with shared brand segments stripped."""
        clean, _ = clean_titles([p.title for p in self.pages], self.site_names)
        return {p.url: (c, p.h1) for p, c in zip(self.pages, clean)}


# ---------------------------------------------------------------- helpers


def normalize_base(url: str) -> str:
    url = url.strip()
    if "//" not in url:
        url = "https://" + url
    p = urlsplit(url)
    return urlunsplit((p.scheme or "https", p.netloc.lower(), "", "", ""))


def _same_site(host: str, url: str) -> bool:
    h = host_of(url)
    a, b = host.removeprefix("www."), h.removeprefix("www.")
    return b == a or b.endswith("." + a)


def decode_sitemap(content: bytes) -> str:
    """Bytes of a sitemap (plain or gzip, by magic number) -> text. Bounded against gzip bombs."""
    if content[:2] == b"\x1f\x8b":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            content = d.decompress(content, MAX_SITEMAP_BYTES)
        except zlib.error:
            try:
                content = gzip.decompress(content)[:MAX_SITEMAP_BYTES]
            except (OSError, EOFError):
                return ""
    return content.decode("utf-8", "replace")


def parse_sitemap(text: str) -> tuple[bool, list[str]]:
    """-> (is_index, locs). Also accepts plain text lists of URLs."""
    locs = [html.unescape(m.strip()) for m in _LOC.findall(text)]
    if locs:
        return "<sitemapindex" in text[:5000].lower(), locs
    if "<" not in text:
        return False, [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("http://", "https://"))]
    return False, []


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1s: list[str] = []
        self.site_name = ""
        self.description = ""
        self._in: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "title" and not self.title:
            self._in, self._buf = "title", []
        elif tag == "h1" and len(self.h1s) < 4:
            self._in, self._buf = "h1", []
        elif tag == "meta":
            a = {k.lower(): (v or "") for k, v in attrs}
            if a.get("property", "").lower() == "og:site_name" and not self.site_name:
                self.site_name = a.get("content", "").strip()
            if a.get("name", "").lower() == "description" and not self.description:
                self.description = " ".join(a.get("content", "").split())[:300]

    def handle_endtag(self, tag: str) -> None:
        if self._in == tag:
            text = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = text
            elif text:
                self.h1s.append(text)
            self._in = None

    def handle_data(self, data: str) -> None:
        if self._in:
            self._buf.append(data)


def parse_page(text: str) -> tuple[str, str, str]:
    """HTML -> (title, h1, og:site_name). The h1 skips headings that just repeat the site name (logo)."""
    p = _HeadParser()
    try:
        p.feed(text[:MAX_HTML_CHARS])
        p.close()
    except Exception:  # malformed markup: keep what was parsed
        pass
    name = fold(p.site_name)
    h1 = next((h for h in p.h1s if not name or fold(h) != name), "")
    return p.title, h1, p.site_name


def parse_description(text: str) -> str:
    """HTML -> the <meta name="description"> content ('' when absent)."""
    p = _HeadParser()
    try:
        p.feed(text[:MAX_HTML_CHARS])
        p.close()
    except Exception:
        pass
    return p.description


_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"'#]+)""", re.I)
MAX_LINKS_PER_PAGE = 300


def external_links(text: str, page_url: str, host: str) -> list[str]:
    """Distinct http(s) links on a page that point to another site (document order)."""
    out: dict[str, None] = {}
    for href in _HREF.findall(text[:MAX_HTML_CHARS]):
        u = urljoin(page_url, html.unescape(href.strip()))
        p = urlsplit(u)
        if p.scheme in ("http", "https") and p.hostname and not _same_site(host, u):
            out[urlunsplit((p.scheme, p.netloc.lower(), p.path, p.query, ""))] = None
            if len(out) >= MAX_LINKS_PER_PAGE:
                break
    return list(out)


def _coprime_step(n: int) -> int:
    step = max(1, round(n * 0.6180339887))
    while gcd(step, n) != 1:
        step += 1
    return step


def sample_urls(urls: list[str], k: int) -> list[str]:
    """Deterministic sample of k URLs spread across sections (root-level flat URLs form one section).

    Quotas per section ~ sqrt(size); inside a section picks are spread with a golden-ratio stride.
    """
    if k <= 0 or not urls:
        return []
    by_sec: dict[str, list[str]] = defaultdict(list)
    for u in sorted(set(urls)):
        by_sec[section_of(u)].append(u)
    secs = sorted(by_sec, key=lambda s: (-len(by_sec[s]), s))
    if len(urls) <= k:
        return sorted(set(urls))
    weight = {s: len(by_sec[s]) ** 0.5 for s in secs}
    total = sum(weight.values())
    quota = {s: min(len(by_sec[s]), int(k * weight[s] / total)) for s in secs}
    left = k - sum(quota.values())
    # largest remainder, then fill any still-open capacity in section order
    order = sorted(secs, key=lambda s: (-(k * weight[s] / total - int(k * weight[s] / total)), -len(by_sec[s]), s))
    for s in order:
        if left <= 0:
            break
        if quota[s] < len(by_sec[s]):
            quota[s] += 1
            left -= 1
    while left > 0:
        grew = False
        for s in secs:
            if left > 0 and quota[s] < len(by_sec[s]):
                quota[s] += 1
                left -= 1
                grew = True
        if not grew:
            break
    picked: list[str] = []
    for s in secs:
        lst = by_sec[s]
        step = _coprime_step(len(lst))
        picked += [lst[(j * step) % len(lst)] for j in range(quota[s])]
    return picked[:k]


# ---------------------------------------------------------------- crawling


def _fetch(client: Any, url: str, crawl: SiteCrawl, *, robots: bool = True):
    """GET through the shared client; None (plus a warning) on any failure or non-200."""
    try:
        resp = client.get(url, robots=robots)
    except HttpError as e:
        crawl.warnings.append(f"{url}: {e}")
        return None
    if resp.status_code != 200:
        if resp.status_code != 404:
            crawl.warnings.append(f"{url}: HTTP {resp.status_code}")
        return None
    return resp


def _read_sitemaps(client: Any, crawl: SiteCrawl, start: list[str], max_urls: int, max_sitemaps: int) -> None:
    todo = list(start)
    seen: set[str] = set()
    urls: dict[str, None] = {}
    while todo and len(urls) < max_urls and len(crawl.sitemaps) < max_sitemaps:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        resp = _fetch(client, sm, crawl)
        if resp is None:
            continue
        crawl.sitemaps.append(sm)
        is_index, locs = parse_sitemap(decode_sitemap(resp.content))
        if is_index:
            todo += [c for c in locs if not any(h in c.lower() for h in _SKIP_CHILD) and _same_site(crawl.host, c)]
        else:
            for u in locs:
                if _same_site(crawl.host, u) and is_content_url(u):
                    urls[u] = None
                    if len(urls) >= max_urls:
                        break
    crawl.urls = list(urls)


def _links_from_home(text: str, base_url: str, host: str) -> list[str]:
    out: dict[str, None] = {}
    for href in re.findall(r"""<a\s[^>]*?href\s*=\s*["']([^"'#]+)""", text[:MAX_HTML_CHARS], re.I):
        u = urljoin(base_url, html.unescape(href.strip()))
        p = urlsplit(u)
        if p.scheme in ("http", "https") and _same_site(host, u) and is_content_url(u):
            out[urlunsplit((p.scheme, p.netloc.lower(), p.path, "", ""))] = None
    return list(out)


def crawl_site(client: Any, url: str, *, max_pages: int = MAX_PAGES, max_urls: int = MAX_URLS,
               max_sitemaps: int = MAX_SITEMAPS) -> SiteCrawl:
    t0 = time.monotonic()
    base = normalize_base(url)
    crawl = SiteCrawl(base=base, host=host_of(base))

    # robots.txt: fetched for its Sitemap: lines. The client enforces the rules on every other request.
    robots_text = ""
    resp = _fetch(client, base + "/robots.txt", crawl, robots=False)
    if resp is not None:
        robots_text = resp.text
    declared = [u for u in _ROBOTS_SITEMAP.findall(robots_text) if _same_site(crawl.host, u)]
    _read_sitemaps(client, crawl, declared or [base + g for g in _GUESS], max_urls, max_sitemaps)

    pages: list[Page] = []
    # homepage first: its title/h1 are the best one-line description of the site, and its links
    # are the fallback URL list for sites that publish no sitemap.
    budget = max_pages
    home = _fetch(client, base + "/", crawl) if budget > 0 else None
    if budget > 0:
        crawl.fetches += 1
        budget -= 1
    if home is not None:
        title, h1, site_name = parse_page(home.text)
        pages.append(Page(base + "/", title, h1, external_links(home.text, base + "/", crawl.host)))
        if site_name:
            crawl.site_names.append(site_name)
        crawl.description = parse_description(home.text)
        if not crawl.urls:
            crawl.urls = _links_from_home(home.text, base + "/", crawl.host)[:max_urls]
            if crawl.urls:
                crawl.warnings.append(_t("onboard.crawl.no_sitemap"))
    if not crawl.urls:
        crawl.warnings.append(_t("onboard.crawl.no_urls"))

    home_url = base + "/"
    for u in sample_urls([u for u in crawl.urls if u.rstrip("/") != base], budget):
        if u.rstrip("/") == home_url.rstrip("/"):
            continue
        resp = _fetch(client, u, crawl)
        crawl.fetches += 1
        if resp is None:
            continue
        ctype = resp.headers.get("content-type", "text/html") if getattr(resp, "headers", None) else "text/html"
        if "html" not in ctype.lower():
            continue
        title, h1, site_name = parse_page(resp.text)
        pages.append(Page(u, title, h1, external_links(resp.text, u, crawl.host)))
        if site_name:
            crawl.site_names.append(site_name)
    crawl.pages = pages
    if crawl.site_names:
        crawl.site_names = [n for n, _ in Counter(crawl.site_names).most_common(3)]
    crawl.seconds = time.monotonic() - t0
    return crawl

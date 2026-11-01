"""Feed discovery: the sites the user's own pages link to -> RSS/Atom feeds worth following.

1. count outbound links on the sampled pages (done by crawl.py), per external domain;
2. for the top domains fetch the homepage and read <link rel="alternate" type="application/rss+xml|atom+xml">;
3. fetch each candidate feed and keep it only if it parses and has recent items.

Everything goes through the shared client (identity UA, robots.txt, <=1 req/s/host). The feeds are the
*user's* choice: they are written to `rss:` and the user is responsible for each feed's terms.
"""
from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from html.parser import HTMLParser
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin, urlsplit

from nicheclock.http import HttpError
from nicheclock.match import match_text
from nicheclock.sources.rss import parse as parse_feed

# "Never ship" (ARCHITECTURE.md): nothing from these is ever suggested, as a link domain or as a feed host.
# Add your own comma-separated domains with the NICHECLOCK_NEVER_SHIP environment variable (for sites whose
# terms forbid automated collection and that matter to your niche).
NEVER_SHIP = (
    "reddit.com", "redd.it", "redditmedia.com", "arcticshift.com", "arctic-shift.photon-reddit.com",
    "producthunt.com", "amazon.", "amzn.to", "news.google.", "google.com", "googleusercontent.com",
    "trends.google.",
    # Chinese hot lists
    "weibo.com", "weibo.cn", "zhihu.com", "baidu.com", "bilibili.com", "douyin.com", "toutiao.com", "tieba.baidu.com",
)
# Not publishers worth following: social networks, link shorteners, infrastructure, reference sites, app stores.
SKIP = (
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    "youtube.com", "youtu.be", "twitch.tv", "discord.com", "discord.gg", "t.me", "telegram.org", "whatsapp.com",
    "bit.ly", "t.co", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly", "lnkd.in",
    "wikipedia.org", "wikimedia.org", "wikidata.org", "archive.org", "schema.org", "w3.org", "creativecommons.org",
    "apple.com", "play.google.", "apps.apple.com", "microsoft.com", "github.com", "gitlab.com",
    "doubleclick.net", "googletagmanager.com", "gravatar.com", "wordpress.org", "wordpress.com", "wp.com",
    "medium.com", "substack.com", "patreon.com", "paypal.com", "buymeacoffee.com", "ko-fi.com",
    "bsky.app", "threads.net", "mastodon.social", "linktr.ee", "eneba.com", "g2a.com", "kinguin.net",
    "instant-gaming.com", "fanatical.com", "humblebundle.com", "gog.com", "epicgames.com", "akamaihd.net",
    "mailchimp.com", "feedburner.com", "feedly.com", "cloudflare.com", "stripe.com", "calendly.com",
)
FEED_TYPES = ("application/rss+xml", "application/atom+xml")
MAX_AGE_DAYS = 365


def _match(host: str, patterns: Iterable[str]) -> bool:
    host = host.lower().removeprefix("www.")
    for p in patterns:
        if p.endswith("."):                       # "amazon." = any TLD: amazon.com, amazon.co.uk
            if host.startswith(p) or f".{p}" in f".{host}":
                return True
        elif host == p or host.endswith("." + p):
            return True
    return False


def denied(host: str) -> bool:
    """True for never-ship domains and for sites that are not publishers."""
    return never_ship(host) or _match(host, SKIP)


def _extra_never_ship() -> tuple[str, ...]:
    return tuple(d.strip().lower() for d in os.environ.get("NICHECLOCK_NEVER_SHIP", "").split(",") if d.strip())


def never_ship(host: str) -> bool:
    return _match(host, (*NEVER_SHIP, *_extra_never_ship()))


@dataclass
class Feed:
    url: str
    domain: str
    title: str = ""
    items: int = 0
    latest: datetime | None = None
    sample: list[str] = field(default_factory=list)
    channel: str = ""
    titles: list[str] = field(default_factory=list)   # recent item titles, newest first (<= 30)
    overlap: float = 0.0                              # share of titles that hit the niche's keywords/entities
    watch: bool = False                               # named by the user with --watch
    suggested: bool = False                           # found by init with 5-10% overlap: suggested for `watch:`


class _AlternateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []   # (href, title)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower().split() and a.get("type", "").lower().split(";")[0].strip() in FEED_TYPES \
                and a.get("href"):
            self.found.append((html.unescape(a["href"].strip()), a.get("title", "").strip()))


def alternate_feeds(page_html: str, base_url: str) -> list[str]:
    """Feed URLs advertised by a page, in document order; comment feeds are skipped."""
    p = _AlternateParser()
    try:
        p.feed(page_html[:400_000])
        p.close()
    except Exception:
        pass
    out: list[str] = []
    for href, title in p.found:
        u = urljoin(base_url, href)
        low = (u + " " + title).lower()
        if urlsplit(u).scheme not in ("http", "https") or "comment" in low or "special:" in low \
                or "recentchanges" in low:                       # comment feeds and wiki change logs are not publications
            continue
        if u not in out:
            out.append(u)
    return out


def top_domains(link_counts: Counter, limit: int = 15, exclude: Iterable[str] = ()) -> list[tuple[str, int]]:
    ex = {e.removeprefix("www.") for e in exclude}
    ranked = sorted(((d, n) for d, n in link_counts.items() if d and d not in ex and not denied(d)),
                    key=lambda kv: (-kv[1], kv[0]))
    return ranked[:limit]


PROBE_PATHS = ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml")
MIN_OVERLAP = 0.10        # discovered feed goes to `rss:`
WATCH_OVERLAP = 0.05      # 5-10%: suggested for `watch:` (lower floor in the pipeline)
_DEAL = re.compile(r"giveaway|deals?\b|coupon|promo(?:s|tion|tional)?\b|discount|freebie|bargain|sweepstake|"
                   r"free[- ]games?|price[- ]drop|cheapest|voucher", re.I)


def is_deal_feed(url: str, channel: str, titles: Sequence[str]) -> bool:
    """Giveaway / deal / coupon feeds: named so in the URL or channel title, or mostly deal headlines."""
    if _DEAL.search(url) or _DEAL.search(channel or ""):
        return True
    return bool(titles) and sum(1 for t in titles if _DEAL.search(t)) / len(titles) >= 0.5


def is_own(host: str, own: Iterable[str]) -> bool:
    h = host.lower().removeprefix("www.")
    return any(h == o or h.endswith("." + o) for o in (x.lower().removeprefix("www.") for x in own))


def _channel_title(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "title" and (el.text or "").strip():
            return el.text.strip()
    return ""


def verify_feed(client: Any, url: str, now: datetime | None = None, *, own: Iterable[str] = (),
                skip_deals: bool = True, policy: Any = None) -> Feed | None:
    """Fetch + parse a feed; None when unreachable, not a feed, empty, stale, a deal feed, on a denied host
    or on the user's own site. `policy(host) -> bool` says which hosts are refused (default: `denied`)."""
    now = now or datetime.now(timezone.utc)
    policy = policy or denied
    try:
        resp = client.get(url)
    except HttpError:
        return None
    if resp.status_code != 200:
        return None
    final = getattr(resp, "url", "") or url
    for u in (url, final):
        host = urlsplit(u).hostname or ""
        if policy(host) or is_own(host, own):
            return None
    try:
        items = parse_feed(resp.text)
    except (ET.ParseError, ValueError):
        return None
    if not items:
        return None
    items.sort(key=lambda i: i.get("published") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    dates = [i["published"] for i in items if i.get("published")]
    latest = max(dates) if dates else None
    if latest is not None and now - latest > timedelta(days=MAX_AGE_DAYS):
        return None
    titles = [i["title"] for i in items[:30]]
    channel = _channel_title(resp.text)
    if skip_deals and is_deal_feed(url, channel, titles):
        return None
    domain = (urlsplit(url).hostname or "").removeprefix("www.")
    return Feed(url=url, domain=domain, items=len(items), latest=latest, sample=titles[:2], channel=channel,
                titles=titles)


def discover_site(client: Any, start: str, *, own: Iterable[str] = (), per_domain: int = 2, probe: bool = True,
                  skip_deals: bool = True, policy: Any = None, now: datetime | None = None) -> list[Feed]:
    """Valid feeds of one site: <link rel=alternate> on `start` and the homepage; when the site advertises none,
    the usual feed paths are probed. Every request goes through the client (robots, 1 req/s/host)."""
    policy = policy or denied
    if "//" not in start:
        start = "https://" + start
    parts = urlsplit(start)
    host = parts.hostname or ""
    if not host or policy(host):
        return []
    origin = f"{parts.scheme}://{parts.netloc}"
    if is_own(host, own):
        return []
    kw = dict(own=own, skip_deals=skip_deals, policy=policy, now=now)
    good: list[Feed] = []
    if re.search(r"(?:rss|feed|atom|\.xml)", parts.path, re.I):          # the user pasted a feed URL itself
        f = verify_feed(client, start, **kw)
        if f:
            return [f]
    pages = [start] if parts.path not in ("", "/") else []
    pages.append(origin + "/")
    reachable = False
    alternates: list[str] = []
    for pg in pages:
        try:
            resp = client.get(pg)
        except HttpError:
            continue
        if resp.status_code != 200:
            continue
        reachable = True
        final = getattr(resp, "url", "") or pg
        if policy(urlsplit(final).hostname or "") or is_own(urlsplit(final).hostname or "", own):
            return []                                     # redirected to a denied host or to the user's own site
        alternates += [u for u in alternate_feeds(resp.text, final) if u not in alternates]
    for u in alternates[:4]:
        f = verify_feed(client, u, **kw)
        if f:
            good.append(f)
        if len(good) >= per_domain:
            break
    if not alternates and probe and reachable:
        for path in PROBE_PATHS:
            f = verify_feed(client, origin + path, **kw)
            if f:
                good.append(f)
                break
    for f in good:
        f.domain = host.removeprefix("www.")
    return good


def discover_feeds(client: Any, domains: Sequence[str], *, own: Iterable[str] = (), per_domain: int = 2,
                   now: datetime | None = None) -> dict[str, list[Feed]]:
    """{domain: valid feeds} for external domains the user's pages link to."""
    out: dict[str, list[Feed]] = {}
    for dom in domains:
        found = discover_site(client, f"https://{dom}/", own=own, per_domain=per_domain, now=now)
        if found:
            out[dom] = found
    return out


def discover_watch(client: Any, urls: Sequence[str], *, own: Iterable[str] = (), per_site: int = 2,
                   now: datetime | None = None) -> tuple[list[Feed], list[str]]:
    """Feeds of the sites the user names with --watch -> (feeds, problems). Only never-ship domains are refused:
    the user chose these sites and is responsible for their terms; deal feeds are kept for the same reason."""
    feeds: list[Feed] = []
    problems: list[str] = []
    for u in urls:
        host = urlsplit(u if "//" in u else "https://" + u).hostname or ""
        if never_ship(host):
            problems.append(f"{u}: {host} is on the never-ship list, not watched")
            continue
        if is_own(host, own):
            problems.append(f"{u}: that is your own site, nothing to watch")
            continue
        found = discover_site(client, u, own=own, per_domain=per_site, skip_deals=False, policy=never_ship, now=now)
        if not found:
            problems.append(f"{u}: no valid feed found (no <link rel=alternate>, none of {', '.join(PROBE_PATHS)})")
        for f in found:
            f.watch = True
            if all(f.url != g.url for g in feeds):
                feeds.append(f)
    return feeds, problems


def overlap(feed: Feed, keywords: Sequence[str], entities: Sequence[str] = (), limit: int = 30) -> float:
    """Share of the feed's recent titles that contain any niche keyword or entity."""
    titles = feed.titles[:limit]
    if not titles or not (keywords or entities):
        return 0.0
    niche = SimpleNamespace(keywords=list(keywords), entities=list(entities), exclude=[])
    return sum(1 for t in titles if match_text(t, niche).hits) / len(titles)


def feeds_for_niche(by_domain: dict[str, list[Feed]], niche_links: Counter, site_links: Counter,
                    keywords: Sequence[str], entities: Sequence[str] = (), limit: int = 5,
                    min_overlap: float = MIN_OVERLAP) -> list[Feed]:
    """Top feeds for one niche. A feed must have >= min_overlap topical overlap with the niche; the best per
    domain come first (five domains beat one prolific domain), ties broken by links from the niche's own pages."""
    scored: dict[str, list[Feed]] = {}
    for d, fs in by_domain.items():
        keep = []
        for f in fs:
            o = overlap(f, keywords, entities)
            if o >= min_overlap:
                keep.append(replace(f, overlap=o))
        if keep:
            scored[d] = sorted(keep, key=lambda f: (-f.overlap, f.url))
    order = sorted(scored, key=lambda d: (-scored[d][0].overlap, -niche_links.get(d, 0), -site_links.get(d, 0), d))
    picked = [scored[d][0] for d in order]
    for d in order:
        picked += scored[d][1:]
    return picked[:limit]

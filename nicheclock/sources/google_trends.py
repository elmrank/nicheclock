"""Google Trends: the public "trending now" RSS feed (not explore / internal RPC)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any
from urllib.parse import quote

from nicheclock.models import Signal
from nicheclock.regions import countries
from nicheclock.sources._common import opt, parse_traffic, stamp

NAME = "google_trends"
REGION_SUPPORT = "country"      # one RSS feed per country (geo=XX): exact
TERMS_URL = "https://support.google.com/trends/answer/4365538?hl=en"
TERMS_NOTE = ('"You can use any information from Google Trends, subject to the Google Terms of '
              'Service. If you reuse Trends data, attribute the information to Google with a '
              'citation." Data source: Google Trends (https://www.google.com/trends).')
RSS_URL = "https://trends.google.com/trending/rss"
_NS = {"ht": "https://trends.google.com/trending/rss"}


def parse(xml_text: str, geo: str | None = None) -> list[Signal]:
    """Parse the trending RSS into signals (rank = position in the feed)."""
    root = ET.fromstring(xml_text)
    out: list[Signal] = []
    for i, item in enumerate(root.iter("item"), start=1):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news = []
        for n in item.findall("ht:news_item", _NS):
            news.append({
                "title": (n.findtext("ht:news_item_title", default="", namespaces=_NS) or "").strip(),
                "url": (n.findtext("ht:news_item_url", default="", namespaces=_NS) or "").strip(),
                "source": (n.findtext("ht:news_item_source", default="", namespaces=_NS) or "").strip(),
            })
        traffic = item.findtext("ht:approx_traffic", default=None, namespaces=_NS)
        out.append(Signal(
            source=NAME, term=title, url=f"https://trends.google.com/trending?geo={geo}" if geo else None,
            rank=i, score=parse_traffic(traffic), geo=geo,
            extra={"traffic": traffic, "published": item.findtext("pubDate"), "news": news[:3]}))
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    """One feed per requested country; with no country requested, ``options.geo`` (default US).
    The signals are tagged with the country they were read for (exact)."""
    signals: list[Signal] = []
    geos = countries(regions or []) or [g.upper() for g in opt(niche, "geo", ["US"])]
    for geo in geos:
        text = client.get_text(RSS_URL, params={"geo": geo}, cache_ttl=3 * 3600)   # trending moves faster than 24 h
        seen = stamp(day)
        for s in parse(text, geo):
            s.seen_at = seen
            s.region, s.region_precision = geo, "exact"
            signals.append(s)
    return signals

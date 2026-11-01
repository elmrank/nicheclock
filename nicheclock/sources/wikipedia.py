"""Wikipedia pageviews (Wikimedia REST, CC0): daily top list + per-article growth."""

from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.match import relevant
from nicheclock.i18n import t
from nicheclock.models import Signal
from nicheclock.regions import COUNTRY_LANGUAGE, GLOBAL, countries, normalize
from nicheclock.sources import mediawiki
from nicheclock.sources._common import CACHE_TTL, opt, stamp

NAME = "wikipedia"
REGION_SUPPORT = "language"     # a country maps to its main language edition: approx
TERMS_URL = "https://dumps.wikimedia.org/other/pageviews/readme.html"
TERMS_NOTE = ('"All Analytics datasets are available under the Creative Commons CC0 dedication."; '
              'bots must "Accurately identify your User-Agent", honor robots.txt and Retry-After '
              '(https://wikitech.wikimedia.org/wiki/Robot_policy).')
BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
TOP_URL = BASE + "/top/{lang}.wikipedia/all-access/{y}/{m}/{d}"
ART_URL = BASE + "/per-article/{lang}.wikipedia.org/all-access/user/{title}/daily/{start}/{end}"
MIN_RATIO = 1.5      # a rising entity/top article must beat 1.5x its 14-day median
MIN_RECENT = 50.0    # ... and average >= 50 views/day over the last 3 days


def parse_top(data: dict[str, Any]) -> list[tuple[int, str, int]]:
    """[(rank, title, views)] for real articles (no Main_Page, Special:, Category: ...)."""
    try:
        arts = data["items"][0]["articles"]
    except (KeyError, IndexError, TypeError):
        return []
    out = []
    for a in arts:
        t = a.get("article", "")
        if t == "Main_Page" or ":" in t or t.startswith("-"):
            continue
        out.append((int(a["rank"]), t.replace("_", " "), int(a["views"])))
    return out


def parse_series(data: dict[str, Any]) -> list[tuple[date, int]]:
    out = []
    for it in data.get("items", []):
        ts = it["timestamp"]
        out.append((date(int(ts[:4]), int(ts[4:6]), int(ts[6:8])), int(it["views"])))
    return sorted(out)


def growth(series: list[tuple[date, int]]) -> tuple[float, float, float] | None:
    """(mean of last 3 days, median of the 14 days before, ratio); None with < 10 days of data."""
    views = [v for _, v in series]
    if len(views) < 10:
        return None
    recent = sum(views[-3:]) / 3
    base = statistics.median(views[-17:-3] if len(views) >= 17 else views[:-3])
    ratio = recent / max(base, 1.0)
    return recent, base, ratio


def week_over_week(series: list[tuple[date, int]]) -> tuple[float, float, float] | None:
    """(mean views of the last 7 days, mean of the 7 days before, ratio); None with < 14 days."""
    views = [v for _, v in series]
    if len(views) < 14:
        return None
    this, prev = sum(views[-7:]) / 7, sum(views[-14:-7]) / 7
    return this, prev, this / max(prev, 1.0)


def _article_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":()_,")


def fetch_series(client: Any, lang: str, title: str, end: date) -> list[tuple[date, int]]:
    url = ART_URL.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""),
                         start=f"{end - timedelta(days=20):%Y%m%d}00", end=f"{end:%Y%m%d}00")
    try:
        resp = client.get(url, cache_ttl=CACHE_TTL)
    except (FetchError, RobotsDisallowed):
        return []
    return parse_series(resp.json()) if resp.status_code == 200 else []


def _top_for(client: Any, lang: str, day: date) -> tuple[date, list[tuple[int, str, int]]]:
    for back in (1, 2, 3):          # the daily top list lags by about a day
        d = day - timedelta(days=back)
        url = TOP_URL.format(lang=lang, y=f"{d:%Y}", m=f"{d:%m}", d=f"{d:%d}")
        try:
            resp = client.get(url, cache_ttl=CACHE_TTL)
        except (FetchError, RobotsDisallowed):
            continue
        if resp.status_code == 200:
            return d, parse_top(resp.json())
    return day - timedelta(days=1), []


def _collect_lang(client: Any, niche: Any, day: date, lang: str) -> list[Signal]:
    min_ratio = float(opt(niche, "wiki_min_ratio", MIN_RATIO))
    seen = stamp(day)
    top_day, top = _top_for(client, lang, day)
    signals: dict[str, Signal] = {}

    # 1. articles in today's top list that look like this niche
    cands = [Signal(NAME, t, _article_url(lang, t), rank=r, score=float(v), geo=lang, seen_at=seen,
                    extra={"article": True, "views": v, "list": "top", "top_day": top_day.isoformat()})
             for r, t, v in top]
    matched = relevant(cands, niche)
    for s in matched:
        signals[s.term] = s
    # 2. entities: resolve to titles, measure growth
    entities = list(niche.entities)[: int(opt(niche, "wiki_max_entities", 15))]
    titles = mediawiki.resolve_titles(client, entities, lang) if entities else {}
    candidates = [p.title for p in titles.values() if p] + [s.term for s in matched[:10]]
    for title in dict.fromkeys(candidates):
        series = fetch_series(client, lang, title, top_day)
        g, w = growth(series), week_over_week(series)
        if g is None:
            continue
        recent, base, ratio = g
        sig = signals.get(title)
        if sig is None:
            rising = (ratio >= min_ratio and recent >= MIN_RECENT) or (
                w is not None and w[2] >= min_ratio and w[0] >= MIN_RECENT)
            if not rising:
                continue
            sig = signals[title] = Signal(NAME, title, _article_url(lang, title), score=round(recent, 1), geo=lang,
                                          seen_at=seen, extra={"article": True, "list": "entity"})
        sig.extra.update(views_3d=round(recent, 1), base_14d=round(base, 1), ratio=round(ratio, 2))
        if w is not None:
            sig.extra.update(views_7d=round(w[0], 1), prev_7d=round(w[1], 1), wow=round(w[2], 2))
        sig.extra["entity"] = title in {p.title for p in titles.values() if p}
    return list(signals.values())


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    """No country requested: the ``wiki_lang`` edition (default en), region=global. With countries:
    each country's main language edition (``COUNTRY_LANGUAGE``), signals tagged with the country at
    ``approx`` precision (a language is not a country; countries sharing a language share the data).
    Entity growth needs the MediaWiki API, which is only exempt from robots.txt on en.wikipedia.org,
    so other editions use the top-articles list and per-article series of matched articles only."""
    regs = normalize(regions)
    out: list[Signal] = []
    if GLOBAL in regs:
        out += _collect_lang(client, niche, day, opt(niche, "wiki_lang", "en"))
    by_lang: dict[str, list[str]] = {}
    for c in countries(regs):
        lang = COUNTRY_LANGUAGE.get(c)
        if lang is None:
            client.note(t("source.wikipedia.no_language", country=c))
            continue
        by_lang.setdefault(lang, []).append(c)
    for lang, cs in by_lang.items():
        sigs = _collect_lang(client, niche, day, lang)
        for c in cs:
            out += [replace(s, region=c, region_precision="approx", extra=dict(s.extra)) for s in sigs]
    return out

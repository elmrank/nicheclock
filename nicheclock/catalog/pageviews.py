"""Wikimedia pageviews (CC0) for the catalog: daily top lists, per-article daily and monthly series."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.sources.wikipedia import parse_series, parse_top

NAME = "wikimedia_pageviews"
LICENSE = "CC0 (Wikimedia Analytics datasets)"
BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
TOP = BASE + "/top/{lang}.wikipedia/all-access/{y}/{m:02d}/{d:02d}"
ART = BASE + "/per-article/{lang}.wikipedia.org/all-access/user/{title}/{gran}/{start}/{end}"
# English plus four large editions; the same QID found in two of them is one topic.
WIKIS = ("en", "es", "de", "fr", "ja")


def article_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":()_,")


def top_url(lang: str, day: date) -> str:
    return TOP.format(lang=lang, y=day.year, m=day.month, d=day.day)


def top_articles(client: Any, lang: str, day: date, retries: int = 0) -> list[tuple[str, int]] | None:
    """[(title, views)] of the day's top articles (real articles only); None when not published yet.

    The API sometimes answers 404 for a day that exists and 200 a moment later; ``retries`` repeats a 404
    that many times, 3 s apart (``latest_day`` probes with 0 because there a 404 is the normal answer)."""
    for attempt in range(retries + 1):
        try:
            resp = client.get(top_url(lang, day))
        except (FetchError, RobotsDisallowed):
            return None
        if resp.status_code == 200:
            return [(t, v) for _rank, t, v in parse_top(resp.json())]
        if attempt < retries:
            time.sleep(3)
    return None


def latest_day(client: Any, today: date, max_back: int = 4) -> date | None:
    """The newest day that is complete in BOTH endpoints: the top list is published first, the per-article
    series a few hours later, and a missing per-article day must not be read as zero views."""
    for back in range(1, max_back + 1):
        d = today - timedelta(days=back)
        top = top_articles(client, "en", d)
        if top and daily_series(client, "en", top[0][0], d, days=2) is not None:
            return d
    return None


def daily_series(client: Any, lang: str, title: str, end: date, days: int = 28) -> list[int] | None:
    """Daily user views, oldest first, ``days`` values ending on ``end`` (missing days are 0).
    None when the request failed or the last day is not published yet; all 0 for an article without views."""
    start = end - timedelta(days=days - 1)
    url = ART.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""), gran="daily",
                     start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    try:
        resp = client.get(url)
    except (FetchError, RobotsDisallowed):
        return None
    if resp.status_code == 404:
        return [0] * days
    if resp.status_code != 200:
        return None
    got = dict(parse_series(resp.json()))
    if got and max(got) < end:
        return None          # the API has not published ``end`` for this article yet: not a zero day
    return [int(got.get(start + timedelta(days=i), 0)) for i in range(days)]


def monthly_series(client: Any, lang: str, title: str, end: date) -> dict[tuple[int, int], int] | None:
    """{(year, month): views} from July 2015 (the start of the user-agent split) to ``end``."""
    url = ART.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""), gran="monthly",
                     start="20150701", end=end.strftime("%Y%m%d"))
    try:
        resp = client.get(url)
    except (FetchError, RobotsDisallowed):
        return None
    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        return None
    out = {}
    for it in resp.json().get("items", []):
        ts = it["timestamp"]
        out[(int(ts[:4]), int(ts[4:6]))] = int(it["views"])
    return out


FEED = "https://api.wikimedia.org/feed/v1/wikipedia/{lang}/featured/{y}/{m:02d}/{d:02d}"


def featured(client: Any, lang: str, day: date) -> dict[str, set[str]]:
    """Titles the Main Page pushed that day: ``tfa`` (today's featured article) and ``otd`` (pages linked
    from "On this day"), from Wikimedia's featured-feed endpoint. Empty sets where an edition has none.
    These pages spike for a day because they sit on the front page, not because people went looking."""
    out: dict[str, set[str]] = {"tfa": set(), "otd": set()}
    try:
        resp = client.get(FEED.format(lang=lang, y=day.year, m=day.month, d=day.day))
    except (FetchError, RobotsDisallowed):
        return out
    if resp.status_code != 200:
        return out
    try:
        data = resp.json()
    except ValueError:
        return out
    t = ((data.get("tfa") or {}).get("titles") or {}).get("normalized")
    if t:
        out["tfa"].add(t)
    for ev in data.get("onthisday") or []:
        for pg in ev.get("pages") or []:
            n = (pg.get("titles") or {}).get("normalized")
            if n:
                out["otd"].add(n)
    return out


def access_split(client: Any, lang: str, title: str, end: date, days: int = 14) -> tuple[list[int], list[int]] | None:
    """(desktop, mobile-web) daily user views for ``days`` days ending on ``end``: the access split the topviews
    heuristics use to spot bot-like traffic. None when either request fails or ``end`` is not published yet."""
    out = []
    start = end - timedelta(days=days - 1)
    for access in ("desktop", "mobile-web"):
        url = ART.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""), gran="daily",
                         start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d")).replace("/all-access/user/", f"/{access}/user/")
        try:
            resp = client.get(url)
        except (FetchError, RobotsDisallowed):
            return None
        if resp.status_code == 404:
            out.append([0] * days)
            continue
        if resp.status_code != 200:
            return None
        got = dict(parse_series(resp.json()))
        if got and max(got) < end:
            return None
        out.append([int(got.get(start + timedelta(days=i), 0)) for i in range(days)])
    return out[0], out[1]

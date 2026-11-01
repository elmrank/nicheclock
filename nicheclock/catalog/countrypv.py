"""Wikimedia differentially-private pageviews per country (CC0), keyed by Wikidata QID.

One TSV per day (country, ISO2, project, page_id, title, QID, noisy count; no header). Only pages above a
release threshold (90 / 550 / 1000 views by country risk class) are published, and counts carry
Gaussian noise. So:

* a day on which a page is missing is *below the threshold*, not zero: its value in a series is that day's
  smallest published count for the country (an upper bound), and the series is marked ``censored``;
* a ratio against such a baseline is a lower bound;
* some countries are not published at all (China, Russia) or have a handful of rows (Ethiopia).
"""

from __future__ import annotations

import gzip
import json
import math
import re
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "wikimedia_country"
LICENSE = "CC0 (Wikimedia Analytics datasets)"
URL = "https://analytics.wikimedia.org/published/datasets/country_project_page/{day}.tsv"
README = "https://analytics.wikimedia.org/published/datasets/country_project_page/00_README.html"
UNIT = "pageviews/day (first 10 unique per device, differentially private)"
MIN_ROWS = 1000                 # fewer published rows on the last day: the country is "limited"


def parse_day(raw: bytes, iso2s: Iterable[str]) -> dict[str, dict[str, Any]]:
    """{ISO2: {"rows": n, "floor": smallest published count, "pages": {qid: [views, title, project]}}}.

    Rows without a QID are counted in ``rows`` but not kept; a QID's views are summed over projects and
    its title/project are those of its biggest row."""
    want = set(iso2s)
    out: dict[str, dict[str, Any]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        f = line.split("\t")
        if len(f) < 7 or f[1] not in want or not f[2].endswith(".wikipedia"):
            continue
        try:
            views = float(f[6])
        except ValueError:
            continue
        c = out.setdefault(f[1], {"rows": 0, "floor": views, "pages": {}})
        c["rows"] += 1
        c["floor"] = min(c["floor"], views)
        qid = f[5]
        if not qid.startswith("Q"):
            continue
        cur = c["pages"].get(qid)
        if cur is None:
            c["pages"][qid] = [views, f[4].replace("_", " "), f[2], views]
        else:
            cur[0] += views
            if views > cur[3]:
                cur[1], cur[2], cur[3] = f[4].replace("_", " "), f[2], views
    for c in out.values():
        c["pages"] = {q: [round(v[0], 1), v[1], v[2]] for q, v in c["pages"].items()}
    return out


def _path(state_dir: Path, day: date) -> Path:
    return state_dir / "countrypv" / f"{day.isoformat()}.json.gz"


def load_day(state_dir: Path, day: date) -> dict[str, Any] | None:
    p = _path(state_dir, day)
    return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


def fetch_day(client: Any, state_dir: Path, day: date, iso2s: Iterable[str]) -> dict[str, Any] | None:
    cached = load_day(state_dir, day)
    if cached is not None:
        return cached
    try:
        resp = client.get(URL.format(day=day.isoformat()), timeout=180)
    except (FetchError, RobotsDisallowed):
        return None
    if resp.status_code != 200:
        return None
    data = parse_day(resp.content, iso2s)
    client.forget()
    p = _path(state_dir, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8")))
    return data


def gather(client: Any, state_dir: Path, end: date, iso2s: Iterable[str], days: int = 22) -> dict[date, dict[str, Any]]:
    iso2s = list(iso2s)
    out = {}
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        data = fetch_day(client, state_dir, d, iso2s)
        if data is not None:
            out[d] = data
    return out


def latest_day(client: Any) -> date | None:
    """Newest day with a file, read from the directory index."""
    try:
        text = client.get_text("https://analytics.wikimedia.org/published/datasets/country_project_page/")
    except (FetchError, RobotsDisallowed):
        return None
    days = sorted(set(re.findall(r'href="(20\d\d-\d\d-\d\d)\.tsv"', text)))
    return date.fromisoformat(days[-1]) if days else None


def series(samples: dict[date, dict[str, Any]], end: date, iso2: str, qid: str, days: int = 22) -> tuple[list[float], bool] | None:
    """(daily values oldest first, censored) for one page in one country; None when ``end`` has no file or
    the page is not published on ``end``. Days the page is missing take that day's floor (censored=True)."""
    last = samples.get(end, {}).get(iso2)
    if not last or qid not in last["pages"]:
        return None
    vals: list[float] = []
    censored = False
    for i in range(days - 1, -1, -1):
        d = samples.get(end - timedelta(days=i))
        if d is None or iso2 not in d:
            vals = []                       # a missing file breaks the series: keep only what follows
            continue
        page = d[iso2]["pages"].get(qid)
        if page is None:
            censored = True
            vals.append(float(d[iso2]["floor"]))
        else:
            vals.append(float(page[0]))
    return vals, censored


def candidates(samples: dict[date, dict[str, Any]], end: date, iso2: str, top: int = 400, min_ratio: float = 1.5) -> list[str]:
    """QIDs published on ``end`` whose last day is at least ``min_ratio`` x the median of the 14 days before
    (floor-substituted), ranked by ratio x log10(views)."""
    last = samples.get(end, {}).get(iso2)
    if not last:
        return []
    prev = [samples[d] for d in sorted(samples) if d < end and iso2 in samples[d]][-14:]
    if len(prev) < 7:
        return []
    ranked = []
    for q, (v, _t, _p) in last["pages"].items():
        if v < 2 * last["floor"]:
            continue
        base = statistics.median([(d[iso2]["pages"].get(q) or [d[iso2]["floor"]])[0] for d in prev])
        ratio = v / max(base, 1.0)
        if ratio >= min_ratio:
            ranked.append((ratio * math.log10(max(v, 10)), q))
    ranked.sort(reverse=True)
    return [q for _s, q in ranked[:top]]


def pool(samples: dict[date, dict[str, Any]], end: date, iso2: str, by_volume: int = 1200, risers: int = 500,
         min_ratio: float = 1.2) -> list[str]:
    """QIDs worth a row for a country: the biggest ``by_volume`` pages on ``end`` plus the best ``risers``
    (ratio >= ``min_ratio``). Series for all of them are already in memory."""
    last = samples.get(end, {}).get(iso2)
    if not last:
        return []
    big = sorted(last["pages"], key=lambda q: -last["pages"][q][0])[:by_volume]
    return list(dict.fromkeys(big + candidates(samples, end, iso2, top=risers, min_ratio=min_ratio)))

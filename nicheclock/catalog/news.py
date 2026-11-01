"""GDELT 15-minute GKG files -> daily article counts per named person/organisation.

File-based on purpose: the DOC API answers 429 for shared IPs, the files (data.gdeltproject.org) do not.
Each day is sampled with ``SLOTS_PER_DAY`` files (every 4 hours, on the hour) and the count is scaled to
a full day (96 files), so the unit is "articles/day (estimated)", the same for every day of a series.
Terms: GDELT is free for any use, with a citation to the GDELT Project (https://www.gdeltproject.org/).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import statistics
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "gdelt"
ATTRIBUTION = "GDELT Project (https://www.gdeltproject.org/): free use with citation"
FILE_URL = "https://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"
SLOT_HOURS = (0, 4, 8, 12, 16, 20)
FILES_PER_FULL_DAY = 96
UNIT = "articles/day (estimated from sampled 15-min GDELT files)"
UNIT_COUNTRY = "articles/day (estimated; articles mentioning a place in the country)"
V2_LOCATIONS, V2_PERSONS, V2_ORGS, URL_COL, EXTRAS_COL = 10, 12, 14, 4, 26
TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
MIN_DAY_COUNT = 2            # names seen fewer times in a day's sample are not stored
MAX_EXAMPLES = 4000
csv.field_size_limit(10 ** 9)


def parse_gkg(raw: bytes, fips: dict[str, str] | None = None) -> tuple[dict[str, Counter], dict[str, dict[str, list[str]]], int]:
    """({region: articles per name}, {region: one example [title, url] per name}, records) for one file.

    ``WW`` counts every article. ``fips`` maps a GDELT (FIPS) country code to a region id: an article
    also counts for a region when its location list names a place in that country. A name counts once
    per article; persons and organisations are counted together; short tokens and names with digits
    are dropped."""
    fips = fips or {}
    counts: dict[str, Counter] = {"WW": Counter()}
    example: dict[str, dict[str, list[str]]] = {"WW": {}}
    n = 0
    text = raw.decode("utf-8", errors="replace")
    for row in csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(row) <= V2_ORGS:
            continue
        n += 1
        names = set()
        for col in (V2_PERSONS, V2_ORGS):
            for item in row[col].split(";"):
                name = item.rsplit(",", 1)[0].strip()
                if len(name) >= 4 and not any(ch.isdigit() for ch in name):
                    names.add(name)
        if not names:
            continue
        regions = ["WW"]
        if fips and len(row) > V2_LOCATIONS:
            seen = set()
            for loc in row[V2_LOCATIONS].split(";"):
                parts = loc.split("#")
                if len(parts) > 2 and parts[2] in fips and fips[parts[2]] not in seen:
                    seen.add(fips[parts[2]])
                    regions.append(fips[parts[2]])
        title = ""
        if len(row) > EXTRAS_COL:
            m = TITLE.search(row[EXTRAS_COL])
            title = (m.group(1).strip() if m else "")[:200]
        for r in regions:
            c = counts.setdefault(r, Counter())
            ex = example.setdefault(r, {})
            for name in names:
                c[name] += 1
                if name not in ex and row[URL_COL].startswith("http"):
                    ex[name] = [title, row[URL_COL]]
    return counts, example, n


def slot_stamps(day: date) -> list[str]:
    return [f"{day:%Y%m%d}{h:02d}0000" for h in SLOT_HOURS]


def _day_path(state_dir: Path, day: date) -> Path:
    return state_dir / "gdelt2" / f"{day.isoformat()}.json.gz"


def load_day(state_dir: Path, day: date) -> dict[str, Any] | None:
    p = _day_path(state_dir, day)
    if not p.exists():
        return None
    return json.loads(gzip.decompress(p.read_bytes()))


def _save_day(state_dir: Path, day: date, data: dict[str, Any]) -> None:
    p = _day_path(state_dir, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False).encode("utf-8")))


def fetch_day(client: Any, state_dir: Path, day: date, fips: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Sample one day (cached in the state dir). None when no file could be fetched.

    Stored: ``counts`` {region: {name: articles in the sample}} (names seen at least MIN_DAY_COUNT times),
    ``examples`` {region: {name: [title, url]}} for the most-mentioned names, ``scale`` to a full day."""
    cached = load_day(state_dir, day)
    if cached is not None:
        return cached
    counts: dict[str, Counter] = {}
    examples: dict[str, dict[str, list[str]]] = {}
    files = 0
    for stamp in slot_stamps(day):
        try:
            resp = client.get(FILE_URL.format(stamp=stamp))
        except (FetchError, RobotsDisallowed):
            continue
        if resp.status_code != 200:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                raw = z.read(z.namelist()[0])
        except (zipfile.BadZipFile, IndexError):
            continue
        finally:
            client.forget()
        c, ex, _n = parse_gkg(raw, fips)
        for r, cnt in c.items():
            counts.setdefault(r, Counter()).update(cnt)
        for r, d in ex.items():
            for k, v in d.items():
                examples.setdefault(r, {}).setdefault(k, v)
        files += 1
    if files == 0:
        return None
    data: dict[str, Any] = {"day": day.isoformat(), "files": files, "scale": FILES_PER_FULL_DAY / files,
                            "counts": {}, "examples": {}}
    for r, cnt in counts.items():
        data["counts"][r] = {k: v for k, v in cnt.items() if v >= MIN_DAY_COUNT}
        top = dict(cnt.most_common(MAX_EXAMPLES))
        data["examples"][r] = {k: examples[r][k] for k in top if k in examples.get(r, {})}
    _save_day(state_dir, day, data)
    return data


def gather(client: Any, state_dir: Path, end: date, days: int = 17, fips: dict[str, str] | None = None) -> dict[date, dict[str, Any]]:
    """Daily samples for the ``days`` days ending on ``end`` (days that fail are absent)."""
    out = {}
    for i in range(days - 1, -1, -1):
        d = end - timedelta(days=i)
        data = fetch_day(client, state_dir, d, fips)
        if data:
            out[d] = data
    return out


def estimated(day: dict[str, Any] | None, name: str, region: str = "WW") -> float:
    return (day["counts"].get(region, {}).get(name, 0) * day["scale"]) if day else 0.0


def series_for(names: Iterable[str], samples: dict[date, dict[str, Any]], end: date, days: int,
               region: str = "WW") -> list[float] | None:
    """Estimated articles/day (names summed) for up to ``days`` days ending on ``end``, oldest first.

    Stops at the first day without a sample, so the series is always contiguous. None when ``end``
    itself has no sample."""
    if end not in samples:
        return None
    names = list(names)
    out: list[float] = []
    for i in range(days):
        d = samples.get(end - timedelta(days=i))
        if d is None:
            break
        out.append(round(sum(estimated(d, n, region) for n in names), 1))
    out.reverse()
    return out


def candidate_names(samples: dict[date, dict[str, Any]], end: date, top_count: int = 80, top_ratio: int = 80,
                    min_count: int = 8, min_ratio_count: int = 6, region: str = "WW", min_share: float = 0.25) -> list[str]:
    """Names worth resolving: the most-mentioned on ``end`` plus the biggest risers against the median of
    the earlier days of the sample (raw sample counts, floors keep one-off blips out).

    For a country the counters hold articles that merely mention a place in it, so a US politician or a wire agency
    that is in the news everywhere also shows up there. A name is a local signal only when at least ``min_share`` of
    its worldwide articles of the day mention a place in the country."""
    last = samples.get(end)
    if not last or region not in last["counts"]:
        return []
    lc = last["counts"][region]
    if region != "WW":
        ww = last["counts"].get("WW", {})
        lc = {n: c for n, c in lc.items() if c >= min_share * ww.get(n, c)}
    names = [n for n, c in lc.items() if c >= min(min_count, min_ratio_count)]
    by_count = sorted((n for n in names if lc[n] >= min_count), key=lambda n: -lc[n])[:top_count]
    prev = [samples[d] for d in sorted(samples) if d < end]
    risers = []
    if len(prev) >= 7:
        for n in names:
            c = lc[n]
            if c < min_ratio_count:
                continue
            base = statistics.median([p["counts"].get(region, {}).get(n, 0) * p["scale"] / last["scale"] for p in prev[-14:]])
            risers.append((c / max(base, 1.0), n))
        risers.sort(reverse=True)
    return list(dict.fromkeys(by_count + [n for _r, n in risers[:top_ratio]]))

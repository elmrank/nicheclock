"""The niche pool: which pages a profile can match, and the Wikidata handles each one carries.

The catalog's own lists are the top of Wikipedia plus the best risers, so a niche of ten pages never reaches them. The pool
is wider: for a country, every page of the Wikimedia per-country pageview file that is *rising* (at least 120 views on the
last day and 1.25x its own median of the 14 days before) or *steady* (at least 200 views). Each pool page gets up to a handful
of **handles**, small integers that say how Wikidata ties the page to the things a profile can name:

    handle = qid * 64 + kind * 16 + sub

======  =================  ===============================================================================
kind    meaning            sub
======  =================  ===============================================================================
0       class              distance 1..3 (P31 first, then P279 upwards)
1       occupation         distance 0..2 (P106, then P279 upwards)
2       subject            property code: P641 0, P921 1, P361 2, P179 3, P8345 4, P136 5, P800 6
3       taxon              distance 1..12 (P171 chain)
======  =================  ===============================================================================

The browser (and later the Worker) matches a profile against rows and handles with ``match.js``; nothing here knows a profile.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from nicheclock.catalog import junk
from nicheclock.catalog.wdbulk import MISSING, BulkFetcher, EntityStore

REGIONS = ("US", "IN", "BR", "ID")
# The three sample profiles of the "Your niche" page (chess; birdwatching + bird; sourdough + sourdough bread + bread): always typeable.
SAMPLE_QIDS = ("Q718", "Q103632", "Q685629", "Q5113", "Q904889", "Q3360560", "Q7802")
RISING_VIEWS, RISING_RATIO, STEADY_VIEWS = 120, 1.25, 200
RATIO_CAP = 10.0
GENERIC_SHARE = 0.03          # a class on 3% or more of the pool carries no signal
GENERIC_TAXON_SHARE = 0.10    # kingdoms and phyla of the whole tree; a class like birds (1%) is the signal for a bird profile
CLASS_DEPTH, OCC_DEPTH, TAXON_DEPTH = 3, 2, 12
BUDGET_GZ = {"US": 2_500_000}
BUDGET_DEFAULT_GZ = 500_000
SERIES_DAYS = 7

KIND_CLASS, KIND_OCC, KIND_SUBJECT, KIND_TAXON = 0, 1, 2, 3
SUBJECT_PROPS = ("P641", "P921", "P361", "P179", "P8345", "P136", "P800")
FLAG_PERSON, FLAG_FICTIONAL, FLAG_CLADE, FLAG_RISING = 1, 2, 4, 8       # bits 4..7: number of occupations (at most 15)
HUMAN = "Q5"
# Taxon ranks (P105 values). Species, subspecies and genus pages are the content of a hobby; ranks above are "clade pages".
LOW_RANKS = frozenset({"Q7432", "Q68947", "Q767728", "Q34740"})
# Which parents become handles, in order of preference: class, order, family, genus, then the broad ranks when none of those exist.
TAXON_PREFERENCE = ("Q37517", "Q36602", "Q35409", "Q34740", "Q38348", "Q334460", "Q36732")
TAXON_HANDLES = 3
# a class whose name starts like this puts the page in the fictional / legendary guard (the class chain is read, no list of items)
FICTIONAL_PREFIXES = ("fictional", "fictitious", "legendary", "mythical", "mythological")
MAX_PER_KIND = {KIND_SUBJECT: 3, KIND_OCC: 3, KIND_CLASS: 3}


def encode(qid: str, kind: int, sub: int) -> int:
    return int(qid[1:]) * 64 + kind * 16 + sub


def decode(h: int) -> tuple[str, int, int]:
    return "Q" + str(h // 64), (h // 16) % 4, h % 16


@dataclass
class Walk:
    """What the statements of one page say, before any size limit is applied."""
    qid: str
    label: str
    description: str
    classes: dict[str, int] = field(default_factory=dict)         # target -> shortest distance
    occupations: dict[str, int] = field(default_factory=dict)
    subjects: list[tuple[str, int]] = field(default_factory=list)  # (target, property code)
    taxa: list[tuple[str, int, str]] = field(default_factory=list)  # (target, distance, rank qid or "")
    person: bool = False
    nocc: int = 0
    fictional: bool = False
    is_taxon: bool = False
    self_rank: str = ""

    def targets(self) -> set[str]:
        return set(self.classes) | set(self.occupations) | {t for t, _ in self.subjects} | {t for t, _d, _r in self.taxa}


def walk(store: EntityStore, qid: str, missing: set[str]) -> Walk | None:
    """Read one page's handles from the store. Ids the walk needs and the store lacks go into ``missing``; the walk is
    then incomplete and is repeated after they are fetched. None: the entity does not exist or has no label."""
    rec = store.get(qid)
    if rec is MISSING:
        missing.add(qid)
        return None
    if rec is None or not rec["l"]:
        return None
    c = rec["c"]
    w = Walk(qid, rec["l"], rec["d"])
    w.person = HUMAN in c.get("P31", [])
    w.nocc = len(c.get("P106", []))
    # classes: P31 and P279 of the page are distance 1, then P279 upwards
    frontier = list(dict.fromkeys(c.get("P31", []) + c.get("P279", [])))
    for d in range(1, CLASS_DEPTH + 1):
        nxt: list[str] = []
        for t in frontier:
            if t in w.classes:
                continue
            w.classes[t] = d
            r = store.get(t)
            if r is MISSING:
                missing.add(t)
                continue
            if r is None:
                continue
            if r["l"].lower().startswith(FICTIONAL_PREFIXES):
                w.fictional = True
            if d < CLASS_DEPTH:
                nxt.extend(r["c"].get("P279", []))
        frontier = nxt
    # occupations: P106 values (distance 0), then P279 upwards
    frontier = list(dict.fromkeys(c.get("P106", [])))
    for d in range(0, OCC_DEPTH + 1):
        nxt = []
        for t in frontier:
            if t in w.occupations:
                continue
            w.occupations[t] = d
            r = store.get(t)
            if r is MISSING:
                missing.add(t)
                continue
            if r is not None and d < OCC_DEPTH:
                nxt.extend(r["c"].get("P279", []))
        frontier = nxt
    # subjects: one handle per target, the first property that names it
    seen: set[str] = set()
    for code, prop in enumerate(SUBJECT_PROPS):
        for t in c.get(prop, []):
            if t not in seen:
                seen.add(t)
                w.subjects.append((t, code))
                if store.get(t) is MISSING:
                    missing.add(t)
    # taxon: the P171 chain, with each parent's rank
    w.is_taxon = "P171" in c or "P105" in c
    w.self_rank = (c.get("P105") or [""])[0]
    cur, hops, chain_seen = (c.get("P171") or [None])[0], 1, set()
    while cur and hops <= TAXON_DEPTH and cur not in chain_seen:
        chain_seen.add(cur)
        r = store.get(cur)
        if r is MISSING:
            missing.add(cur)
            break
        if r is None:
            break
        w.taxa.append((cur, hops, (r["c"].get("P105") or [""])[0]))
        cur, hops = (r["c"].get("P171") or [None])[0], hops + 1
    return w


def flags_of(w: Walk, rising: bool) -> int:
    f = (FLAG_PERSON if w.person else 0) | (FLAG_FICTIONAL if w.fictional else 0) | (FLAG_RISING if rising else 0)
    if w.is_taxon and w.self_rank not in LOW_RANKS:
        f |= FLAG_CLADE
    return f | (min(w.nocc, 15) << 4)


def select_handles(w: Walk, df: Counter, n: int) -> list[int]:
    """The handles one row carries: subjects, then occupations and classes, then taxon parents. A class on 3% or more of
    the pool is dropped; within a kind the rarer target (smaller ``df``) comes first, because it says more."""
    def share(t: str) -> float:
        return df[t] / max(n, 1)

    out: list[int] = []
    subj = sorted(w.subjects, key=lambda x: (df[x[0]], x[1]))[:MAX_PER_KIND[KIND_SUBJECT]]
    out += [encode(t, KIND_SUBJECT, code) for t, code in subj]
    occ = sorted(w.occupations.items(), key=lambda x: (df[x[0]], x[1]))[:MAX_PER_KIND[KIND_OCC]]
    out += [encode(t, KIND_OCC, d) for t, d in occ]
    cls = sorted(((t, d) for t, d in w.classes.items() if share(t) < GENERIC_SHARE), key=lambda x: (x[1], df[x[0]]))[:MAX_PER_KIND[KIND_CLASS]]
    out += [encode(t, KIND_CLASS, d) for t, d in cls]
    by_rank: dict[str, tuple[str, int]] = {}
    for t, d, rank in w.taxa:
        if rank in TAXON_PREFERENCE and rank not in by_rank and share(t) < GENERIC_TAXON_SHARE:
            by_rank[rank] = (t, d)
    picked = [by_rank[r] for r in TAXON_PREFERENCE if r in by_rank][:TAXON_HANDLES]
    out += [encode(t, KIND_TAXON, d) for t, d in picked]
    return list(dict.fromkeys(out))


# -- choosing pages ------------------------------------------------------------------------------

def select_pages(samples: dict[date, dict[str, Any]], end: date, iso2: str, extra: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Pool pages of one country on ``end``: rising, steady, and ``extra`` (the catalog's own topics) when published.

    Each is ``{qid, title, views, ratio, rising, series}``; ``ratio`` is against the median of the 14 days before (a day on
    which the page is not published takes that day's floor, an upper bound, so the ratio is a lower bound), capped at 10.
    """
    last = samples.get(end, {}).get(iso2)
    if not last:
        return []
    prev = [samples[d] for d in sorted(samples) if d < end and iso2 in samples[d]][-14:]
    if len(prev) < 7:
        return []
    days = [samples[d] for d in sorted(samples) if d <= end and iso2 in samples[d]][-SERIES_DAYS:]
    want = set(extra)
    out = []
    for q, (v, title, _proj) in last["pages"].items():
        if v < RISING_VIEWS and q not in want:
            continue
        base = statistics.median([(d[iso2]["pages"].get(q) or [d[iso2]["floor"]])[0] for d in prev])
        ratio = v / max(base, 1.0)
        rising = v >= RISING_VIEWS and ratio >= RISING_RATIO
        if not (rising or v >= STEADY_VIEWS or q in want):
            continue
        series = [round((d[iso2]["pages"].get(q) or [d[iso2]["floor"]])[0]) for d in days] if rising else None
        out.append({"qid": q, "title": title, "views": round(v), "ratio": round(min(ratio, RATIO_CAP), 2), "rising": rising, "series": series,
                    "keep": q in want})
    out.sort(key=lambda r: -r["views"])
    return out


# -- building one region ------------------------------------------------------------------------------

@dataclass
class Region:
    iso2: str
    pages: list[dict[str, Any]]
    walks: dict[str, Walk] = field(default_factory=dict)
    dropped: Counter = field(default_factory=Counter)


def resolve(store: EntityStore, fetcher: BulkFetcher, qids: Iterable[str], log: Callable[[str], None] = print,
            max_rounds: int = 16) -> dict[str, Walk]:
    """Walk every page, fetching what the walks miss, round after round, until nothing is missing (or a round fetches nothing)."""
    pending = list(dict.fromkeys(qids))
    walks: dict[str, Walk] = {}
    for rnd in range(max_rounds):
        missing: set[str] = set()
        again: list[str] = []
        for q in pending:
            m: set[str] = set()
            w = walk(store, q, m)
            if w is not None:
                walks[q] = w
            if m:
                again.append(q)
                missing |= m
        if not missing:
            break
        log(f"round {rnd}: {len(pending)} pages walked, {len(missing)} entities to read")
        got = fetcher.fetch(missing)
        still = [q for q in missing if store.get(q) is MISSING]
        if got == 0 or len(still) == len(missing):
            log(f"  nothing more could be read ({len(still)} entities stay unread)")
            break
        pending = again
    return walks


def apply_junk(w: Walk, title: str) -> str | None:
    """Why a page is not published in the pool (adult, utility page), or None."""
    return junk.adult_reason(w.label, w.description, sorted(w.targets())) or junk.utility_reason(w.label, [title, w.label])


def region_rows(region: Region, steady_floor: int = STEADY_VIEWS) -> tuple[list[list[Any]], dict[str, Any]]:
    """Rows of the pool file for one region, and the stats the manifest records."""
    kept = []
    dropped: Counter = Counter()
    for p in region.pages:
        w = region.walks.get(p["qid"])
        if w is None:
            dropped["no entity"] += 1
            continue
        if not p["rising"] and p["views"] < steady_floor and not p.get("keep"):
            continue
        why = apply_junk(w, p["title"])
        if why:
            dropped[why] += 1
            continue
        kept.append((p, w))
    n = len(kept)
    df: Counter = Counter()
    for _p, w in kept:
        for t in w.targets():
            df[t] += 1
    rows = []
    for p, w in kept:
        h = select_handles(w, df, n)
        title = w.label if w.label else p["title"]
        rows.append([int(p["qid"][1:]), title, p["views"], p["ratio"], h, p["series"] or 0, flags_of(w, p["rising"])])
    stats = {"rows": n, "rising": sum(1 for p, _w in kept if p["rising"]), "steady": sum(1 for p, _w in kept if not p["rising"]),
             "with_handles": sum(1 for r in rows if r[4]), "dropped": dict(dropped), "steady_floor": steady_floor}
    return rows, stats


def encode_json(doc: Any) -> bytes:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def gz_size(data: bytes) -> int:
    return len(gzip.compress(data, 9))


def fit_budget(region: Region, log: Callable[[str], None] = print) -> tuple[list[list[Any]], dict[str, Any], int]:
    """Rows within the gzip budget of the region: the steady floor rises by a quarter until the file fits."""
    budget = BUDGET_GZ.get(region.iso2, BUDGET_DEFAULT_GZ)
    floor = STEADY_VIEWS
    while True:
        rows, stats = region_rows(region, floor)
        size = gz_size(encode_json(rows))
        if size <= budget or floor > 5000:
            stats["gzip_bytes"] = size
            stats["budget_gzip_bytes"] = budget
            if floor != STEADY_VIEWS:
                stats["note"] = f"steady floor raised from {STEADY_VIEWS} to {floor} views/day to stay within {budget} bytes gzip"
                log(f"  {region.iso2}: {stats['note']}")
            return rows, stats, size
        floor = math.ceil(floor * 1.25)

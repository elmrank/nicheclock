"""Category-seeded pool: the best-known instances of each category's seed classes, read from Wikidata."""

from __future__ import annotations

import statistics
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from nicheclock.catalog.categories import category_ids
from nicheclock.catalog.wd import Wikidata
from nicheclock.http import FetchError, RobotsDisallowed

DATA = Path(__file__).parent / "data"
MIN_CATEGORY_FLOOR = 150.0       # views/day: below this a page is noise in any edition


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return yaml.safe_load((DATA / "seed_classes.yaml").read_text(encoding="utf-8"))


def check_seed_symmetry(cfg: dict[str, Any] | None = None) -> list[str]:
    """Problems with the hand-written seed classes (empty = fine): every category except "other" has between
    min_classes and max_classes of them, none is shared across categories, ids look like QIDs."""
    cfg = cfg or load()
    problems = []
    ids = set(category_ids()) - {"other"}
    for cat in sorted(ids - set(cfg["classes"])):
        problems.append(f"seed classes: {cat} has none")
    seen: dict[str, str] = {}
    for cat, qs in cfg["classes"].items():
        if cat not in ids:
            problems.append(f"seed classes: unknown category {cat!r}")
        if not cfg["min_classes"] <= len(qs) <= cfg["max_classes"]:
            problems.append(f"seed classes: {cat} has {len(qs)} (needs {cfg['min_classes']}-{cfg['max_classes']})")
        for q in qs:
            if not (isinstance(q, str) and q[:1] == "Q" and q[1:].isdigit()):
                problems.append(f"seed classes: {cat}: bad id {q!r}")
            if q in seen and seen[q] != cat:
                problems.append(f"seed classes: {q} is in both {seen[q]} and {cat}")
            seen.setdefault(q, cat)
    return problems


def _query(cls: str, limit: int, min_sl: int) -> str:
    return (f"SELECT ?item ?sl WHERE {{ ?item wdt:P31 wd:{cls} ; wikibase:sitelinks ?sl . "
            f"FILTER(?sl >= {min_sl}) }} ORDER BY DESC(?sl) LIMIT {limit}")


def members(wd: Wikidata, log: Any = print, cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """{qid: {"category", "class", "sitelinks"}}: per category the ``per_category_pool`` most-linked instances of
    its seed classes. A class whose query fails is skipped and reported."""
    cfg = cfg or load()
    out: dict[str, dict[str, Any]] = {}
    for cat, classes in cfg["classes"].items():
        rows: dict[str, tuple[int, str]] = {}
        ok = 0
        for cls in classes:
            try:
                for r in wd.sparql(_query(cls, cfg["per_class_limit"], cfg["min_sitelinks"])):
                    q = r["item"].rsplit("/", 1)[-1]
                    sl = int(float(r["sl"]))
                    if q not in rows or rows[q][0] < sl:
                        rows[q] = (sl, cls)
                ok += 1
            except (FetchError, RobotsDisallowed, KeyError, ValueError) as e:
                log(f"  seeds {cat} {cls}: {type(e).__name__}: {str(e)[:80]}")
        best = sorted(rows.items(), key=lambda kv: -kv[1][0])[:cfg["per_category_pool"]]
        for q, (sl, cls) in best:
            if q not in out:                       # first category wins when an item sits in two
                out[q] = {"category": cat, "class": cls, "sitelinks": sl}
        log(f"  seeds {cat}: {ok}/{len(classes)} classes, {len(best)} members")
    return out


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (p in 0..100) of a non-empty list."""
    v = sorted(values)
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def category_floors(volumes: dict[str, list[float]], cap: float, p: float = 75.0, minimum: float = MIN_CATEGORY_FLOOR,
                    min_members: int = 20) -> dict[str, float]:
    """{category: floor}: the category's own p75 of last-days volume, clamped to [minimum, cap]. A category with
    fewer than ``min_members`` measured members keeps the plain minimum (a p75 of 5 pages means nothing)."""
    out = {}
    for cat, vals in volumes.items():
        out[cat] = minimum if len(vals) < min_members else max(minimum, min(cap, percentile(vals, p)))
    return out

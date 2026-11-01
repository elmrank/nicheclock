"""Level-1 categories (Google Trends' list) and the Wikidata class rules that place a topic in one.

``data/categories.json`` is the category list; ``data/category_map.yaml`` the documented mapping.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from nicheclock.catalog.wd import Wikidata

DATA = Path(__file__).parent / "data"
OTHER = "other"
HUMAN = "Q5"
TAXON = "Q16521"


@dataclass(frozen=True)
class Classification:
    category: str
    kind: str = "fallback"        # property | class | occupation | taxon | fallback
    via: str = ""                 # the class QID (or property) that decided it
    via_label: str = ""
    depth: int = -1               # steps from the entity's own class to the rule's class

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def load_categories() -> dict[str, Any]:
    return json.loads((DATA / "categories.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_map() -> dict[str, Any]:
    return yaml.safe_load((DATA / "category_map.yaml").read_text(encoding="utf-8"))


def category_ids() -> list[str]:
    return [c["id"] for c in load_categories()["categories"]]


def category_label(cid: str) -> str:
    for c in load_categories()["categories"]:
        if c["id"] == cid:
            return c["label"]
    return cid


def _index(rules: dict[str, list[dict[str, Any]]], order: list[str]) -> dict[str, tuple[str, int, int]]:
    """{class QID: (category, priority, max_depth)}; priority = position of the category in ``order``."""
    out: dict[str, tuple[str, int, int]] = {}
    for cat, items in rules.items():
        for it in items:
            out.setdefault(it["class"], (cat, order.index(cat), int(it.get("max_depth", 99))))
    return out


def check_symmetry(cmap: dict[str, Any] | None = None) -> list[str]:
    """Problems with the hand-written rules (empty = fine).

    The only hand-written part of the catalog is this mapping, so this is where an uneven seed would
    show: every category must be a real level-1 category, have between ``min_rules`` and ``max_rules``
    rules, and none may have more than 3x the median. Per-category topic counts are NOT checked here:
    what the data does is organic and is shown as it is.
    """
    cmap = cmap or load_map()
    ids = set(category_ids())
    counts: dict[str, int] = {c: 0 for c in ids if c != OTHER}
    problems = []
    for section in ("class_rules", "occupation_rules"):
        for cat, items in cmap[section].items():
            if cat not in ids:
                problems.append(f"{section}: unknown category {cat!r}")
                continue
            counts[cat] = counts.get(cat, 0) + len(items)
    counts["pets-animals"] += 1          # the taxon rule
    for cat in set(cmap["agency_rules"].values()):
        if cat not in ids:
            problems.append(f"agency_rules: unknown category {cat!r}")
    if cmap["property_rules"]:
        for cat in cmap["property_rules"].values():
            if cat not in ids:
                problems.append(f"property_rules: unknown category {cat!r}")
        counts["sports"] += len(cmap["property_rules"])
    lo, hi = cmap["min_rules"], cmap["max_rules"]
    med = statistics.median(counts.values())
    for cat, n in sorted(counts.items()):
        if n < lo:
            problems.append(f"{cat}: {n} rules (< min_rules {lo})")
        if n > hi:
            problems.append(f"{cat}: {n} rules (> max_rules {hi})")
        if n > 3 * med:
            problems.append(f"{cat}: {n} rules is more than 3x the median ({med})")
    return problems


class Classifier:
    """``Classifier(wikidata).classify_many(qids) -> {qid: Classification}``."""

    def __init__(self, wd: Wikidata, cmap: dict[str, Any] | None = None) -> None:
        self.wd = wd
        self.map = cmap or load_map()
        order = list(self.map["priority"])
        self.classes = _index(self.map["class_rules"], order)
        self.occupations = _index(self.map["occupation_rules"], order)
        self.depth = int(self.map["max_depth"])
        self.taxon_depth = int(self.map["taxon_depth"])

    # -- ancestry ---------------------------------------------------------------------------
    def _prefetch(self, seeds: Iterable[str], prop: str, depth: int) -> None:
        """Fetch the records the upward walks below will read, level by level, in batches."""
        seen = set(seeds)
        frontier = set(seeds)
        for _ in range(depth):
            recs = self.wd.entities(frontier)
            nxt: set[str] = set()
            for q in frontier:
                for p in (recs.get(q) or {}).get("claims", {}).get(prop, []):
                    if p not in seen:
                        seen.add(p)
                        nxt.add(p)
            if not nxt:
                return
            frontier = nxt

    def _closure(self, seeds: Iterable[str], prop: str, depth: int) -> dict[str, int]:
        """{ancestor: steps} reachable from ``seeds`` (steps 0) through ``prop``."""
        out = {s: 0 for s in seeds}
        frontier = list(out)
        for d in range(1, depth + 1):
            recs = self.wd.entities(frontier)
            nxt = []
            for q in frontier:
                for p in (recs.get(q) or {}).get("claims", {}).get(prop, []):
                    if p not in out:
                        out[p] = d
                        nxt.append(p)
            if not nxt:
                break
            frontier = nxt
        return out

    def _label(self, qid: str) -> str:
        return (self.wd.entities([qid]).get(qid) or {}).get("label", "")

    def _best(self, hits: dict[str, int], index: dict[str, tuple[str, int, int]], kind: str) -> Classification | None:
        best = None
        for q, d in hits.items():
            rule = index.get(q)
            if rule is None or d > rule[2]:
                continue
            key = (d, rule[1])
            if best is None or key < best[0]:
                best = (key, q, rule[0], d)
        if best is None:
            return None
        return Classification(best[2], kind, best[1], self._label(best[1]), best[3])

    # -- classification ---------------------------------------------------------------------
    def classify_many(self, qids: Iterable[str]) -> dict[str, Classification]:
        qids = list(dict.fromkeys(qids))
        recs = self.wd.entities(qids)
        classes, occs, taxa = set(), set(), set()
        for q in qids:
            r = recs.get(q)
            if not r:
                continue
            c = r["claims"]
            if HUMAN in c.get("P31", []):
                occs.update(c.get("P106", []))
            elif TAXON in c.get("P31", []) or "P171" in c:
                taxa.update(c.get("P171", []))
            else:
                classes.update(c.get("P31", []) + c.get("P279", []))
        self._prefetch(classes, "P279", self.depth)
        self._prefetch(occs, "P279", self.depth)
        self._prefetch(taxa, "P171", self.taxon_depth)
        return {q: self._classify(recs.get(q)) for q in qids}

    def classify(self, qid: str) -> Classification:
        return self.classify_many([qid])[qid]

    def _classify(self, rec: dict[str, Any] | None) -> Classification:
        if not rec:
            return Classification(OTHER)
        c = rec["claims"]
        human = HUMAN in c.get("P31", [])
        if not human:
            for prop, cat in self.map["property_rules"].items():
                if prop in c:
                    # a film or a fictional character "about" a sport is still a film or a character
                    by_class = self._by_class(c)
                    if by_class and by_class.category in ("entertainment", "games") and by_class.depth <= 3:
                        return by_class
                    return Classification(cat, "property", prop, "sport" if prop == "P641" else prop, 0)
        if human:
            # each occupation votes for the category of its nearest rule; most votes win, then the
            # nearer rule, then the tie-break order (so an entrepreneur-and-politician is not "politics"
            # just because it is listed first)
            votes: dict[str, list[Classification]] = {}
            for prop, cat in self.map["property_rules"].items():       # a sport statement is one vote
                if prop in c:
                    votes.setdefault(cat, []).append(Classification(cat, "property", prop, "sport", 0))
            for occ in c.get("P106", []):
                best = self._best(self._closure([occ], "P279", self.depth), self.occupations, "occupation")
                if best:
                    votes.setdefault(best.category, []).append(best)
            if not votes:
                return Classification(OTHER)
            order = self.map["priority"]
            cat = min(votes, key=lambda k: (-len(votes[k]), min(v.depth for v in votes[k]), order.index(k)))
            return min(votes[cat], key=lambda v: v.depth)
        if TAXON in c.get("P31", []) or "P171" in c:
            up = self._closure(c.get("P171", []), "P171", self.taxon_depth)
            if self.map["taxon_animal_class"] in up:
                return Classification("pets-animals", "taxon", self.map["taxon_animal_class"], self._label(self.map["taxon_animal_class"]), up[self.map["taxon_animal_class"]])
            return Classification("science", "taxon", TAXON, self._label(TAXON), 0)
        return self._by_class(c) or Classification(OTHER)

    def _by_class(self, c: dict[str, list[str]]) -> Classification | None:
        # a class-like item (espresso, "coffee") has "subclass of" instead of "instance of"
        hits = self._closure(c.get("P31", []) + c.get("P279", []), "P279", self.depth)
        return self._best(hits, self.classes, "class")

    def classify_agency(self, slugs: Iterable[str]) -> str:
        for s in slugs:
            if s in self.map["agency_rules"]:
                return self.map["agency_rules"][s]
        return "law-government"

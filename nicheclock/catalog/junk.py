"""Junk and safety filter for the catalog: topics that must never be published as "trending".

Three kinds, each counted and exampled in ``meta.json`` (``filtered``):

* ``adult``    NSFW topics: Wikidata classes (``ADULT_CLASSES``, matched on the entity's instance-of/occupation and
               their subclass ladder), words in the Wikidata description (``ADULT_TEXT``), and a small explicit
               denylist (``ADULT_LABELS``: things like the ``.xxx`` domain that carry no class signal).
* ``utility``  Wikipedia meta and utility pages: Main Page, special/namespace pages, "List of ..." pages and the
               monthly/yearly "Deaths in <Month> <Year>" / "Deaths in <Year>" pages. Real titles that merely start
               with these words ("Deaths in Venice") stay.
* ``bot``      Traffic that is not people (the topviews heuristics, deliberately strict: mobile-heavy audiences are
               normal for celebrities): access skew of 98% or more over the last two days, or one day holding more than
               80% of the 7-day total together with a skew of 90% or more.

Everything is a pure function of its inputs so it is tested without the network.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

# Wikidata classes: pornography, pornographic film, film actor, film director, website, studio, magazine, hentai,
# erotic art, erotic entertainment, and the erotic and adult game classes. Sponsored TLD is NOT here: .xxx is on the label list.
ADULT_CLASSES = ("Q291", "Q185529", "Q488111", "Q17456089", "Q11074464", "Q30941437", "Q3244962", "Q172067", "Q1168168",
                 "Q106466629", "Q139987676", "Q3362070")
ADULT_TEXT = re.compile(r"pornograph|\bporn\b|adult (?:film|video|entertainment|website|model|content)|erotic|\bhentai\b|"
                        r"\bonlyfans\b|\bcam ?girl|sex tape|sexually explicit|\bnsfw\b", re.I)
ADULT_LABELS = frozenset({".xxx", ".adult", ".porn", ".sex", "xxx", "onlyfans", "pornhub", "xvideos", "xnxx", "xhamster", "redtube",
                          "youporn", "chaturbate", "brazzers"})

# Titles/labels (English edition forms) of pages that are not topics. Other editions are covered by Wikidata classes
# (list article, disambiguation, category, template, Wikimedia main page) in trending._drop_non_topics.
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
UTILITY_TITLE = re.compile(rf"^(?:deaths in (?:(?:{MONTHS}) )?\d{{4}}$|list of |lists of |portal:|category:|template:|help:|wikipedia:|"
                           r"special:|file:|main page$)", re.I)

SKEW_ALONE = 0.98               # access skew that marks bot-like traffic by itself (mobile-heavy 90% is normal for celebrities)
SKEW_WITH_SPIKE = 0.90          # lower skew bar when the traffic is also one day of the week
SPIKE_SHARE = 0.80              # one day above this share of the 7-day total is a single-day spike
SKEW_MIN_VIEWS = 200            # below this many views the share is too noisy to judge
SPIKE_FACTOR = 8.0              # single_day_spike(): a day this many times both neighbours


def adult_reason(label: str, description: str, class_ids: Sequence[str]) -> str | None:
    """Why a topic is adult, or None. ``class_ids``: the entity's instance-of / occupation / subclass ids."""
    if (label or "").strip().casefold() in ADULT_LABELS:
        return "label"
    if set(class_ids) & set(ADULT_CLASSES):
        return "class"
    if ADULT_TEXT.search(description or "") or ADULT_TEXT.search(label or ""):
        return "text"
    return None


def utility_reason(label: str, titles: Sequence[str]) -> str | None:
    for t in [label, *titles]:
        t = (t or "").replace("_", " ").strip()
        if t and UTILITY_TITLE.match(t):
            return "title"
    return None


def access_skew(desktop: Sequence[float], mobile: Sequence[float], days: int = 2) -> float | None:
    """Larger access type's share of the last ``days`` days of views (0.5-1.0); None when too few views to judge."""
    d, m = sum(desktop[-days:]), sum(mobile[-days:])
    if d + m < SKEW_MIN_VIEWS:
        return None
    return round(max(d, m) / (d + m), 3)


def single_day_spike(series: Sequence[float]) -> int | None:
    """Index of a day (never the last: its neighbour is not known yet) that is at least SPIKE_FACTOR x both
    neighbours and 5x the series median, else None."""
    if len(series) < 5:
        return None
    med = sorted(series)[len(series) // 2]
    for i in range(1, len(series) - 1):
        around = max(series[i - 1], series[i + 1], 1.0)
        if series[i] >= SPIKE_FACTOR * around and series[i] >= 5 * max(med, 1.0):
            return i
    return None


def week_peak_share(series: Sequence[float]) -> float:
    """Largest day's share of the last 7 days' total (1/7 = flat, 1.0 = everything on one day)."""
    tail = list(series[-7:])
    return max(tail) / sum(tail) if tail and sum(tail) > 0 else 0.0


def bot_reason(desktop: Sequence[float] | None, mobile: Sequence[float] | None, series: Sequence[float]) -> str | None:
    """Why traffic looks automated, or None. Needs the access split: without it nothing is flagged."""
    if desktop is None or mobile is None:
        return None
    s = access_skew(desktop, mobile)
    if s is None:
        return None
    if s >= SKEW_ALONE:
        return f"skew {s:.0%}"
    if s >= SKEW_WITH_SPIKE and week_peak_share(series) > SPIKE_SHARE:
        return f"single-day spike ({week_peak_share(series):.0%} of the week) with skew {s:.0%}"
    return None


class Tally:
    """Counts and a few examples per kind, for ``meta.json``."""

    def __init__(self) -> None:
        self.n: dict[str, int] = {"adult": 0, "utility": 0, "bot": 0}
        self.examples: dict[str, list[str]] = {"adult": [], "utility": [], "bot": []}

    def add(self, kind: str, label: str, why: str) -> None:
        self.n[kind] += 1
        if len(self.examples[kind]) < 5:
            self.examples[kind].append(f"{label} ({why})")

    def to_dict(self) -> dict[str, Any]:
        return {"removed": dict(self.n), "total": sum(self.n.values()), "examples": {k: list(v) for k, v in self.examples.items() if v}}

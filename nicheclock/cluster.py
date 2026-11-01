"""Signals -> topics: merge the same thing seen by several sources, then score it."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from nicheclock.generic import is_generic
from nicheclock.match import contains_phrase, normalize, tokens
from nicheclock.models import Signal, Topic, utcnow
from nicheclock.regions import GLOBAL, combine

# Source order when picking a topic label.
LABEL_PRIORITY = ("wikipedia", "google_trends", "apple", "rss", "hn", "github", "federal_register", "gdelt")
RANKED_LISTS = {"google_trends", "apple", "wikipedia", "rss"}   # rank = position in a broad list
HALF_LIFE_DAYS = 2.0


# Independent kinds of evidence: two sources of one family count once for the relevance floor.
FAMILY = {"google_trends": "trends", "apple": "trends", "wikipedia": "reference", "hn": "community",
          "github": "community", "gdelt": "news", "federal_register": "news", "rss": "feeds"}


def strong(s: Signal) -> bool:
    """A signal worth showing on one keyword hit alone: HN >= 100 points, Google Trends top 10, a
    Wikipedia riser of x2+ (3 days vs 14-day median, or week over week), a GDELT volume spike of
    x2+ vs the 4-week mean, or a Federal Register document whose title names a keyword."""
    x = s.extra
    if s.source == "hn":
        return (s.score or 0) >= 100
    if s.source == "google_trends":
        return bool(s.rank) and s.rank <= 10
    if s.source == "wikipedia":
        return (x.get("ratio") or 0) >= 2 or (x.get("wow") or 0) >= 2
    if s.source == "gdelt":
        return x.get("metric") == "news volume" and (x.get("ratio") is None or x["ratio"] >= 2)
    if s.source == "federal_register":
        return bool(x.get("title_match"))
    return False


ENTITY_SOURCES = {"wikipedia", "google_trends", "apple"}   # their terms are names, not headlines
PHRASE_SOURCES = {"hn", "gdelt", "rss"}     # headline-like titles: a specific multi-word keyword in one is enough


def phrase_hit(s: Signal) -> bool:
    """A title containing a keyword of >= 2 words that is not generic ("small teams", "free trial")."""
    return s.source in PHRASE_SOURCES and any(
        len(tokens(k)) >= 2 and not is_generic(k) for k in s.extra.get("keywords", []))


def signal_passes(s: Signal) -> bool:
    """Does this one signal clear the floor on its own?"""
    x = s.extra
    kws, ents = x.get("keywords", []), x.get("entities", [])
    if s.source == "federal_register":     # a specific entity alone is not enough here: needs a keyword in the title
        return bool(kws) and bool(x.get("title_match"))
    if ents:                               # a specific entity (context never counts alone)
        return True
    if not kws:
        return False
    if x.get("watch") and any(not is_generic(k) for k in kws):
        return True                        # a feed you asked to watch: your intent is the filter, but
                                           # a generic keyword ("free trial") in a watched feed is not a topic
    return strong(s) or phrase_hit(s)


def passes_floor(topic: Topic) -> bool:
    """Relevance floor. A topic passes when one of its signals does (a specific entity, or a keyword
    plus strength: see ``signal_passes``), or when it has >= 2 distinct hits (keywords, plus context
    terms seen in the same item as a keyword/entity), or 1 keyword seen by >= 2 source families."""
    if any(signal_passes(s) for s in topic.signals):
        return True
    kws = {k for s in topic.signals for k in s.extra.get("keywords", [])}
    ctx = {c for s in topic.signals if s.extra.get("keywords") for c in s.extra.get("context", [])}
    families = {FAMILY.get(s.source, s.source) for s in topic.signals if s.source != "federal_register" or s.extra.get("keywords")}
    return len(kws) + len(ctx) >= 2 or (len(kws) >= 1 and len(families) >= 2)


# -- clustering ------------------------------------------------------------------------------

class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, i: int) -> int:
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _similar(a: list[str], b: list[str]) -> bool:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    if len(sa & sb) / len(sa | sb) >= 0.6:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # one side is a whole phrase inside the other ("Hytale" in "Hytale is back" only when short)
    return len(short) >= 2 and contains_phrase(long_, short) or (
        len(short) == 1 and len(long_) <= 3 and short[0] in long_)


def cluster(signals: list[Signal], niche: Any, mode: str = "combined") -> list[Topic]:
    """Group signals into topics and tag each topic with its region.

    ``combined``: one pass over every signal, so the same story seen in several countries is one
    topic (region = that country, or 'multi'). ``per_country``: signals are first split by region
    (a country, or 'global' for sources without one) and each group is clustered on its own, so a
    topic belongs to exactly one region."""
    if mode == "per_country":
        parts: dict[str, list[Signal]] = {}
        for s in signals:
            parts.setdefault(s.region or GLOBAL, []).append(s)
        groups = [parts[r] for r in sorted(parts, key=lambda r: (r == GLOBAL, r))]
    else:
        groups = [signals]
    topics: list[Topic] = []
    for g in groups:
        for t in _cluster_group(g, niche):
            t.region, t.region_precision, t.regions = combine(t.signals)
            topics.append(t)
    return topics


def _cluster_group(signals: list[Signal], niche: Any) -> list[Topic]:
    """A signal containing one of the niche's entities joins that entity's topic; the rest merge by
    token overlap."""
    anchors = sorted(niche.entities, key=lambda e: -len(tokens(e)))
    anchor_toks = [(e, tokens(e)) for e in anchors]
    keyed: dict[str, list[Signal]] = {}
    labels: dict[str, str] = {}
    loose: list[Signal] = []
    for s in signals:
        toks = tokens(s.term)
        # Only signals that name the entity themselves anchor to it (an article, a trending
        # query, a short title). A headline that merely mentions "OpenAI" stays its own item.
        hit = next((e for e, et in anchor_toks
                    if et and contains_phrase(toks, et)
                    and (s.source in ENTITY_SOURCES or len(toks) <= len(et) + 1)), None)
        if hit:
            k = normalize(hit)
            keyed.setdefault(k, []).append(s)
            labels[k] = hit
        else:
            loose.append(s)

    uf = _UF(len(loose))
    toks_l = [tokens(s.term) for s in loose]
    for i in range(len(loose)):
        for j in range(i + 1, len(loose)):
            if _similar(toks_l[i], toks_l[j]):
                uf.union(i, j)
    groups: dict[int, list[Signal]] = {}
    for i, s in enumerate(loose):
        groups.setdefault(uf.find(i), []).append(s)

    # a lone signal keeps its own headline; several signals about one entity share the entity name
    topics = [Topic(key=k, label=v[0].term if len(v) == 1 else labels[k], signals=v, niche=niche.name)
              for k, v in keyed.items()]
    for g in groups.values():
        label = _pick_label(g)
        topics.append(Topic(key=normalize(label), label=label, signals=g, niche=niche.name))
    return topics


def _pick_label(group: list[Signal]) -> str:
    def prio(s: Signal) -> tuple[int, int, int]:
        i = LABEL_PRIORITY.index(s.source) if s.source in LABEL_PRIORITY else len(LABEL_PRIORITY)
        return (i, s.rank or 10_000, len(s.term))
    return min(group, key=prio).term


# -- scoring ---------------------------------------------------------------------------------

def _rank_points(rank: int | None) -> float:
    return 0.0 if not rank else 1.0 / (1.0 + math.log(rank))


def signal_value(s: Signal, now: datetime) -> tuple[float, str]:
    """(value, human fragment) for one signal: recency x (rank + magnitude)."""
    born = s.seen_at
    if s.extra.get("created_at"):          # stories / repos: age counts from their creation
        try:
            born = datetime.fromisoformat(str(s.extra["created_at"]).replace("Z", "+00:00"))
            if born.tzinfo is None:           # a bare date (Federal Register) is UTC midnight
                born = born.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    age = max(0.0, (now - born).total_seconds() / 86400.0)
    decay = 0.5 ** (age / HALF_LIFE_DAYS)
    rank = _rank_points(s.rank) * (1.0 if s.source in RANKED_LISTS else 0.6)
    mag, frag = 0.0, f"{s.source}" + (f" #{s.rank}" if s.rank else "")
    x = s.extra
    if s.source == "google_trends" and x.get("traffic"):
        frag += f" ({x['traffic']} searches)"
    elif s.source == "hn":
        mag = min(1.0, math.log10(1 + (s.score or 0)) / 3)
        frag = f"hn {int(s.score or 0)} pts, {x.get('comments', 0)} comments"
    elif s.source == "github":
        mag = min(1.0, math.log10(1 + (s.score or 0)) / 3)
        frag = f"github {int(s.score or 0)} stars (new repo)"
    elif s.source == "wikipedia":
        ratio, wow = x.get("ratio") or 0, x.get("wow") or 0
        if max(ratio, wow) > 1:
            mag = min(2.0, math.log2(max(ratio, wow)) * 0.5)
            if wow >= ratio and wow > 1:
                frag = f"{s.term} pageviews x{wow} this week ({x.get('views_7d')}/day vs {x.get('prev_7d')}/day before)"
            else:
                frag = f"{s.term} pageviews x{ratio} (3-day avg {x.get('views_3d')} vs {x.get('base_14d')} median)"
        elif x.get("views"):
            frag = f"wikipedia #{s.rank} most-read ({x['views']} views)"
    elif s.source == "gdelt":
        ratio = x.get("ratio") or 0
        mag = min(1.0, math.log2(max(ratio, 1.0)) * 0.3)
        frag = f"news volume x{ratio or '>10'} vs 4-week mean" if x.get("metric") else f"in the news (GDELT, {x.get('domain', '')})"
        if not x.get("metric"):
            mag = 0.3
    elif s.source == "federal_register":
        mag = 0.5
        frag = f"federal register {x.get('type', 'document')}" + (
            f" ({', '.join(x['agencies'][:1])})" if x.get("agencies") else "")
    elif s.source == "apple":
        frag = f"apple {x.get('chart', 'chart')} #{s.rank}"
    elif s.source == "rss":
        frag = "your feed"
        mag = 0.3
    return decay * (rank + mag), frag


def score_topic(topic: Topic, now: datetime | None = None) -> Topic:
    """Fill ``topic.score`` (0-100) and ``topic.why``. Best signal per source counts, plus a bonus
    for every extra independent source."""
    now = now or utcnow()
    best: dict[str, tuple[float, str]] = {}
    for s in topic.signals:
        v = signal_value(s, now)
        if s.source not in best or v[0] > best[s.source][0]:
            best[s.source] = v
    values = sorted(best.values(), key=lambda t: -t[0])
    raw = values[0][0] + 0.5 * sum(v for v, _ in values[1:]) + 0.75 * max(0, len(best) - 1) if values else 0.0
    topic.score = round(100.0 * (1.0 - math.exp(-raw / 2.5)), 1)
    matched = sorted({m for s in topic.signals for m in s.extra.get("matched", [])})
    parts = [f for _, f in values]
    if len(best) > 1:
        parts.insert(0, f"{len(best)} sources")
    if matched:
        parts.append("matches: " + ", ".join(matched[:4]))
    topic.why = "; ".join(parts)
    return topic


def rank_topics(topics: list[Topic], now: datetime | None = None) -> list[Topic]:
    return sorted((score_topic(t, now) for t in topics), key=lambda t: (-t.score, t.label))

"""Phrases -> 1-4 niches per site (by URL section, else co-occurrence), kind detection, source suggestions.

Everything is deterministic (same crawl -> same niches) and rule-based; no LLM.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Sequence

from nicheclock.generic import is_generic

from .extract import (
    DICT_MIN_DF,
    common_noun_reason,
    is_broad_entity,
    KNOWN_SHORT,
    MAX_SHARE,
    analyze,
    load_background,
    template_phrases,
    KIND_FORMATS,
    KIND_LEXICON,
    Background,
    Doc,
    EntityResolver,
    Extraction,
    brand_tokens,
    build_docs,
    extract,
    split_keywords_entities,
    title_matches,
    tokenize,
)

# kind -> sources (names from nicheclock.config.KNOWN_SOURCES). `rss` is only enabled once the user
# supplies feeds (config validation rejects `rss` with an empty `rss:` list).
SOURCES_BY_KIND: dict[str, list[str]] = {
    # query-driven sources run for every niche (hn, wikipedia, google_trends, gdelt); the rest depend on the kind
    "games": ["google_trends", "wikipedia", "hn", "gdelt"],
    "dev_ai": ["google_trends", "wikipedia", "hn", "gdelt", "github"],
    "apps": ["google_trends", "wikipedia", "hn", "gdelt", "apple"],
    "policy": ["google_trends", "wikipedia", "hn", "gdelt", "federal_register"],
    "default": ["google_trends", "wikipedia", "hn", "gdelt"],
}
# "Upcoming" event kinds that make sense per niche kind (informational; printed by `init`).
UPCOMING_KINDS: dict[str, list[str]] = {
    "games": ["game_release", "game_event", "game_update", "seasonal_peak", "early_riser"],
    "dev_ai": ["seasonal_peak", "early_riser"],
    "apps": ["app_release", "seasonal_peak", "early_riser"],
    "policy": ["regulatory_date", "seasonal_peak", "early_riser"],
    "default": ["seasonal_peak", "early_riser"],
}
_KIND_PRIORITY = ("policy", "games", "apps", "dev_ai")
_MIN_KIND_SCORE = 2.0


@dataclass
class NicheDraft:
    name: str
    keywords: list[str]
    entities: list[str]
    formats: list[str]
    exclude: list[str]
    kind: str
    sources: list[str]
    upcoming_kinds: list[str]
    # ranked phrases of the cluster before the entity split, for reports and the LLM step
    phrases: list[str] = field(default_factory=list)
    section: str = ""                                    # URL section the niche was split on ("" = co-occurrence / general)
    urls: frozenset[str] = frozenset()                   # pages that belong to the niche
    feeds: list[Any] = field(default_factory=list)       # discovered feeds (feeds.Feed), filled by `init`
    context: list[str] = field(default_factory=list)     # broad Wikipedia titles: count only next to a keyword
    watch: list[Any] = field(default_factory=list)       # feeds of sites the user named with --watch

    def to_config(self) -> dict[str, Any]:
        cfg = {
            "name": self.name,
            "keywords": list(self.keywords),
            "entities": list(self.entities),
            "formats": list(self.formats),
            "exclude": list(self.exclude),
            "sources": list(self.sources) + (["rss"] if (self.feeds or self.watch) and "rss" not in self.sources else []),
            "rss": [f.url for f in self.feeds],
            "ics": [],
            "upcoming": True,
        }
        if self.context:
            cfg["context"] = list(self.context)
        if self.watch:
            cfg["watch"] = [f.url for f in self.watch]
        return cfg


# ---------------------------------------------------------------- grouping


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _jac(a: frozenset | set, b: frozenset | set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similarity_matrix(phrases: Sequence[str], docs: Sequence[Doc]) -> list[list[float]]:
    n = len(phrases)
    total = len(docs)
    sec_count = Counter(d.section for d in docs)
    informative = {s for s, c in sec_count.items() if s != "/" and c >= 3 and 0.03 <= c / total <= 0.8}
    occ: list[set[int]] = []
    prof: list[dict[str, float]] = []
    for p in phrases:
        idx = {i for i, d in enumerate(docs) if p in d.phrases}
        occ.append(idx)
        c = Counter(docs[i].section for i in idx if docs[i].section in informative)
        prof.append({s: float(v) for s, v in c.items()})
    words = [set(p.split()) for p in phrases]
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = 0.45 * _jac(occ[i], occ[j]) + 0.40 * _cos(prof[i], prof[j]) + 0.15 * _jac(words[i], words[j])
            sim[i][j] = sim[j][i] = s
    return sim


def cluster_phrases(
    phrases: Sequence[str],
    docs: Sequence[Doc],
    *,
    max_niches: int = 5,
    min_size: int = 5,
    threshold: float = 0.12,
    min_cohesion: float = 0.1,
) -> list[list[str]]:
    """Group phrases by where they co-occur: same pages, same URL sections, shared words.

    Average-linkage agglomeration on sim = .45 page-Jaccard + .40 section-cosine + .15 word-Jaccard.
    Sections shared by >80% of pages (or by <3 pages) say nothing and are ignored, so flat sites
    cluster on page co-occurrence alone. Clusters below `min_size` are folded into their nearest
    neighbour; clusters with mean pair similarity below `min_cohesion` are noise and dissolve into the
    rest (a site with no real structure gets one niche); never more than `max_niches`. Output order: biggest total phrase weight first,
    phrases keep their input (rank) order inside a cluster.
    """
    phrases = list(phrases)
    n = len(phrases)
    if n <= 1:
        return [phrases] if phrases else []
    sim = similarity_matrix(phrases, docs)

    clusters: list[list[int]] = [[i] for i in range(n)]

    def link(a: list[int], b: list[int]) -> float:
        return sum(sim[i][j] for i in a for j in b) / (len(a) * len(b))

    def best_pair(allowed=None):
        best = None
        for x in range(len(clusters)):
            for y in range(x + 1, len(clusters)):
                if allowed and not allowed(clusters[x], clusters[y]):
                    continue
                key = (-link(clusters[x], clusters[y]), x, y)
                if best is None or key < best[0]:
                    best = (key, x, y)
        return best

    def merge(x: int, y: int) -> None:
        clusters[x] = sorted(clusters[x] + clusters[y])
        del clusters[y]

    while len(clusters) > 1:
        b = best_pair()
        if b is None or -b[0][0] < threshold:
            break
        merge(b[1], b[2])
    while len(clusters) > 1:
        small = [x for x, c in enumerate(clusters) if len(c) < min_size]
        if not small:
            break
        x = min(small, key=lambda k: (len(clusters[k]), k))
        others = [y for y in range(len(clusters)) if y != x]
        y = min(others, key=lambda k: (-link(clusters[x], clusters[k]), -len(clusters[k]), k))
        merge(*sorted((x, y)))
    # Clusters whose phrases hardly ever share pages/sections are noise (a pile of unrelated one-off
    # titles), not a niche: dissolve them into the cohesive clusters, or into one niche if none is.
    def cohesion(c: list[int]) -> float:
        pairs = [sim[i][j] for k, i in enumerate(c) for j in c[k + 1:]]
        return sum(pairs) / len(pairs) if pairs else 0.0

    solid = [c for c in clusters if cohesion(c) >= min_cohesion]
    if len(solid) < len(clusters):
        loose = sorted(i for c in clusters if cohesion(c) < min_cohesion for i in c)
        if not solid:
            solid = [loose]
        else:
            for i in loose:
                tgt = max(range(len(solid)), key=lambda k: (sum(sim[i][j] for j in solid[k]) / len(solid[k]),
                                                              len(solid[k]), -k))
                solid[tgt] = solid[tgt] + [i]
        clusters = [sorted(c) for c in solid]
    while len(clusters) > max_niches:
        b = best_pair()
        merge(b[1], b[2])

    clusters.sort(key=lambda c: (-sum(1.0 / (1 + i) for i in c), c[0]))
    return [[phrases[i] for i in c] for c in clusters]


# ---------------------------------------------------------------- kind + sources


def classify(phrases: Sequence[str], formats: Sequence[str] = ()) -> str:
    toks = [t for p in phrases for t in tokenize(p)]
    scores: dict[str, float] = {}
    for kind, lex in KIND_LEXICON.items():
        s = float(sum(1 for t in toks if t in lex))
        s += 2.0 * len(set(formats) & KIND_FORMATS.get(kind, frozenset()))
        scores[kind] = s
    best = max(scores.values()) if scores else 0.0
    if best < _MIN_KIND_SCORE:
        return "default"
    return next(k for k in _KIND_PRIORITY if scores.get(k) == best)


def suggest_sources(kind: str) -> list[str]:
    return list(SOURCES_BY_KIND.get(kind, SOURCES_BY_KIND["default"]))


# ---------------------------------------------------------------- assembling


_SMALL_WORDS = frozenset("of the a an and or in on for to vs".split())
_ACRONYMS = frozenset(
    """ai eu uk us un ml ar vr xr pc tv ui ux api sdk cli llm gpt saas gdpr ftc sec doj irs fda epa fcc nist iso hipaa
    ccpa rpg fps mmo moba dlc gta cod nba nfl mlb nhl fifa ufc wwe ps5 ps4 ios mcp aws gcp""".split()
)


def title_case(text: str) -> str:
    """"us bird act" -> "US Bird Act"; words that already carry capitals ("eBird", "iPhone") are kept."""
    out = []
    for i, w in enumerate(text.split()):
        if any(c.isupper() for c in w):
            out.append(w)
        elif w.lower() in _ACRONYMS:
            out.append(w.upper())
        elif i > 0 and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _niche_name(phrases: Sequence[str], titles: dict[str, str], taken: set[str]) -> str:
    """Title Case name from the niche's top entity (among its first five phrases), else its top phrase."""
    src = next((titles[p] for p in phrases[:5] if p in titles), None) or (phrases[0] if phrases else "niche")
    name = title_case(src)
    base, k = name, 2
    while name in taken:
        name = f"{base} ({k})"
        k += 1
    return name


def _niche_formats(docs: Sequence[Doc], phrases: Sequence[str], site: Counter, limit: int = 8) -> list[str]:
    pset = set(phrases)
    c = Counter(f for d in docs if d.phrases & pset for f in d.formats)
    out = [f for f, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 2][:limit]
    if len(out) < 2:  # too few niche pages: fall back to the site-wide formats
        for f, n in sorted(site.items(), key=lambda kv: (-kv[1], kv[0])):
            if n >= 2 and f not in out and len(out) < limit:
                out.append(f)
    return out


# URL sections that name a kind of page, not a topic: their pages stay in the general niche.
GENERIC_SECTIONS = frozenset(
    """blog blogs news post posts article articles glossary tools tool topics topic stories story resources library
    learn wiki docs help support faq press newsletter updates latest archive archives guides guide reviews review
    tutorials how-to compare comparisons templates checklists category categories tag tags author authors events
    giveaways deals""".split()
)


def split_by_section(docs: Sequence[Doc], max_niches: int = 4, *, min_docs: int = 8,
                     min_share: float = 0.03) -> list[tuple[str, list[Doc]]] | None:
    """Topical top-level URL sections (/snowy-owl/..., /gear/...) -> [(section, docs)], largest first.

    Pages of every other section (blog, glossary ...) and flat URLs form the trailing general group ("").
    None when the site has no topical section big enough: use co-occurrence instead.
    """
    n = len(docs)
    sec = Counter(d.section for d in docs)
    keep = [s for s, c in sorted(sec.items(), key=lambda kv: (-kv[1], kv[0]))
            if s != "/" and s not in GENERIC_SECTIONS and c >= max(min_docs, min_share * n)][: max(max_niches - 1, 0)]
    if not keep:
        return None
    groups: list[tuple[str, list[Doc]]] = [(s, [d for d in docs if d.section == s]) for s in keep]
    rest = [d for d in docs if d.section not in keep]
    if len(rest) >= max(min_docs, min_share * n):
        groups.append(("", rest))
    return groups


def _group_phrases(docs: Sequence[Doc], bg: Background, brand: frozenset[str], backend: str,
                   skip: frozenset[str], pool: int) -> tuple[Extraction, list[str]]:
    ext = extract(docs, bg, brand=brand, backend=backend, skip=skip, max_share=None)
    return ext, ext.ranked[:pool]


def draft_niches(
    docs: Sequence[Doc],
    bg: Background | None = None,
    resolver: EntityResolver | None = None,
    *,
    brand: frozenset[str] = frozenset(),
    backend: str = "python",
    skip: frozenset[str] = frozenset(),
    max_niches: int = 4,
    pool: int = 40,
    per_niche: int = 20,
    entity_limit: int = 30,
    min_size: int = 5,
) -> list[NicheDraft]:
    """1..max_niches niches: one per topical URL section, or by co-occurrence when the site has none."""
    bg = bg or load_background()
    site = extract(docs, bg, brand=brand, backend=backend, skip=skip)
    if not site.ranked:
        return []
    # (label of the group, ranked phrases, docs the group owns, extraction used for formats)
    groups: list[tuple[str, list[str], list[Doc], Extraction]] = []
    secs = split_by_section(docs, max_niches)
    if secs:
        n = len(docs)
        cnt = Counter(p for d in docs for p in d.phrases)
        broad = frozenset(p for p, c in cnt.items() if n >= 20 and c / n > MAX_SHARE and bg.df.get(p, 0) >= 2)   # site-wide boilerplate
        parts = [(sec, gd, *_group_phrases(gd, bg, brand, backend, skip | broad, pool)) for sec, gd in secs]
        def share(k: int, p: str) -> float:
            return parts[k][2].df.get(p, 0) / len(parts[k][1])

        for idx, (sec, gd, ext, ranked) in enumerate(parts):
            # a phrase belongs to the group where it is most prevalent
            mine = [p for p in ranked if max(range(len(parts)), key=lambda k: (share(k, p), -k)) == idx]
            if mine:
                groups.append((sec, mine, gd, ext))
    else:
        ranked = site.ranked[:60]
        for g in cluster_phrases(ranked, docs, max_niches=max_niches, min_size=min_size):
            groups.append(("", g, list(docs), site))
    if not groups:
        return []

    # entity lookup over the top phrases of every group, interleaved so each group gets its share
    cands: list[str] = []
    for i in range(max(len(g[1]) for g in groups)):
        cands += [g[1][i] for g in groups if i < len(g[1]) and g[1][i] not in cands]
    _, entity_of = split_keywords_entities(cands, resolver, entity_limit=entity_limit)
    for p, t in list(entity_of.items()):
        # an ALL-CAPS single word that is also a plain dictionary word ("TEXT") is template noise, not an entity
        if " " not in t and t.isupper() and bg.df.get(t.lower(), 0) >= DICT_MIN_DF and t.lower() not in KNOWN_SHORT:
            del entity_of[p]

    # broad entities (places, generic concepts, very popular pages) are context, not entities
    broad_titles: set[str] = set()
    if resolver is not None:
        for t in dict.fromkeys(entity_of.values()):
            desc = resolver.describe(t)
            if is_broad_entity(desc, None) or is_broad_entity(desc, resolver.median_daily_views(t)):
                broad_titles.add(t)

        # common-noun phrases ("Bird feeder", "Field guide") are not specific entities either: context
        rest = [t for t in dict.fromkeys(entity_of.values()) if t not in broad_titles]
        leads = resolver.leads(rest) if rest else {}
        for t in rest:
            if common_noun_reason(t, resolver.describe(t), leads.get(t, "")):
                broad_titles.add(t)

    site_excl = [(w, c) for w, c in site.excludes if c >= 2]
    out: list[NicheDraft] = []
    taken: set[str] = set()
    used_titles: set[str] = set()
    for sec, g, gdocs, ext in groups:
        g = g[:per_niche]
        kw = [p for p in g if p not in entity_of or (not title_matches(p, entity_of[p])
                                                      and entity_of[p] not in broad_titles)]
        # every keyword becomes a live query: most specific first (multi-word), and single words that are
        # ordinary English ("standard", "commander") are too generic to query, so they are left out
        kw = [p for p in kw if " " in p] + [p for p in kw if " " not in p and bg.df.get(p, 0) < DICT_MIN_DF][:4]
        kw = [p for p in kw if not is_generic(p)] + [p for p in kw if is_generic(p)]   # generic ones are queried last
        ents: list[str] = []
        context: list[str] = []
        for p in g:
            t = entity_of.get(p)
            if not t:
                continue
            if t in broad_titles:
                if t not in context:
                    context.append(t)
            elif t not in used_titles:
                used_titles.add(t)
                ents.append(t)
        ents.sort(key=lambda t: " " not in t)             # multi-word (laws, titles, products) before single words
        if not (kw or ents) and g:                         # never leave a niche with only context
            kw = [g[0]]
        formats = _niche_formats(gdocs, g, Counter(dict(ext.formats)))
        words = {t for p in g for t in p.split()}
        exclude = [w for w, _ in site_excl if w not in words][:6]
        kind = classify(g + ([sec] if sec else []), formats)
        name = _niche_name(g, {p: t for p, t in entity_of.items() if t not in broad_titles}, taken)
        taken.add(name)
        pset = set(g)
        urls = frozenset(d.url for d in gdocs if sec or d.phrases & pset)
        out.append(NicheDraft(name=name, keywords=kw, entities=ents, formats=formats, exclude=exclude,
                              kind=kind, sources=suggest_sources(kind),
                              upcoming_kinds=list(UPCOMING_KINDS.get(kind, UPCOMING_KINDS["default"])),
                              phrases=list(g), section=sec, urls=urls, context=context))
    return out


def draft_from_docs(docs: Sequence[Doc], bg: Background | None = None, resolver: EntityResolver | None = None,
                    **kw: Any) -> list[NicheDraft]:
    return draft_niches(docs, bg, resolver, **kw)


def project_from_crawls(
    name: str,
    crawls: Sequence[Any],
    bg: Background | None = None,
    resolver: EntityResolver | None = None,
    *,
    backend: str = "python",
    **kw: Any,
) -> tuple[dict[str, Any], list[NicheDraft], list[Doc]]:
    """SiteCrawl(s) of one project -> (project dict in config schema, niche drafts, docs)."""
    bg = bg or load_background()
    brand = brand_tokens([c.base for c in crawls])
    docs: list[Doc] = []
    for c in crawls:
        titles = c.titles()
        slug_sets = [d.phrases for d in build_docs(c.urls, None, brand)]
        title_sets = [analyze(tokenize(t), brand)[0] | analyze(tokenize(h), brand)[0] for t, h in titles.values()]
        docs += build_docs(c.urls, titles, brand, drop=template_phrases(slug_sets, title_sets))
    drafts = draft_niches(docs, bg, resolver, brand=brand, backend=backend, **kw)
    return project_dict(name, [c.base for c in crawls], drafts), drafts, docs


def project_dict(name: str, urls: Sequence[str], drafts: Sequence[NicheDraft]) -> dict[str, Any]:
    return {"name": name, "urls": list(urls), "niches": [d.to_config() for d in drafts],
            "schedule": "daily", "notify": []}

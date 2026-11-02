"""Bridges: topics that travel with a hobby, sport or field, read from the links of its Wikipedia article.

Wikidata does not tie a hobby to its subject (``birdwatching`` is "hobby, subclass of wildlife observation", with no edge to
birds), so a profile that names only the hobby matches nothing. The English article of the hobby does link to the subject.
For each anchor (a concept that is a hobby, sport, game, craft or field of study, plus the sample profiles' topics) this
reads the article's links, keeps those that are in the concept index and are themselves handles of pool rows, and writes the
best few as ``bridges.json``. The page offers them as chips ("People who write about X also cover ..."); nothing is added
to a profile without the user's click, and matching never uses them.

The article's links are facts about which pages link to which (CC BY-SA text is not copied; only titles' Wikidata ids).
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from nicheclock.catalog import pool
from nicheclock.catalog.wdbulk import MISSING, BulkFetcher, EntityStore

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
# Direct P31 / P279 targets that make a concept an anchor: hobby, sport, academic discipline, branch of science, game,
# craft, activity (Wikidata Q47728, Q349, Q11862829, Q2465832, Q11410, Q131436, Q2207288, Q1914636).
ANCHOR_CLASSES = frozenset({"Q47728", "Q349", "Q11862829", "Q2465832", "Q11410", "Q131436", "Q2207288", "Q1914636"})
MAX_ANCHORS = 1500
MIN_NEIGHBOUR_DF = 5            # a neighbour must be the handle of at least this many pool rows, else it leads nowhere
KEEP = 8
MUTUAL_MIN = 1                 # at least this many articles must link both ways, else no bridge is offered
NOTE = ("For a hobby, sport or field of study: the topics its English Wikipedia article links to and that link back, "
        "which are also handles of pool rows. [qid, rows it matches]")


def anchors(store: EntityStore, concepts: list[list[Any]], seeds: tuple[str, ...]) -> list[str]:
    out = list(seeds)
    for row in concepts:
        q = "Q" + str(row[0])
        rec = store.get(q)
        if rec is MISSING or rec is None:
            continue
        if ANCHOR_CLASSES & set(rec["c"].get("P31", []) + rec["c"].get("P279", [])):
            out.append(q)
    return list(dict.fromkeys(out))[:MAX_ANCHORS]


def _titles_for(fetcher: BulkFetcher, qids: list[str], store: EntityStore) -> dict[str, str]:
    """{qid: English Wikipedia title} for the anchors, from Wikidata's sitelinks (one batched call per 50)."""
    out: dict[str, str] = {}
    from nicheclock.catalog.wdbulk import API
    chunks = [qids[n:n + 50] for n in range(0, len(qids), 50)]

    def one(chunk: list[str]) -> dict[str, str]:
        data = fetcher.get_json(API, {"action": "wbgetentities", "ids": "|".join(chunk), "props": "sitelinks", "sitefilter": "enwiki",
                                      "format": "json", "formatversion": 2, "maxlag": 5})
        return {q: e["sitelinks"]["enwiki"]["title"] for q, e in (data.get("entities") or {}).items() if (e.get("sitelinks") or {}).get("enwiki")}

    with ThreadPoolExecutor(fetcher.threads) as ex:
        for part in ex.map(one, chunks):
            out.update(part)
    return out


def _pages(fetcher: BulkFetcher, generator: str, title: str, pages: int = 1) -> list[str]:
    """Wikidata ids of the main-namespace pages an article links to (``links``) or is linked from (``linkshere``): 500 per page of
    results, ``pages`` pages at most."""
    short = "gpl" if generator == "links" else "glh"
    params = {"action": "query", "generator": generator, "titles": title, "redirects": 1, short + "limit": 500, short + "namespace": 0,
              "prop": "pageprops", "ppprop": "wikibase_item", "format": "json", "formatversion": 2, "maxlag": 5}
    out: list[str] = []
    for _ in range(pages):
        data = fetcher.get_json(WIKIPEDIA_API, params)
        out += [q for p in (data.get("query") or {}).get("pages", []) if (q := (p.get("pageprops") or {}).get("wikibase_item"))]
        cont = data.get("continue")
        if not cont:
            break
        params = {**params, **cont}
    return out


def build(store: EntityStore, fetcher: BulkFetcher, concepts: list[list[Any]], rows_by_region: dict[str, list[list[Any]]], rt: Any,
          user_agent: str, contact: str, log: Callable[[str], None] = print, seeds: tuple[str, ...] = ()) -> dict[str, Any]:
    df: Counter = Counter()
    for rows in rows_by_region.values():
        for r in rows:
            for h in r[4]:
                df[pool.decode(h)[0]] += 1
    in_index = {"Q" + str(c[0]) for c in concepts}
    anc = anchors(store, concepts, seeds or pool.SAMPLE_QIDS)
    log(f"bridges: {len(anc)} anchors")
    titles = _titles_for(fetcher, anc, store)
    result: dict[str, list[list[int]]] = {}

    def one(q: str) -> tuple[str, list[str], set[str]]:
        try:
            return q, _pages(fetcher, "links", titles[q]), set(_pages(fetcher, "linkshere", titles[q], pages=3))
        except Exception as e:                                        # one article must not stop the others
            log(f"  bridges: {q}: {str(e)[:80]}")
            return q, [], set()

    with ThreadPoolExecutor(fetcher.threads) as ex:
        for q, links, back in ex.map(one, [q for q in anc if q in titles]):
            near = [x for x in dict.fromkeys(links) if x != q and x in in_index and df[x] >= MIN_NEIGHBOUR_DF]
            mutual = [x for x in near if x in back]                    # the other article links back: related, not a passing mention
            near = mutual if len(mutual) >= MUTUAL_MIN else []         # no article links back: nothing is offered rather than a passing mention
            near.sort(key=lambda x: (-df[x], x))
            if near:
                result[q] = [[int(x[1:]), df[x]] for x in near[:KEEP]]
    return {"note": NOTE, "bridges": {q: v for q, v in sorted(result.items())}}

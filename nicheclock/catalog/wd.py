"""Wikidata (CC0) through the Action API: entity search, compact entity records, title -> QID, SPARQL.

Every call goes through ``nicheclock.http.Client`` (identity User-Agent, per-host spacing, 429 handling)
and every result is cached in a ``Cache``.
"""

from __future__ import annotations

from typing import Any, Iterable

from nicheclock.catalog.cache import Cache
from nicheclock.http import FetchError, RobotsDisallowed

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
BATCH = 50
ENTITY_TTL = 7 * 86400
SEARCH_TTL = 14 * 86400
SPARQL_TTL = 12 * 3600
# Statements kept per entity (everything the category rules read).
TRACKED = ("P31", "P279", "P106", "P641", "P171", "P136")
# Sitelink sites kept as page titles (the wikis the daily build reads).
KEEP_SITES = ("enwiki", "eswiki", "hiwiki", "zhwiki", "idwiki", "urwiki", "ptwiki", "bnwiki", "ruwiki", "amwiki",
              "hawiki", "yowiki")
# Label languages kept besides English (native topic names per region).
LABEL_LANGS = ("hi", "zh", "id", "ur", "pt", "bn", "ru", "am", "ha", "yo", "es", "om", "ig")


def compact_entity(raw: dict[str, Any]) -> dict[str, Any] | None:
    """The record cached per entity; None when the entity is missing."""
    if not raw or "missing" in raw:
        return None
    labels, descs, aliases = raw.get("labels") or {}, raw.get("descriptions") or {}, raw.get("aliases") or {}
    sitelinks = raw.get("sitelinks") or {}
    claims: dict[str, list[str]] = {}
    for prop in TRACKED:
        vals = []
        for st in (raw.get("claims") or {}).get(prop, []):
            if st.get("rank") == "deprecated":
                continue
            snak = st.get("mainsnak") or {}
            v = (snak.get("datavalue") or {}).get("value")
            if snak.get("snaktype") == "value" and isinstance(v, dict) and v.get("id"):
                vals.append(v["id"])
        if vals:
            claims[prop] = vals
    return {
        "id": raw["id"],
        "label": (labels.get("en") or {}).get("value") or "",
        "labels": {lg: labels[lg]["value"] for lg in LABEL_LANGS if lg in labels},
        "description": (descs.get("en") or {}).get("value") or "",
        "aliases": [a["value"] for a in aliases.get("en", [])][:12],
        "sitelinks": len(sitelinks),
        "titles": {s: sitelinks[s]["title"] for s in KEEP_SITES if s in sitelinks},
        "claims": claims,
    }


class Wikidata:
    def __init__(self, client: Any, cache: Cache) -> None:
        self.client = client
        self.cache = cache

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Action API GET; an ``error`` object in a 200 response is a failure too."""
        data = self.client.get_json(API, params=params)
        if "error" in data:
            raise FetchError(f"wikidata {params.get('action')}: {data['error'].get('code')}: {data['error'].get('info')}")
        return data

    # -- search -----------------------------------------------------------------------------
    def search(self, text: str, lang: str = "en", limit: int = 7) -> list[dict[str, Any]]:
        key = f"{lang}|{limit}|{text}"
        found, hit = self.cache.get("search", key, SEARCH_TTL)
        if found:
            return hit
        try:
            data = self._call({
                "action": "wbsearchentities", "search": text, "language": lang, "uselang": lang,
                "type": "item", "limit": limit, "format": "json"})
        except (FetchError, RobotsDisallowed):
            return []
        out = [{"id": r["id"], "label": r.get("label") or "", "description": r.get("description") or "",
                "match": (r.get("match") or {}).get("type") or "", "match_text": (r.get("match") or {}).get("text") or ""}
               for r in data.get("search", [])]
        self.cache.put("search", key, out)
        return out

    # -- entities ---------------------------------------------------------------------------
    def entities(self, ids: Iterable[str]) -> dict[str, dict[str, Any] | None]:
        """Compact records by QID (``None`` = missing). Redirected ids map to the target's record."""
        want = list(dict.fromkeys(i for i in ids if i))
        out: dict[str, dict[str, Any] | None] = {}
        todo = []
        for i in want:
            found, hit = self.cache.get("ent2", i, ENTITY_TTL)
            if found:
                out[i] = hit
            else:
                todo.append(i)
        for n in range(0, len(todo), BATCH):
            chunk = todo[n:n + BATCH]
            try:
                data = self._call({
                    "action": "wbgetentities", "ids": "|".join(chunk), "props": "labels|descriptions|aliases|sitelinks|claims",
                    "languages": "|".join(("en",) + LABEL_LANGS), "format": "json", "formatversion": 2})
            except (FetchError, RobotsDisallowed):
                continue
            ents = data.get("entities") or {}
            for i in chunk:
                rec = compact_entity(ents.get(i) or {})
                out[i] = rec
                self.cache.put("ent2", i, rec)
            self.cache.commit()
        return out

    def qids_for_titles(self, site: str, titles: list[str]) -> dict[str, str | None]:
        """{title: QID or None} for page titles of one wiki (``enwiki``, ``jawiki``...)."""
        out: dict[str, str | None] = {}
        todo = []
        for t in dict.fromkeys(titles):
            found, hit = self.cache.get("sl2:" + site, t, ENTITY_TTL)
            if found:
                out[t] = hit
            else:
                todo.append(t)
        for n in range(0, len(todo), BATCH):
            chunk = todo[n:n + BATCH]
            try:
                # no "normalize": the API only allows it for a single title; callers pass canonical titles
                data = self._call({
                    "action": "wbgetentities", "sites": site, "titles": "|".join(t.replace("_", " ") for t in chunk),
                    "props": "labels|descriptions|aliases|sitelinks|claims", "languages": "|".join(("en",) + LABEL_LANGS),
                    "format": "json", "formatversion": 2})
            except (FetchError, RobotsDisallowed):
                continue
            by_title: dict[str, str] = {}
            for qid, raw in (data.get("entities") or {}).items():
                rec = compact_entity(raw)
                if rec is None:
                    continue
                self.cache.put("ent2", rec["id"], rec)
                st = (raw.get("sitelinks") or {}).get(site)
                if st:
                    by_title[st["title"]] = rec["id"]
            for t in chunk:
                q = by_title.get(t.replace("_", " "))
                out[t] = q
                self.cache.put("sl2:" + site, t, q)
            self.cache.commit()
        return out

    # -- SPARQL -----------------------------------------------------------------------------
    def sparql(self, query: str) -> list[dict[str, Any]]:
        """Result bindings flattened to ``{var: value}``. Raises FetchError on timeout / HTTP errors."""
        found, hit = self.cache.get("sparql", query, SPARQL_TTL)
        if found:
            return hit
        data = self.client.get_json(SPARQL, params={"query": query, "format": "json"},
                                    headers={"Accept": "application/sparql-results+json"}, timeout=75)
        rows = [{k: v.get("value") for k, v in b.items()} for b in data["results"]["bindings"]]
        self.cache.put("sparql", query, rows)
        self.cache.commit()
        return rows

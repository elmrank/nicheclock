"""Resolver: free text (or a QID, or a Wikipedia URL) -> one Wikidata QID.

Wikidata search returns candidates in its own order. We re-rank them by what a person means by the
text: an exact label match beats an alias match, and among equal matches the entity with more sitelinks
(wikis that have an article on it) wins, because popularity is the best cheap prior for "which
Mercury". Pages that are not topics (disambiguation, lists, categories, templates) are skipped.
``ambiguous`` is set when a runner-up is almost as plausible, so callers can show alternatives instead
of guessing silently.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from nicheclock.catalog.wd import Wikidata

QID = re.compile(r"^[Qq][1-9]\d*$")
# P31 values that are not topics.
NOT_TOPIC = {
    "Q4167410": "disambiguation page", "Q13406463": "list article", "Q4167836": "category",
    "Q11266439": "template", "Q17633526": "Wikinews article", "Q15184295": "module",
    "Q11753321": "navigational template", "Q97303170": "disambiguation (name)",
}
WIKI_HOST = re.compile(r"^([a-z\-]{2,12})\.(?:m\.)?wikipedia\.org$")
CACHE_TTL = 30 * 86400
# Hints a caller may give: the class of thing the text names (a GDELT name list says "person" or "org").
HINT_CLASSES = {"person": {"Q5"}, "org": {"Q43229", "Q783794", "Q4830453", "Q891723", "Q48204"}}
AMBIGUITY_RATIO = 0.6            # runner-up score / best score at or above this -> ambiguous
MATCH_WEIGHT = {"label": 1.0, "alias": 0.55}


def norm(text: str) -> str:
    """Casefolded, accent-stripped, punctuation-light form used to compare a text with labels."""
    t = unicodedata.normalize("NFKD", text).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^\w]+", " ", t).strip()


@dataclass
class Resolution:
    text: str
    qid: str | None = None
    label: str = ""
    description: str = ""
    sitelinks: int = 0
    match: str = ""                # "label" | "alias" | "qid" | "url" | "fuzzy"
    confidence: float = 0.0        # 0..1
    ambiguous: bool = False
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""               # why nothing resolved

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(s: str) -> set[str]:
    return {w for w in norm(s).split() if len(w) > 2}


class Resolver:
    def __init__(self, wd: Wikidata) -> None:
        self.wd = wd
        self.cache = wd.cache

    # -- public -----------------------------------------------------------------------------
    def resolve(self, text: str, *, hint: str | None = None, context: str = "", lang: str = "en",
                min_sitelinks: int = 0) -> Resolution:
        """``hint``: "person" | "org" | None. ``context``: extra words ("planet", "band") that favour
        candidates whose description contains them. ``min_sitelinks``: reject unknown entities."""
        text = (text or "").strip()
        if not text:
            return Resolution(text, reason="empty text")
        key = f"{lang}|{hint or ''}|{norm(context)}|{min_sitelinks}|{text}"
        found, hit = self.cache.get("resolve", key, CACHE_TTL)
        if found:
            return Resolution(**hit)
        res = self._resolve(text, hint, context, lang, min_sitelinks)
        self.cache.put("resolve", key, res.to_dict())
        return res

    def resolve_many(self, texts: list[str], **kw: Any) -> dict[str, Resolution]:
        return {t: self.resolve(t, **kw) for t in dict.fromkeys(texts)}

    # -- internals --------------------------------------------------------------------------
    def _direct(self, text: str, lang: str) -> Resolution | None:
        """A QID or a Wikipedia article URL resolves without search."""
        if QID.match(text):
            qid = text.upper()
            rec = self.wd.entities([qid]).get(qid)
            if rec is None:
                return Resolution(text, reason="unknown QID")
            return Resolution(text, rec["id"], rec["label"], rec["description"], rec["sitelinks"], "qid", 1.0)
        if text.startswith(("http://", "https://")):
            u = urlsplit(text)
            m = WIKI_HOST.match(u.netloc.lower())
            if m and u.path.startswith("/wiki/"):
                title = unquote(u.path[len("/wiki/"):]).replace("_", " ")
                site = m.group(1).replace("-", "_") + "wiki"
                qid = self.wd.qids_for_titles(site, [title]).get(title)
                if qid:
                    rec = self.wd.entities([qid])[qid] or {}
                    return Resolution(text, qid, rec.get("label", ""), rec.get("description", ""),
                                      rec.get("sitelinks", 0), "url", 1.0)
                return Resolution(text, reason="Wikipedia page has no Wikidata item")
        return None

    def _resolve(self, text: str, hint: str | None, context: str, lang: str, min_sitelinks: int) -> Resolution:
        direct = self._direct(text, lang)
        if direct is not None:
            return direct
        hits = self.wd.search(text, lang=lang)
        if not hits:
            return Resolution(text, reason="no Wikidata search result")
        ents = self.wd.entities([h["id"] for h in hits])
        want = norm(text)
        ctx = _tokens(context)
        hint_cls = HINT_CLASSES.get(hint or "", set())
        scored: list[tuple[float, dict[str, Any], str]] = []
        for h in hits:
            rec = ents.get(h["id"])
            if rec is None:
                continue
            p31 = set(rec["claims"].get("P31", []))
            if p31 & NOT_TOPIC.keys():
                continue
            if h["match"] in MATCH_WEIGHT:
                kind, weight = h["match"], MATCH_WEIGHT[h["match"]]
                if norm(h["match_text"] or h["label"]) != want:
                    kind, weight = "fuzzy", 0.25
            else:
                kind, weight = "fuzzy", 0.25
            pop = math.log1p(rec["sitelinks"])
            score = weight * (0.5 + pop)
            if hint_cls and p31 & hint_cls:
                score *= 1.5
            if ctx and ctx & _tokens(rec["description"] + " " + rec["label"]):
                score *= 2.0
            scored.append((score, rec, kind))
        if not scored:
            return Resolution(text, reason="only non-topic pages (disambiguation, list, category)")
        scored.sort(key=lambda x: -x[0])
        best_score, best, kind = scored[0]
        if best["sitelinks"] < min_sitelinks:
            return Resolution(text, reason=f"best match has {best['sitelinks']} sitelinks (< {min_sitelinks})")
        alts = [{"qid": r["id"], "label": r["label"], "description": r["description"], "sitelinks": r["sitelinks"]}
                for _, r, _ in scored[1:4]]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        ambiguous = len(scored) > 1 and runner >= AMBIGUITY_RATIO * best_score and kind != "fuzzy"
        share = best_score / (best_score + runner) if runner else 1.0
        confidence = round(share * (1.0 if kind == "label" else 0.8 if kind == "alias" else 0.4), 3)
        return Resolution(text, best["id"], best["label"], best["description"], best["sitelinks"], kind,
                          confidence, ambiguous, alts)

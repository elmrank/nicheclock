"""Match free text against a niche's keywords / entities / exclude lists."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation (``bird-watching`` -> ``bird watching``), drop ``(parenthetical)``."""
    text = re.sub(r"\([^)]*\)", " ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(_WORD.findall(text.lower().replace("&", " and ")))


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss") else tok


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in normalize(text).split()]


def contains_phrase(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    if n == 0 or n > len(hay):
        return False
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


@dataclass
class Match:
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)   # broad entities: count only beside a keyword/entity
    excluded: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Relevant to the niche and not excluded. Context alone never makes anything relevant."""
        return bool(self.keywords or self.entities) and not self.excluded

    @property
    def hits(self) -> list[str]:
        return self.entities + self.keywords


def match_text(text: str, niche: Any) -> Match:
    """``niche`` is a config.Niche (or anything with keywords/entities/exclude lists)."""
    hay = tokens(text)
    m = Match()
    m.keywords = [k for k in niche.keywords if contains_phrase(hay, tokens(k))]
    m.entities = [e for e in niche.entities if contains_phrase(hay, tokens(e))]
    m.context = [c for c in getattr(niche, "context", []) if contains_phrase(hay, tokens(c))]
    m.excluded = [x for x in niche.exclude if contains_phrase(hay, tokens(x))]
    return m


def relevant(signals: list[Any], niche: Any) -> list[Any]:
    """Keep signals relevant to the niche; tag ``extra['matched']`` with the hits.

    * excluded terms always drop a signal;
    * signals from a search a source ran *for* a niche term (``extra['query']``, e.g. GDELT,
      Federal Register) are relevant by construction, unless the source marks them ``strict``
      (fuzzy full-text search such as HN or GitHub), which must still contain a keyword or entity
      in the title or ``extra['text']``;
    * broad lists (trending, charts, feeds) must contain a keyword or entity.
    """
    out = []
    for s in signals:
        m = match_text(s.term + " " + (s.extra.get("text") or ""), niche)
        if s.extra.get("article"):
            # an article only is an entity when its title is that entity ("Godot" != "Waiting for Godot")
            m.entities = [e for e in m.entities if tokens(e) == tokens(s.term)]
            m.context = [c for c in m.context if tokens(c) == tokens(s.term)]
        if m.excluded:
            continue
        queried = s.extra.get("query") and not s.extra.get("strict")
        if not (m.keywords or m.entities or queried):
            continue
        q = s.extra.get("query")
        kw, ents = list(m.keywords), list(m.entities)
        if not (kw or ents) and q:        # relevant by the search term it was fetched for
            (ents if q in niche.entities else kw).append(q)
        s.extra["matched"] = ents + kw
        s.extra["entities"] = ents
        s.extra["keywords"] = kw
        s.extra["context"] = m.context if (kw or ents) else []
        out.append(s)
    return out

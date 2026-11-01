"""Hacker News stories (Algolia HN Search API: title, link and points only) as the community channel.

A snapshot taken at build time: the current front page plus stories of the last 48 hours with at least
``MIN_POINTS`` points. Topics come from two routes: capitalised phrases in titles resolved through
Wikidata, and titles that contain the label of a topic already found by another channel.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed

NAME = "hn"
LICENSE = "Hacker News API terms (Y Combinator / Algolia): title, link and points only"
FRONT = "https://hn.algolia.com/api/v1/search"
RECENT = "https://hn.algolia.com/api/v1/search_by_date"
MIN_POINTS = 100
UNIT = "HN points (sum over front-page and 48 h stories)"

STOP = {"the", "a", "an", "show", "ask", "tell", "how", "why", "what", "when", "who", "is", "are", "i", "we", "my",
        "our", "new", "launch", "introducing", "hn", "this", "that", "it", "in", "on", "of", "for", "to", "and",
        "or", "with", "from", "you", "your", "can", "will", "do", "does", "no", "not", "yes", "if", "all", "why"}
JOIN = {"of", "the", "de", "van", "von", "and", "for", "la", "le", "du"}
WORD = re.compile(r"[A-Z][\w.+#'’-]*")


def candidate_phrases(title: str, limit: int = 4) -> list[str]:
    """Runs of capitalised words in a story title ("Show HN: Formula One telemetry viewer" ->
    ["Formula One"]). Lone sentence-initial words are kept too; the caller resolves single words
    with a stricter popularity floor so common words fall out."""
    tokens = re.findall(r"[\w.+#'’-]+|[^\w\s]", title)
    runs: list[tuple[int, list[str]]] = []
    run: list[str] = []
    start = 0
    for i, tok in enumerate(tokens):
        if WORD.fullmatch(tok) and tok[0].isupper():
            if not run:
                start = i
            run.append(tok)
        elif run and tok.lower() in JOIN and i + 1 < len(tokens) and tokens[i + 1][:1].isupper():
            run.append(tok)
        else:
            if run:
                runs.append((start, run))
            run = []
    if run:
        runs.append((start, run))
    out: list[str] = []
    for start, words in runs:
        while words and words[-1].lower() in JOIN:
            words = words[:-1]
        while words and words[0].lower() in STOP | JOIN:
            words = words[1:]
            start += 1
        if not words:
            continue
        if len(words) == 1:
            w = words[0]
            if w.lower() in STOP or len(w) < 2:
                continue
        out.append(" ".join(words))
    return list(dict.fromkeys(out))[:limit]


def title_mentions(title: str, label: str) -> bool:
    """Whole-word, case-insensitive containment; labels shorter than 5 letters must be a single
    all-caps/capitalised token in the title (avoids "Go", "Rust" as words)."""
    if len(label) < 4:
        return False
    if len(label) < 5:
        return re.search(rf"\b{re.escape(label)}\b", title) is not None
    return re.search(rf"(?<!\w){re.escape(label)}(?!\w)", title, re.I) is not None


def fetch_stories(client: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Front page + recent high-point stories, deduplicated: [{id,title,url,hn_url,points,comments,created_at}]."""
    now = now or datetime.now(timezone.utc)
    since = int((now - timedelta(hours=48)).timestamp())
    hits: list[dict[str, Any]] = []
    for url, params in ((FRONT, {"tags": "front_page", "hitsPerPage": 60}),
                        (RECENT, {"tags": "story", "hitsPerPage": 100,
                                  "numericFilters": f"created_at_i>{since},points>={MIN_POINTS}"})):
        try:
            hits += client.get_json(url, params=params).get("hits", [])
        except (FetchError, RobotsDisallowed):
            continue
    out: dict[str, dict[str, Any]] = {}
    for h in hits:
        oid, title = h.get("objectID"), (h.get("title") or "").strip()
        if not oid or not title or oid in out:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={oid}"
        out[oid] = {"id": str(oid), "title": title, "url": h.get("url") or hn_url, "hn_url": hn_url,
                    "points": int(h.get("points") or 0), "comments": int(h.get("num_comments") or 0),
                    "created_at": h.get("created_at")}
    return sorted(out.values(), key=lambda s: -s["points"])

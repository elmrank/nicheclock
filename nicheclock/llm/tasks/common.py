"""Helpers the tasks share: site profile payload, topic payloads, batching, cache keys, reports."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence, TypeVar

from nicheclock.llm.settings import LLMSettings
from nicheclock.models import Topic

T = TypeVar("T")
BATCH = 12                 # topics per model call
CACHE_DAYS = 7             # a judgement of the same input is reused for a week
TOP_QUERIES = 15
TOP_ROWS = 20
SNIPPETS = 3
SNIPPET_CHARS = 200


@dataclass
class TaskReport:
    """What a task did this run, for the run summary."""

    judged: int = 0
    cached: int = 0
    errors: list[str] = field(default_factory=list)


def chunks(seq: Sequence[T], n: int = BATCH) -> Iterator[Sequence[T]]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def digest(*parts: Any) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode())
        h.update(b"\x1f")
    return h.hexdigest()[:24]


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _clip(s: Any, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def snippets(t: Topic) -> list[str]:
    """Up to three short lines from the topic's signals: ``source: headline`` plus any abstract."""
    out: list[str] = []
    seen: set[str] = set()
    for s in t.signals:
        line = f"{s.source}: {_clip(s.term, 140)}"
        text = s.extra.get("text") or s.extra.get("description") or s.extra.get("summary")
        if text:
            line += f" — {_clip(text, SNIPPET_CHARS)}"
        if line not in seen:
            seen.add(line)
            out.append(line)
        if len(out) >= SNIPPETS:
            break
    return out


def topic_payload(i: int, t: Topic, evidence: bool = True) -> dict[str, Any]:
    """Titles and snippets only: no URLs, no Search Console data."""
    d: dict[str, Any] = {"id": i, "title": _clip(t.label, 160), "sources": t.sources, "snippets": snippets(t)}
    if evidence and t.why:
        d["evidence"] = _clip(t.why, 200)
    return d


def gsc_queries(store: Any, project: str, limit: int = TOP_QUERIES) -> list[str]:
    """Top Search Console queries by impressions (current window): the strings only."""
    imp: dict[str, float] = defaultdict(float)
    for r in store.gsc_rows(project):
        if r.get("query") and r.get("window") in (None, "cur"):
            imp[str(r["query"])] += float(r.get("impressions") or 0)
    return [q for q, _ in sorted(imp.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def gsc_rows_payload(store: Any, project: str, limit: int = TOP_ROWS) -> list[dict[str, Any]]:
    """Raw rows (query, page, clicks, impressions, position): only with ``send_gsc_rows: true``."""
    rows = [r for r in store.gsc_rows(project) if r.get("query") and r.get("window") in (None, "cur")]
    rows.sort(key=lambda r: -(r.get("impressions") or 0))
    return [{"query": r["query"], "page": r.get("page"), "clicks": r.get("clicks"),
             "impressions": r.get("impressions"), "position": r.get("position")} for r in rows[:limit]]


def site_profile(project: Any, niche: Any | None, store: Any, settings: LLMSettings) -> dict[str, Any]:
    """Who the site is: title, description, the niche being judged (or all niches) and, when there is
    Search Console data, the queries people find it with."""
    prof = getattr(project, "profile", None) or {}
    p: dict[str, Any] = {"title": prof.get("title") or project.name, "description": prof.get("description") or ""}
    niches = [niche] if niche is not None else list(project.niches)

    def nd(n: Any) -> dict[str, Any]:
        return {"name": n.name, "keywords": n.keywords[:15], "entities": n.entities[:10],
                "formats": n.formats[:6], "exclude": n.exclude[:8]}

    if niche is not None:
        p["niche"] = nd(niche)
    else:
        p["niches"] = [nd(n) for n in niches]
    if store is not None:
        q = gsc_queries(store, project.name)
        if q:
            p["top_queries"] = q
        if settings.send_gsc_rows:
            rows = gsc_rows_payload(store, project.name)
            if rows:
                p["gsc_rows"] = rows
    return p


def valid_ids(known: Iterable[int]):
    known = set(known)

    def check(items: Any, key: str = "id") -> str | None:
        ids = [it.get(key) for it in items if isinstance(it, dict)]
        bad = [i for i in ids if i not in known]
        if bad:
            return f"unknown ids {bad[:5]}; use only {sorted(known)[:20]}"
        if len(set(ids)) != len(ids):
            return "an id appears twice"
        return None

    return check

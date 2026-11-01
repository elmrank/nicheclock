"""Task ``merge``: group topics that are the same story across sources."""
from __future__ import annotations

from typing import Any, Sequence

from nicheclock.llm import prompts
from nicheclock.llm.errors import InvalidOutput
from nicheclock.llm.session import LLMSession
from nicheclock.llm.tasks.common import CACHE_DAYS, TaskReport, digest, dumps
from nicheclock.llm.types import messages_of
from nicheclock.models import Topic

TASK = "merge"
MAX_TOPICS = 60
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["groups"],
    "properties": {"groups": {"type": "array", "items": {
        "type": "array", "minItems": 2, "items": {"type": "integer"}}}},
}


def _check(n: int):
    def check(j: Any) -> str | None:
        seen: set[int] = set()
        for g in j["groups"]:
            for i in g:
                if not 1 <= i <= n:
                    return f"id {i} is not one of 1..{n}"
                if i in seen:
                    return f"id {i} is in two groups"
                seen.add(i)
        return None
    return check


def groups(session: LLMSession, topics: Sequence[Topic], report: TaskReport) -> list[list[Topic]]:
    """Groups of 2+ topics (same niche) that tell the same story. Titles and sources only."""
    topics = list(topics)[:MAX_TOPICS]
    if len(topics) < 2:
        return []
    prompt = prompts.load(TASK)
    ck = digest(prompt.version, session.provider.model, sorted(t.key for t in topics))
    hit = session.store.llm_cache_get(session.project, TASK, ck, CACHE_DAYS)
    if hit is not None:
        by_key = {t.key: t for t in topics}
        report.cached += 1
        return [[by_key[k] for k in g if k in by_key] for g in hit if sum(k in by_key for k in g) >= 2]
    user = dumps({"topics": [{"id": i, "title": t.label, "sources": t.sources} for i, t in enumerate(topics, 1)]})
    try:
        res = session.call(TASK, messages_of(prompt.text, user), SCHEMA, max_tokens=1000, check=_check(len(topics)))
    except InvalidOutput as e:
        report.errors.append(f"merge: skipped: {e}")
        return []
    report.judged += 1
    out = [[topics[i - 1] for i in g] for g in res.json["groups"]]
    session.store.llm_cache_put(session.project, TASK, ck, [[t.key for t in g] for g in out])
    return out

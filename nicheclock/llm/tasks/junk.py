"""Task ``junk``: flag ephemeral, spam or irrelevant topics."""
from __future__ import annotations

from typing import Any, Sequence

from nicheclock.llm import prompts
from nicheclock.llm.errors import InvalidOutput
from nicheclock.llm.session import LLMSession
from nicheclock.llm.tasks.common import (CACHE_DAYS, TaskReport, chunks, digest, dumps, snippets,
                                         topic_payload, valid_ids)
from nicheclock.llm.types import messages_of
from nicheclock.models import Topic

TASK = "junk"
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["flagged"],
    "properties": {"flagged": {"type": "array", "items": {
        "type": "object", "required": ["id", "reason"],
        "properties": {"id": {"type": "integer"},
                       "reason": {"type": "string", "minLength": 1, "maxLength": 300}}}}},
}


def flag(session: LLMSession, profile: dict[str, Any], topics: Sequence[Topic],
         report: TaskReport) -> dict[str, str]:
    """``{topic.key: reason}`` for topics flagged as junk."""
    prompt = prompts.load(TASK)
    phash = digest(profile)
    model = session.provider.model
    out: dict[str, str] = {}
    todo: list[tuple[Topic, str]] = []
    for t in topics:
        ck = digest(prompt.version, model, phash, t.label, snippets(t))
        hit = session.store.llm_cache_get(session.project, TASK, ck, CACHE_DAYS)
        if hit is not None:
            if hit.get("junk"):
                out[t.key] = str(hit["reason"])
            report.cached += 1
        else:
            todo.append((t, ck))
    for batch in chunks(todo):
        user = dumps({"site": profile, "topics": [topic_payload(i, t, evidence=False)
                                                  for i, (t, _) in enumerate(batch, 1)]})
        check = valid_ids(range(1, len(batch) + 1))
        try:
            res = session.call(TASK, messages_of(prompt.text, user), SCHEMA, max_tokens=1000,
                               check=lambda j: check(j["flagged"]))
        except InvalidOutput as e:
            report.errors.append(f"junk: skipped a batch of {len(batch)}: {e}")
            continue
        flagged = {it["id"]: " ".join(str(it["reason"]).split()) for it in res.json["flagged"]}
        for i, (t, ck) in enumerate(batch, 1):
            if i in flagged:
                out[t.key] = flagged[i]
                session.store.llm_cache_put(session.project, TASK, ck, {"junk": True, "reason": flagged[i]})
            else:
                session.store.llm_cache_put(session.project, TASK, ck, {"junk": False})
            report.judged += 1
    return out

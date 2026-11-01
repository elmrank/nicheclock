"""Task ``fit``: 0-100 niche fit and a one-sentence reason per candidate topic."""
from __future__ import annotations

from typing import Any, Sequence

from nicheclock.llm import prompts
from nicheclock.llm.errors import InvalidOutput
from nicheclock.llm.session import LLMSession
from nicheclock.llm.tasks.common import (CACHE_DAYS, TaskReport, chunks, digest, dumps, snippets,
                                         topic_payload, valid_ids)
from nicheclock.llm.types import messages_of
from nicheclock.models import Topic

TASK = "fit"
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "required": ["id", "fit", "reason"],
        "properties": {"id": {"type": "integer"},
                       "fit": {"type": "number", "minimum": 0, "maximum": 100},
                       "reason": {"type": "string", "minLength": 1, "maxLength": 400}}}}},
}


def judge(session: LLMSession, profile: dict[str, Any], topics: Sequence[Topic],
          report: TaskReport) -> dict[str, tuple[float, str]]:
    """``{topic.key: (fit, reason)}`` for the topics the model (or the cache) judged. Provider outages
    and budget stops propagate; a malformed batch is skipped and noted."""
    prompt = prompts.load(TASK)
    phash = digest(profile)
    model = session.provider.model
    out: dict[str, tuple[float, str]] = {}
    todo: list[tuple[Topic, str]] = []
    for t in topics:
        ck = digest(prompt.version, model, phash, t.label, snippets(t))
        hit = session.store.llm_cache_get(session.project, TASK, ck, CACHE_DAYS)
        if hit:
            out[t.key] = (float(hit["fit"]), str(hit["reason"]))
            report.cached += 1
        else:
            todo.append((t, ck))
    for batch in chunks(todo):
        payload = [topic_payload(i, t) for i, (t, _) in enumerate(batch, 1)]
        user = dumps({"site": profile, "topics": payload})
        check = valid_ids(range(1, len(batch) + 1))
        try:
            res = session.call(TASK, messages_of(prompt.text, user), SCHEMA,
                               max_tokens=max(1500, 110 * len(batch)),
                               check=lambda j: check(j["items"]) or (None if j["items"] else "no items"))
        except InvalidOutput as e:
            report.errors.append(f"fit: skipped a batch of {len(batch)}: {e}")
            continue
        for it in res.json["items"]:
            t, ck = batch[it["id"] - 1]
            fit, reason = round(float(it["fit"]), 1), " ".join(str(it["reason"]).split())
            out[t.key] = (fit, reason)
            session.store.llm_cache_put(session.project, TASK, ck, {"fit": fit, "reason": reason})
            report.judged += 1
    return out


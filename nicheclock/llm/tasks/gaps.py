"""Task ``gaps``: an article angle and 2-3 reader questions for each Search Console gap query."""
from __future__ import annotations

from typing import Any, Sequence

from nicheclock.llm import prompts
from nicheclock.llm.errors import InvalidOutput
from nicheclock.llm.session import LLMSession
from nicheclock.llm.tasks.common import CACHE_DAYS, TaskReport, chunks, digest, dumps, valid_ids
from nicheclock.llm.types import messages_of
from nicheclock.models import FeedItem

TASK = "gaps"
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "required": ["id", "angle", "questions"],
        "properties": {"id": {"type": "integer"},
                       "angle": {"type": "string", "minLength": 1, "maxLength": 400},
                       "questions": {"type": "array", "minItems": 1, "maxItems": 4,
                                     "items": {"type": "string", "minLength": 1, "maxLength": 200}}}}}},
}


def _payload(i: int, g: FeedItem, rows: bool) -> dict[str, Any]:
    d: dict[str, Any] = {"id": i, "query": g.title}
    if rows:                     # raw Search Console data, only with send_gsc_rows: true
        d.update({"why": g.why, "page": g.url})
    return d


def advise(session: LLMSession, profile: dict[str, Any], gaps: Sequence[FeedItem],
           report: TaskReport) -> dict[str, tuple[str, list[str]]]:
    """``{gap query: (angle, questions)}``."""
    prompt = prompts.load(TASK)
    rows = session.settings.send_gsc_rows
    phash = digest(profile)
    out: dict[str, tuple[str, list[str]]] = {}
    todo: list[tuple[FeedItem, str]] = []
    for g in gaps:
        ck = digest(prompt.version, session.provider.model, phash, g.title, rows)
        hit = session.store.llm_cache_get(session.project, TASK, ck, CACHE_DAYS)
        if hit:
            out[g.title] = (hit["angle"], list(hit["questions"]))
            report.cached += 1
        else:
            todo.append((g, ck))
    for batch in chunks(todo):
        user = dumps({"site": profile, "gaps": [_payload(i, g, rows) for i, (g, _) in enumerate(batch, 1)]})
        check = valid_ids(range(1, len(batch) + 1))
        try:
            res = session.call(TASK, messages_of(prompt.text, user), SCHEMA,
                               max_tokens=max(1500, 200 * len(batch)), check=lambda j: check(j["items"]))
        except InvalidOutput as e:
            report.errors.append(f"gaps: skipped a batch of {len(batch)}: {e}")
            continue
        for it in res.json["items"]:
            g, ck = batch[it["id"] - 1]
            angle = " ".join(str(it["angle"]).split())
            qs = [" ".join(str(q).split()) for q in it["questions"]][:3]
            out[g.title] = (angle, qs)
            session.store.llm_cache_put(session.project, TASK, ck, {"angle": angle, "questions": qs})
            report.judged += 1
    return out

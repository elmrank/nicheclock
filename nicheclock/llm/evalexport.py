"""``nicheclock eval export``: rule-based vs model ranking side by side, for a manager to rate.

One CSV row per rank: the topic the rule-based ranking puts there, and the topic the model-judged
feed puts there, each with a blank ``usable?`` column to fill in (y / n). Topic ids are
``<niche>/<key>``: pass the title to the MCP tool ``explain_topic`` to see the evidence.
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

import csv
from datetime import timedelta
from pathlib import Path
from typing import Any

from nicheclock.models import Topic, utcnow

COLUMNS = ["rank", "rule_id", "rule_title", "rule_niche", "rule_score", "rule_why", "rule_usable?",
           "llm_id", "llm_title", "llm_niche", "llm_score", "llm_fit", "llm_reason", "llm_usable?"]


class EvalError(ValueError):
    pass


def _cell(v: Any) -> Any:
    """Spreadsheet-safe: a text cell must not start a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _id(t: Topic) -> str:
    return f"{t.niche or ''}/{t.key}"


def rankings(store: Any, project: str, days: int, top: int) -> tuple[list[Topic], list[Topic]]:
    """(rule-based top-N, model-judged top-N) from the topics stored in the last ``days`` days."""
    topics = store.topics(project, since=utcnow() - timedelta(days=days), include_hidden=True)
    if not any(t.llm_fit is not None for t in topics):
        raise EvalError(_t("llm.eval.no_scores", project=project, days=days))
    rule = sorted(topics, key=lambda t: (-(t.rule_score if t.rule_score is not None else t.score), t.label))
    llm = sorted((t for t in topics if t.llm_status is None), key=lambda t: (-t.score, t.label))
    return rule[:top], llm[:top]


def export(store: Any, project: str, days: int, top: int, path: Path) -> Path:
    rule, llm = rankings(store, project, days, top)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for i in range(max(len(rule), len(llm))):
            r = rule[i] if i < len(rule) else None
            m = llm[i] if i < len(llm) else None
            w.writerow([i + 1] + (
                [_cell(_id(r)), _cell(r.label), _cell(r.niche), r.rule_score if r.rule_score is not None else r.score,
                 _cell(r.why), ""] if r else [""] * 6) + (
                [_cell(_id(m)), _cell(m.label), _cell(m.niche), m.score, m.llm_fit if m.llm_fit is not None else "",
                 _cell(m.llm_reason or ""), ""] if m else [""] * 7))
    return path

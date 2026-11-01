"""Run the optional model tasks over a run's topics and gaps; the rule-based result is the fallback.

Order per run (cheap and safe first, so the budget goes to topics worth judging):
  candidates = the top ``max_items_per_run`` topics by rule score, over all niches of the project
  1. junk  - flagged topics leave the feed (kept in the store with ``llm_status='junk'``)
  2. merge - topics that are the same story become one (the best-scored absorbs the others' signals)
  3. fit   - 0-100 fit + reason; the feed score becomes the blend (see ``blend``)
  4. gaps  - angle + reader questions for Search Console gaps
A budget stop, a provider outage or a malformed reply ends or skips that task only; whatever was
judged before stays, the rest keeps its rule-based score.
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from nicheclock.cluster import score_topic
from nicheclock.llm.denylist import NOT_SENT, Denylist
from nicheclock.llm.errors import BudgetExceeded, LLMError
from nicheclock.llm.session import LLMSession
from nicheclock.llm.tasks import fit as fit_task
from nicheclock.llm.tasks import gaps as gaps_task
from nicheclock.llm.tasks import junk as junk_task
from nicheclock.llm.tasks import merge as merge_task
from nicheclock.llm.tasks.common import TaskReport, site_profile
from nicheclock.models import FeedItem, Topic, utcnow

log = logging.getLogger("nicheclock.llm")
UNJUDGED_FIT = 50.0     # neutral prior for topics the model did not score, so scores stay comparable


@dataclass
class EnrichReport:
    judged: int = 0
    cached: int = 0
    junk: int = 0
    merged: int = 0
    gaps: int = 0
    blocked: int = 0          # topics kept out of every prompt by the source denylist
    errors: list[str] = field(default_factory=list)   # failures: reported like a failing source
    notes: list[str] = field(default_factory=list)    # not failures ("llm budget reached")


def blend(rule: float, fit: float, weight: float) -> float:
    """Feed score = ``(1 - weight) * rule_score + weight * llm_fit`` (both 0-100); ``weight`` is
    ``llm.fit_weight`` (default 0.6). Topics the model did not score use a neutral fit of 50."""
    return round((1.0 - weight) * rule + weight * fit, 1)


def _candidates(niche_topics: dict[str, list[Topic]], limit: int, blocked: set[int]) -> set[int]:
    allt = [t for ts in niche_topics.values() for t in ts if id(t) not in blocked]
    allt.sort(key=lambda t: (-t.score, t.label))
    return {id(t) for t in allt[:limit]}


def _merge_into(primary: Topic, others: Sequence[Topic], now: Any) -> None:
    seen = {(s.source, s.term, s.url) for s in primary.signals}
    for o in others:
        for s in o.signals:
            if (s.source, s.term, s.url) not in seen:
                seen.add((s.source, s.term, s.url))
                primary.signals.append(s)
    score_topic(primary, now)
    primary.rule_score = primary.score
    primary.llm_note = "also reported as: " + "; ".join(o.label for o in others)
    primary.why += f" (merged: {'; '.join(o.label for o in others)})"[:300]
    for o in others:
        o.llm_status, o.llm_note = "merged", f"merged into {primary.label}"


def enrich_topics(session: LLMSession, project: Any, niche_topics: dict[str, list[Topic]],
                  now: Any = None) -> EnrichReport:
    """Mutates the topics in place (score blend, llm_* fields, hidden statuses)."""
    cfg = session.settings
    rep = EnrichReport()
    now = now or utcnow()
    for ts in niche_topics.values():
        for t in ts:
            t.rule_score = t.score
    niches = {n.name: n for n in project.niches}
    deny = Denylist(cfg.denylist)
    blocked: set[int] = set()
    for name, ts in niche_topics.items():       # source terms forbid AI use: these never reach a prompt
        off = niches[name].llm_off_feeds if name in niches else []
        for t in ts:
            if deny.reason(t, off):
                t.llm_note = NOT_SENT
                blocked.add(id(t))
    rep.blocked = len(blocked)
    cand = _candidates(niche_topics, cfg.max_items_per_run, blocked)
    by_niche = {n: [t for t in ts if id(t) in cand] for n, ts in niche_topics.items()}
    profiles = {n: site_profile(project, niches.get(n), session.store, cfg) for n in by_niche}
    judged_any = False

    def run(task: str, fn) -> None:
        if task not in cfg.tasks or session.budget_reached:
            return
        for niche, ts in by_niche.items():
            ts = [t for t in ts if t.llm_status is None]
            if not ts:
                continue
            tr = TaskReport()
            try:
                fn(niche, ts, tr)
            except BudgetExceeded as e:
                rep.notes.append(str(e))
                return
            except LLMError as e:
                rep.errors.append(_t("llm.task_error", task=task, error=e))
                return
            finally:
                rep.judged += tr.judged
                rep.cached += tr.cached
                rep.errors += [f"llm {e}" for e in tr.errors]

    def do_junk(niche: str, ts: list[Topic], tr: TaskReport) -> None:
        flagged = junk_task.flag(session, profiles[niche], ts, tr)
        for t in ts:
            if t.key in flagged:
                t.llm_status, t.llm_note = "junk", flagged[t.key]
                rep.junk += 1

    def do_merge(niche: str, ts: list[Topic], tr: TaskReport) -> None:
        for grp in merge_task.groups(session, ts, tr):
            grp = [t for t in grp if t.llm_status is None]
            if len(grp) < 2:
                continue
            grp.sort(key=lambda t: (-t.score, t.label))
            _merge_into(grp[0], grp[1:], now)
            rep.merged += len(grp) - 1

    def do_fit(niche: str, ts: list[Topic], tr: TaskReport) -> None:
        nonlocal judged_any
        for key, (fit, reason) in fit_task.judge(session, profiles[niche], ts, tr).items():
            for t in ts:
                if t.key == key:
                    t.llm_fit, t.llm_reason = fit, reason
                    judged_any = True

    run("junk", do_junk)
    run("merge", do_merge)
    run("fit", do_fit)
    if judged_any:
        for ts in niche_topics.values():
            for t in ts:
                fit = t.llm_fit if t.llm_fit is not None else UNJUDGED_FIT
                t.score = blend(t.rule_score if t.rule_score is not None else t.score, fit, cfg.fit_weight)
    if session.budget_reached and not rep.notes:
        rep.notes.append(_t("llm.budget_reached_note"))
    return rep


def enrich_gaps(session: LLMSession, project: Any, gaps: list[FeedItem], rep: EnrichReport) -> None:
    """Fills ``angle`` / ``questions`` of the first ``max_items_per_run`` gaps."""
    cfg = session.settings
    if "gaps" not in cfg.tasks or not gaps or session.budget_reached:
        return
    tr = TaskReport()
    try:
        got = gaps_task.advise(session, site_profile(project, None, session.store, cfg),
                               gaps[: cfg.max_items_per_run], tr)
    except BudgetExceeded as e:
        rep.notes.append(str(e))
        return
    except LLMError as e:
        rep.errors.append(_t("llm.task_error", task="gaps", error=e))
        return
    finally:
        rep.judged += tr.judged
        rep.cached += tr.cached
        rep.errors += [f"llm {e}" for e in tr.errors]
    for g in gaps:
        if g.title in got:
            g.angle, g.questions = got[g.title]
            rep.gaps += 1

"""stdio MCP server exposing the radar to agents (``nicheclock mcp``).

Tools (read-only; they read the SQLite store and, as a fallback, ``out/<project>/feed.json``):

* ``trends_for_niche(project, niche, days=7)`` - topics scored in the last ``days`` days
* ``upcoming(project, days=30)`` - dated events with their ``publish_by``
* ``gsc_gaps(project, limit=50)`` - Search Console gaps from the last sync
* ``explain_topic(project, topic, niche=None)`` - the evidence for one topic, for YOUR model to judge

Topics carry the configured model's niche-fit score and reason (``fit``, ``fit_reason``) when a run had
one; gaps carry ``angle`` and ``reader_questions``. ``explain_topic`` makes no model call: the client's
own model does the judging, which is also how the hosted service works.

The server never fetches anything, never calls an LLM and never writes. Only stdout carries
the protocol, so nothing here prints.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError as _SdkToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError as _SdkToolError  # type: ignore[no-redef]

from nicheclock import gsc
from nicheclock.i18n import t as tr
from nicheclock import regions as rg
from nicheclock.llm.denylist import NOT_SENT, for_llm_config
from nicheclock.store import Store

SERVER_NAME = "nicheclock"


class ToolError(ValueError):
    """Bad arguments; the message tells the agent what values are valid."""


# ---- data access -------------------------------------------------------------------------

def _project(cfg: Any, name: str):
    names = [p.name for p in cfg.projects]
    for p in cfg.projects:
        if p.name == name:
            return p
    if not name and len(cfg.projects) == 1:
        return cfg.projects[0]
    raise ToolError(tr("mcp.unknown_project", name=name, names=names))


def _open_store(cfg: Any) -> Store | None:
    """The existing store, or None (never creates a database from a read-only server)."""
    return Store.open(cfg) if (Path(cfg.data_dir) / "nicheclock.db").is_file() else None


def _feed_json(cfg: Any, project: str) -> dict[str, Any]:
    try:
        return json.loads((Path(cfg.out_dir) / project / "feed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _fit(fit: Any, reason: Any, rule: Any) -> dict[str, Any]:
    """The model's judgement of a topic; empty when no model scored it."""
    if fit is None:
        return {}
    return {"fit": fit, "fit_reason": reason, "rule_score": rule}


def _days(value: int, lo: int, hi: int, what: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ToolError(tr("mcp.int_range", what=what, lo=lo, hi=hi)) from None
    if not lo <= v <= hi:
        raise ToolError(tr("mcp.out_of_range", what=what, lo=lo, hi=hi, value=v))
    return v


# ---- tool implementations (pure functions of cfg; tested directly) ----------------------

def _region(value: str | None) -> str | None:
    """Validated optional ``region`` argument: 'global' / empty = no filter, else a country code."""
    if value is None or str(value).strip() == "" or str(value).strip().lower() == rg.GLOBAL:
        return None
    if rg.invalid([value]):
        raise ToolError(tr("mcp.region_invalid", region=value))
    return str(value).strip().upper()


def _region_fields(region: str | None, precision: str | None, regions: list[str] | None) -> dict[str, Any]:
    return {"region": region or rg.GLOBAL, "region_precision": precision or "global", "regions": regions or []}


def trends_for_niche(cfg: Any, project: str, niche: str, days: int = 7,
                     region: str | None = None) -> list[dict[str, Any]]:
    """Topics of a niche. ``region`` (a country code) keeps that country's topics plus the global ones."""
    proj = _project(cfg, project)
    days = _days(days, 1, 365, "days")
    region = _region(region)
    niches = [n.name for n in proj.niches]
    match = next((n for n in niches if n.lower() == (niche or "").strip().lower()), None)
    if match is None:
        raise ToolError(tr("mcp.unknown_niche", niche=niche, project=proj.name, niches=niches))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    store = _open_store(cfg)
    if store is not None:
        with store:
            for t in store.topics(proj.name, match, since=since, region=region):
                out.append({
                    "niche": t.niche, "title": t.label, "score": round(t.score, 2), "why": t.why,
                    "url": next((s.url for s in t.signals if s.url), None), "sources": t.sources,
                    **_region_fields(t.region, t.region_precision, t.regions),
                    **_fit(t.llm_fit, t.llm_reason, t.rule_score),
                    **({"llm_note": NOT_SENT} if t.llm_note == NOT_SENT else {}),
                })
    if not out:  # no store yet: fall back to the last rendered feed
        for it in _feed_json(cfg, proj.name).get("items", []):
            if it.get("kind") == "topic" and (it.get("niche") or "").lower() == match.lower() \
                    and rg.keeps(it.get("region"), region, it.get("regions")):
                out.append({"niche": it["niche"], "title": it["title"], "score": it.get("score"),
                            "why": it.get("why", ""), "url": it.get("url"), "sources": [],
                            **_region_fields(it.get("region"), it.get("region_precision"), it.get("regions")),
                            **_fit(it.get("llm_fit"), it.get("llm_reason"), it.get("rule_score")),
                            **({"llm_note": it["llm_note"]} if it.get("llm_note") else {})})
        out.sort(key=lambda d: -(d["score"] or 0))
    return out


def upcoming(cfg: Any, project: str, days: int = 30, region: str | None = None) -> list[dict[str, Any]]:
    """Dated events. ``region`` (a country code) keeps that country's events plus the global ones."""
    proj = _project(cfg, project)
    days = _days(days, 1, 730, "days")
    region = _region(region)
    today = date.today()
    rows: list[dict[str, Any]] = []
    store = _open_store(cfg)
    if store is not None:
        with store:
            rows = [e.to_dict() for e in store.events(proj.name, since=today, until=today + timedelta(days=days),
                                                      region=region)]
    if not rows:
        horizon = (today + timedelta(days=days)).isoformat()
        rows = [e for e in _feed_json(cfg, proj.name).get("upcoming", [])
                if today.isoformat() <= (e.get("date") or "") <= horizon and rg.keeps(e.get("region"), region)]
        rows.sort(key=lambda e: (e.get("date") or "", e.get("title") or ""))
    for e in rows:
        e["days_until"] = (date.fromisoformat(e["date"]) - today).days
        pb = e.get("publish_by")
        e["publish_by_passed"] = bool(pb and date.fromisoformat(pb) < today)
    return rows


def gsc_gaps(cfg: Any, project: str, limit: int = 50) -> list[dict[str, Any]]:
    proj = _project(cfg, project)
    limit = _days(limit, 1, 500, "limit")
    feed_gaps = {g["title"]: g for g in _feed_json(cfg, proj.name).get("gaps", [])}
    store = _open_store(cfg)
    if store is not None:
        with store:
            found = gsc.gaps(proj.name, store, limit=limit)
        if found:
            return [{**g, **_advice(feed_gaps.get(g.get("query")))} for g in found]
    return [{"query": g["title"], "page": g.get("url"), "why": g.get("why", ""), "score": g.get("score"),
             **_advice(g)} for g in list(feed_gaps.values())[:limit]]


def _advice(g: dict[str, Any] | None) -> dict[str, Any]:
    if not g or not g.get("angle"):
        return {}
    return {"angle": g["angle"], "reader_questions": g.get("questions") or []}


JUDGE_PROMPT = tr("mcp.judge_prompt")


def _norm(s: str) -> str:
    return " ".join((s or "").casefold().split())


def explain_topic(cfg: Any, project: str, topic: str, niche: str | None = None) -> dict[str, Any]:
    """Evidence for one stored topic so the CALLER's model can judge its niche fit. No model call here.
    Topics from sources whose terms forbid AI use (``nicheclock.llm.denylist``) return a notice instead."""
    proj = _project(cfg, project)
    want = _norm(topic)
    if not want:
        raise ToolError(tr("mcp.topic_required"))
    store = _open_store(cfg)
    if store is None:
        raise ToolError(tr("mcp.no_data", project=proj.name))
    with store:
        pool = store.topics(proj.name, since=datetime.now(timezone.utc) - timedelta(days=30),
                            include_hidden=True)
        top_queries = _top_queries(store, proj.name)
    if niche:
        pool = [t for t in pool if _norm(t.niche or "") == _norm(niche)]
    hits = [t for t in pool if want in (_norm(t.label), _norm(t.key))]
    if not hits:
        hits = [t for t in pool if want in _norm(t.label)]
    if not hits:
        raise ToolError(tr("mcp.no_topic", topic=topic, titles=[x.label for x in pool[:8]]))
    if len({(t.niche, t.key) for t in hits}) > 1:
        raise ToolError(tr("mcp.ambiguous_topic", topic=topic,
                          matches=[f"{x.niche}: {x.label}" for x in hits[:8]]))
    t = hits[0]
    n = proj.niche(t.niche or "")
    prof = getattr(proj, "profile", {}) or {}
    if for_llm_config(getattr(cfg, "llm", None)).reason(t, n.llm_off_feeds if n else []):
        # the source's terms forbid AI use: the caller's model gets no content of it either
        return {"project": proj.name, "niche": t.niche, "title": t.label, "key": t.key, "score": round(t.score, 2),
                "sources": t.sources, "llm_note": NOT_SENT,
                "note": tr("mcp.withheld_note")}
    out: dict[str, Any] = {
        "project": proj.name, "niche": t.niche, "title": t.label, "key": t.key,
        "score": round(t.score, 2), "why": t.why, "sources": t.sources,
        **_region_fields(t.region, t.region_precision, t.regions),
        "status": t.llm_status or "visible", "status_note": t.llm_note,
        "signals": [_signal(s) for s in t.signals[:12]],
        "site": {"title": prof.get("title") or proj.name, "description": prof.get("description", ""),
                 "urls": proj.urls},
        "niche_profile": ({"name": n.name, "keywords": n.keywords, "entities": n.entities,
                           "formats": n.formats, "exclude": n.exclude} if n else None),
        "top_search_queries": top_queries,
        "judge_with_your_model": JUDGE_PROMPT,
    }
    out.update(_fit(t.llm_fit, t.llm_reason, t.rule_score))
    return out


def _top_queries(store: Store, project: str, limit: int = 15) -> list[str]:
    imp: dict[str, float] = {}
    for r in store.gsc_rows(project):
        if r.get("query") and r.get("window") in (None, "cur"):
            imp[str(r["query"])] = imp.get(str(r["query"]), 0.0) + float(r.get("impressions") or 0)
    return [q for q, _ in sorted(imp.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def _signal(s: Any) -> dict[str, Any]:
    extra = {k: v for k, v in s.extra.items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 300}
    return {"source": s.source, "term": s.term, "url": s.url, "rank": s.rank, "score": s.score,
            "seen_at": s.seen_at.isoformat(), "region": s.region, "region_precision": s.region_precision,
            "extra": extra}


# ---- server ------------------------------------------------------------------------------

def build_server(cfg: Any):
    """An MCP server bound to ``cfg`` (tools take ``project`` by name)."""
    server = _Server(SERVER_NAME, instructions=tr("mcp.instructions"))

    def guarded(fn, *args):
        try:
            return fn(cfg, *args)
        except ToolError as e:  # anticipated bad input: tell the model what is valid
            raise _SdkToolError(str(e)) from None

    @server.tool(name="trends_for_niche", description=tr("mcp.tool.trends"))
    def _trends(project: str, niche: str, days: int = 7, region: str | None = None) -> list[dict]:
        return guarded(trends_for_niche, project, niche, days, region)

    @server.tool(name="upcoming", description=tr("mcp.tool.upcoming"))
    def _upcoming(project: str, days: int = 30, region: str | None = None) -> list[dict]:
        return guarded(upcoming, project, days, region)

    @server.tool(name="gsc_gaps", description=tr("mcp.tool.gaps"))
    def _gaps(project: str, limit: int = 50) -> list[dict]:
        return guarded(gsc_gaps, project, limit)

    @server.tool(name="explain_topic", description=tr("mcp.tool.explain"))
    def _explain(project: str, topic: str, niche: str | None = None) -> dict:
        return guarded(explain_topic, project, topic, niche)

    return server


# ---- CLI ---------------------------------------------------------------------------------

def _cmd_mcp(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for

    build_server(load_for(args)).run(transport="stdio")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("mcp", help=tr("mcp.cmd_help"))
    p.set_defaults(func=_cmd_mcp)

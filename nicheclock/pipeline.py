"""``run``: collect -> store -> match -> cluster -> score -> upcoming -> outputs -> notify."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from nicheclock import regions as rg
from nicheclock.i18n import t as tr
from nicheclock import upcoming as upc
from nicheclock.cluster import cluster, passes_floor, rank_topics
from nicheclock.config import Config, Niche, Project
from nicheclock.llm.denylist import NOT_SENT
from nicheclock.llm.enrich import enrich_gaps, enrich_topics
from nicheclock.match import relevant
from nicheclock.models import Event, FeedItem, Signal, Topic, utcnow
from nicheclock.output import Feed, NicheMeta, write_all
from nicheclock.sources import attribution, load
from nicheclock.sources._common import opt

WINDOW_DAYS = 3          # topics are built from signals seen in the last 3 days
FEED_LIMIT = 30          # topics kept per niche in the feed
NOTIFY_TOPICS = 10       # per niche handed to notifications
UPCOMING_DAYS = 120


@dataclass
class ProjectResult:
    project: str
    feed: Feed
    counts: dict[str, dict[str, int]] = field(default_factory=dict)   # niche -> source -> signals kept
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    files: dict[str, Any] = field(default_factory=dict)
    notified: Any = None
    notes: list[str] = field(default_factory=list)       # not failures, e.g. "llm budget reached"
    llm: dict[str, Any] | None = None                    # model usage of the run (None: no model)

    def niche_items(self, niche: str) -> int:
        return sum(1 for i in self.feed.items if i.niche == niche)


def _collect_niche(client: Any, niche: Niche, day: date, errors: list[str],
                   regions: list[str] | None = None) -> tuple[list[Signal], dict[str, int]]:
    raw: list[Signal] = []
    raw_counts: dict[str, int] = {}
    for name in niche.sources:
        try:
            got = load(name).collect(client, niche, day, regions)
        except Exception as e:     # a failing source is reported, never fatal
            errors.append(f"{niche.name}/{name}: {type(e).__name__}: {e}")
            continue
        raw_counts[name] = len(got)
        raw += got
    return raw, raw_counts


def _refresh_gsc(cfg: Config, project: Project, store: Any, errors: list[str]) -> None:
    """Sync Search Console (only if `connect gsc` was done; integrations decides when to resync)."""
    if not project.gsc:
        return
    try:
        gsc = importlib.import_module("nicheclock.gsc")
    except ModuleNotFoundError:
        return
    refresh = getattr(gsc, "refresh", None)
    if refresh is None:
        return
    try:
        res = refresh(project, store, cfg)
    except Exception as e:
        errors.append(f"gsc: {type(e).__name__}: {e}")
        return
    if isinstance(res, dict) and res.get("error"):
        errors.append(f"gsc: {res['error']}")


def _gaps(project: Project, store: Any, limit: int = 50) -> list[FeedItem]:
    if not project.gsc:
        return []
    try:
        gsc = importlib.import_module("nicheclock.gsc")
    except ModuleNotFoundError:
        return []
    fn: Callable | None = getattr(gsc, "gaps", None)
    if fn is None:
        return []
    out = []
    for g in fn(project.name, store, limit=limit):
        why = g.get("why") or "{:.0f} impressions, position {:.1f}".format(
            g.get("impressions") or 0, g.get("position") or 0)
        out.append(FeedItem(project.name, None, "gap", g["query"], g.get("page"), why,
                            float(g.get("score") or g.get("impressions") or 0)))
    return out


def _regions_for(project: Project, niche: Niche, only: str | None) -> tuple[list[str], str]:
    """(regions, mode) a niche is read for; ``only`` (run --region XX) narrows it to one country."""
    regs, mode = rg.effective(project, niche)
    return ([only.upper()], mode) if only else (regs, mode)


def run_project(cfg: Config, project: Project, *, client: Any, store: Any, day: date | None = None,
                notify: bool = True, log: Callable[[str], None] = lambda m: None,
                region: str | None = None) -> ProjectResult:
    t0 = time.monotonic()
    now = utcnow()
    day = day or now.date()
    errors: list[str] = []
    run_id = store.start_run(project.name)
    feed = Feed(project=project.name, urls=list(project.urls), generated_at=now)
    _refresh_gsc(cfg, project, store, errors)
    lag = upc.lag_days(cfg, store, project.name)
    counts: dict[str, dict[str, int]] = {}
    if any("gdelt" in n.sources for n in project.niches):
        try:
            note = load("gdelt").prime(client, project.niches, day, {
                n.name: _regions_for(project, n, region)[0] for n in project.niches})
            if note:
                errors.append(note)
        except Exception as e:
            errors.append(f"gdelt: {type(e).__name__}: {e}")
    used_sources: set[str] = set()
    all_events: list[Event] = []
    niche_topics: dict[str, list[Topic]] = {}

    for niche in project.niches:
        log(f"  niche {niche.name!r}")
        regs, mode = _regions_for(project, niche, region)
        feed.niches.append(NicheMeta(niche.name, niche.keywords, niche.entities, niche.formats, regs, mode))
        raw, raw_counts = _collect_niche(client, niche, day, errors, regs)
        drain = getattr(client, "drain_failures", None)
        if drain:
            errors += [f"{niche.name}: {f}" for f in drain()]
        kept = relevant(raw, niche)
        low = [s for s in kept if s.extra.get("low_confidence")]
        kept = [s for s in kept if not s.extra.get("low_confidence")]
        if not opt(niche, "show_low_confidence", False):
            for s in low:
                feed.low_confidence.append(FeedItem(
                    project.name, niche.name, "topic", s.term, s.url,
                    tr("feed.low_confidence_abstract", source=s.source,
                      terms=", ".join(s.extra.get("matched", [])[:3])), 0.0, now))
        else:
            kept += low
        per_source: dict[str, int] = {}
        for s in kept:
            per_source[s.source] = per_source.get(s.source, 0) + 1
        counts[niche.name] = per_source
        used_sources |= {s for s, n in per_source.items() if n}
        store.add_signals(project.name, niche.name, kept, run_id)

        window = store.signals_since(project.name, niche.name, now - timedelta(days=WINDOW_DAYS))
        topics = rank_topics([t for t in cluster(window, niche, mode) if passes_floor(t)], now)[:FEED_LIMIT]
        niche_topics[niche.name] = topics

        events = upc.collect(client, niche, day, kept, lag, errors, regs)
        if events:
            used_sources |= {e.source for e in events}
        all_events += events
        log(f"    {sum(per_source.values())} signals kept of {sum(raw_counts.values())} fetched, "
            f"{len(topics)} topics, {len(events)} events")

    session, llm_rep = _open_llm(cfg, project, store, errors)
    if session is not None:
        llm_rep = enrich_topics(session, project, niche_topics, now)
    for niche_name, topics in niche_topics.items():
        store.save_topics(run_id, project.name, niche_name, topics)
        for t in topics:
            if t.llm_status == "merged":
                continue
            if t.llm_status == "junk":
                feed.low_confidence.append(FeedItem(project.name, niche_name, "topic", t.label, _url(t),
                                                    tr("feed.llm_junk", note=t.llm_note), 0.0, now))
                continue
            feed.items.append(FeedItem(project.name, niche_name, "topic", t.label, _url(t), t.why, t.score, now,
                                       region=t.region, region_precision=t.region_precision, regions=t.regions,
                                       rule_score=t.rule_score, llm_fit=t.llm_fit, llm_reason=t.llm_reason,
                                       llm_note=t.llm_note if t.llm_note == NOT_SENT else None,
                                       evidence=_evidence(t)))

    store.save_events(project.name, all_events)
    feed.upcoming = store.events(project.name, since=day, until=day + timedelta(days=UPCOMING_DAYS))
    feed.gaps = _gaps(project, store)
    feed.regions = rg.normalize([r for n in feed.niches for r in n.regions])
    feed.region_mode = rg.effective(project, project.niches[0])[1] if project.niches else "combined"
    if session is not None:
        enrich_gaps(session, project, feed.gaps, llm_rep)
        feed.llm = {**session.summary(), "junk": llm_rep.junk, "merged": llm_rep.merged,
                    "gaps": llm_rep.gaps, "cached": llm_rep.cached, "not_sent": llm_rep.blocked,
                    "fit_weight": session.settings.fit_weight, "notes": llm_rep.notes}
        errors += llm_rep.errors
    attrs = attribution()
    feed.sources = {n: attrs[n] for n in sorted(used_sources) if n in attrs}
    if "wikipedia" in used_sources:
        feed.sources["mediawiki"] = attrs["mediawiki"]
    feed.items.sort(key=lambda i: -i.score)

    result = ProjectResult(project.name, feed, counts, errors, notes=llm_rep.notes if llm_rep else [],
                           llm=feed.llm)
    result.files = write_all(feed, cfg.out_dir)
    if notify and project.notify:
        result.notified = _notify(cfg, project, feed, errors)
    store.finish_run(run_id, {"counts": counts, "errors": errors, "llm": feed.llm,
                              "items": len(feed.items), "upcoming": len(feed.upcoming)},
                     status="ok" if not errors else "partial")
    result.seconds = time.monotonic() - t0
    return result


def _url(t: Topic) -> str | None:
    return next((s.url for s in t.signals if s.url), None)


EVIDENCE_LIMIT = 8


def _evidence(t: Topic) -> list[dict[str, Any]]:
    """The signals behind a topic, newest first, one entry per (source, title): what the report shows when a row is opened."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for s in sorted(t.signals, key=lambda s: s.seen_at, reverse=True):
        if (s.source, s.term) in seen:
            continue
        seen.add((s.source, s.term))
        out.append({"source": s.source, "title": s.term, "url": s.url,
                    "at": s.seen_at.strftime("%Y-%m-%dT%H:%MZ"), "note": _signal_note(s)})
        if len(out) == EVIDENCE_LIMIT:
            break
    return out


def _signal_note(s: Signal) -> str | None:
    """One plain sentence of numbers for a signal, or None when the source has none worth printing."""
    x = s.extra
    if s.source == "wikipedia" and x.get("ratio") is not None and x.get("views_3d") is not None:
        return f"{x['ratio']:.1f}x its 14-day median ({x['views_3d']:,.0f} vs {x['base_14d']:,.0f} views a day)"
    if s.source == "wikipedia" and x.get("views") is not None:
        return f"{x['views']:,} views on the day's most-read list"
    if s.source == "gdelt" and x.get("ratio") is not None:
        return f"news volume {x['ratio']:.1f}x its baseline"
    if s.source == "hn" and s.score is not None:
        return f"{s.score:.0f} points on Hacker News, {x.get('comments') or 0} comments"
    if s.source == "google_trends" and x.get("traffic"):
        return f"{x['traffic']} searches"
    if s.source == "federal_register" and x.get("type"):
        return str(x["type"])
    return None


def _open_llm(cfg: Config, project: Project, store: Any, errors: list[str]) -> tuple[Any, Any]:
    """(session, report) when a model is configured and usable, else (None, None); a problem with the
    model never stops the run, the rule-based ranking stands."""
    if not cfg.llm:
        return None, None
    try:
        from nicheclock.llm import LLMError, open_session
        from nicheclock.llm.enrich import EnrichReport
        return open_session(cfg, store, project.name), EnrichReport()
    except LLMError as e:
        errors.append(tr("pipeline.llm_fallback", error=e))
        return None, None


def _notify(cfg: Config, project: Project, feed: Feed, errors: list[str]) -> Any:
    try:
        mod = importlib.import_module("nicheclock.notify")
    except ModuleNotFoundError:
        errors.append(tr("pipeline.notify_missing"))
        return None
    per_niche: dict[str | None, int] = {}
    items: list[FeedItem] = []
    for it in feed.items:            # already sorted by score
        if per_niche.get(it.niche, 0) < NOTIFY_TOPICS:
            per_niche[it.niche] = per_niche.get(it.niche, 0) + 1
            items.append(it)
    soon = date.today() + timedelta(days=7)
    for e in feed.upcoming:
        if e.publish_by is not None and e.publish_by <= soon:
            items.append(FeedItem(project.name, e.niche, "event", e.title, e.url,
                                  tr("feed.event_why", kind=e.kind, date=e.date.isoformat(), by=e.publish_by.isoformat()),
                                  0.0, feed.generated_at, region=e.region, region_precision=e.region_precision))
    items += feed.gaps[:5]
    try:
        result = mod.dispatch(cfg, project, items)
    except Exception as e:
        errors.append(f"notify: {type(e).__name__}: {e}")
        return None
    for ch in (result or {}).get("channels", []):
        if ch.get("error"):
            errors.append(f"notify {ch.get('channel')}: {ch['error']}")
    return result

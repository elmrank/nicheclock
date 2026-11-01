"""Upcoming calendar: dated events + Wikipedia early risers, each with ``publish_by = date - lag``."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from nicheclock.models import Event, Signal
from nicheclock.sources import load

# An early riser: 3-day average views >= 2x the median of the 14 days before, with real volume.
RISER_RATIO = 2.0
RISER_MIN_BASE = 30.0
RISER_MIN_RECENT = 150.0


def lag_days(cfg: Any, store: Any, project: str) -> int:
    """Index lag from GSC when connected (meta ``index_lag_days``), else ``default_lag_days``."""
    v = store.get_meta(project, "index_lag_days") if store is not None else None
    return int(v) if v is not None else int(cfg.default_lag_days)


def apply_lag(events: list[Event], lag: int) -> list[Event]:
    for e in events:
        e.publish_by = e.date - timedelta(days=lag)
    return events


def early_risers(signals: list[Signal], niche: Any, day: date) -> list[Event]:
    out = []
    for s in signals:
        if s.source != "wikipedia":
            continue
        x = s.extra
        ratio, base, recent = x.get("ratio"), x.get("base_14d"), x.get("views_3d")
        if ratio is None or base is None or recent is None:
            continue
        if ratio >= RISER_RATIO and base >= RISER_MIN_BASE and recent >= RISER_MIN_RECENT:
            out.append(Event(kind="early riser", title=s.term, date=day, source="wikipedia", url=s.url,
                             niche=niche.name, region=s.region, region_precision=s.region_precision))
    return out


def collect(client: Any, niche: Any, day: date, signals: list[Signal], lag: int,
            errors: list[str] | None = None, regions: list[str] | None = None) -> list[Event]:
    """Events for one niche: federal_register dates, future-dated RSS items, user ICS, early risers."""
    events: list[Event] = []
    if niche.upcoming:
        wanted = []
        if "federal_register" in niche.sources:
            wanted.append("federal_register")
        if "rss" in niche.sources and (niche.rss or niche.watch):
            wanted.append("rss")
        if niche.ics:
            wanted.append("ics")
        for name in wanted:
            try:
                events += load(name).collect_events(client, niche, day, regions)
            except Exception as e:     # one broken source never kills the run
                if errors is not None:
                    errors.append(f"{niche.name}/{name} events: {type(e).__name__}: {e}")
        events += early_risers(signals, niche, day)
    return apply_lag(events, lag)

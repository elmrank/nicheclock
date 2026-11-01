"""Shared formatting for the text outputs."""

from __future__ import annotations

from datetime import date

from nicheclock.i18n import t
from nicheclock.llm.denylist import NOT_SENT
from nicheclock.models import Event
from nicheclock.output import Feed
from nicheclock.regions import GLOBAL, label

# Sources whose terms require a citation when their data is shown (text in i18n: out.cite.<source>).
CITED_SOURCES = ("google_trends", "gdelt", "wikipedia", "mediawiki")


def citations(feed: Feed) -> list[str]:
    return [t(f"out.cite.{s}") for s in feed.sources if s in CITED_SOURCES]


def when(e: Event, today: date | None = None) -> str:
    return e.date.strftime("%Y-%m") if e.date_precision == "month" else e.date.isoformat()


def publish_by(e: Event, today: date | None = None) -> str:
    if e.publish_by is None:
        return ""
    today = today or date.today()
    if e.publish_by < today:
        return t("out.publish_now")
    return e.publish_by.isoformat()


def by_niche(feed: Feed) -> dict[str, list]:
    out: dict[str, list] = {n.name: [] for n in feed.niches}
    for it in feed.items:
        out.setdefault(it.niche or "", []).append(it)
    return out


def by_region(items: list, mode: str) -> list[tuple[str | None, list]]:
    """Items of one niche as [(region or None, items)]. Several sections only for ``per_country``
    feeds whose items really span more than one region (countries first, global last)."""
    regions = {i.region for i in items}
    if mode != "per_country" or len(regions) < 2:
        return [(None, items)]
    order = sorted(regions, key=lambda r: (r == GLOBAL, r))
    return [(r, [i for i in items if i.region == r]) for r in order]


def region_text(region: str | None, precision: str | None) -> str:
    """Badge text for an item, '' when there is nothing to say."""
    return label(region, precision) if region else ""


def fit_text(it) -> str:
    """``fit 85: reason`` for an item the model judged, else ''."""
    if it.llm_fit is None:
        return t("llm.not_sent") if it.llm_note == NOT_SENT else (it.llm_note or "")
    if it.llm_reason:
        return t("out.fit_reason", fit=f"{it.llm_fit:.0f}", reason=it.llm_reason)
    return t("out.fit", fit=f"{it.llm_fit:.0f}")


def llm_line(feed: Feed) -> str:
    """One line about the model's part in this feed ('' when no model ran)."""
    m = feed.llm
    if not m:
        return ""
    weight = m.get("fit_weight", 0.6)
    cost = f", ~${m['cost_usd_est']:.4f}" if m.get("cost_usd_est") else ""
    line = t("out.llm.judged", provider=m["provider"], model=m["model"], calls=m["calls"],
             tokens_in=m["input_tokens"], tokens_out=m["output_tokens"], cost=cost,
             rules=f"{1 - weight:.0%}", fit=f"{weight:.0%}")
    if m.get("not_sent"):
        line += " " + t("out.llm.not_sent", count=m["not_sent"])
    if m.get("budget_reached"):
        line += " " + t("out.llm.budget")
    return line

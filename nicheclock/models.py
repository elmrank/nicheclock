"""Core data models shared by every slice (names are stable; see ARCHITECTURE.md)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _d(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


_LLM_TOPIC = ("rule_score", "llm_fit", "llm_reason", "llm_status", "llm_note")
_LLM_ITEM = ("rule_score", "llm_fit", "llm_reason", "llm_note", "angle", "questions")


@dataclass
class Signal:
    """One observation of a term from one source (a trending query, a story, a repo...)."""

    source: str
    term: str
    url: str | None = None
    rank: int | None = None
    score: float | None = None
    geo: str | None = None
    seen_at: datetime = field(default_factory=utcnow)
    extra: dict[str, Any] = field(default_factory=dict)
    region: str = "global"             # ISO country code or 'global'
    region_precision: str = "global"   # exact | approx | global (see nicheclock.regions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "term": self.term, "url": self.url, "rank": self.rank,
            "score": self.score, "geo": self.geo, "seen_at": _iso(self.seen_at),
            "extra": self.extra, "region": self.region, "region_precision": self.region_precision,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Signal":
        return cls(
            source=d["source"], term=d["term"], url=d.get("url"), rank=d.get("rank"),
            score=d.get("score"), geo=d.get("geo"),
            seen_at=_dt(d.get("seen_at")) or utcnow(), extra=dict(d.get("extra") or {}),
            region=d.get("region") or "global", region_precision=d.get("region_precision") or "global",
        )


@dataclass
class Topic:
    """A cluster of signals about the same thing, optionally assigned to a niche."""

    key: str
    label: str
    signals: list[Signal] = field(default_factory=list)
    niche: str | None = None
    score: float = 0.0
    why: str = ""
    region: str = "global"             # the one country the evidence is from, 'multi', or 'global'
    region_precision: str = "global"
    regions: list[str] = field(default_factory=list)   # every country seen (sorted)
    # Model judgement (nicheclock.llm); all None when no model is configured. ``score`` is then the
    # blend of ``rule_score`` and ``llm_fit``; ``llm_status`` is "junk" / "merged" for hidden topics.
    rule_score: float | None = None
    llm_fit: float | None = None
    llm_reason: str | None = None
    llm_status: str | None = None
    llm_note: str | None = None

    @property
    def sources(self) -> list[str]:
        return sorted({s.source for s in self.signals})

    def to_dict(self) -> dict[str, Any]:
        d = {
            "key": self.key, "label": self.label, "niche": self.niche, "score": self.score,
            "why": self.why, "region": self.region, "region_precision": self.region_precision,
            "regions": self.regions, "signals": [s.to_dict() for s in self.signals],
        }
        d.update({k: getattr(self, k) for k in _LLM_TOPIC if getattr(self, k) is not None})
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Topic":
        return cls(
            key=d["key"], label=d["label"], niche=d.get("niche"), score=d.get("score", 0.0),
            why=d.get("why", ""), signals=[Signal.from_dict(s) for s in d.get("signals", [])],
            region=d.get("region") or "global", region_precision=d.get("region_precision") or "global",
            regions=list(d.get("regions") or []),
            **{k: d.get(k) for k in _LLM_TOPIC},
        )


@dataclass
class Event:
    """A dated thing in the future. ``publish_by`` = date minus the indexing lag."""

    kind: str
    title: str
    date: date
    date_precision: Literal["day", "month"] = "day"
    source: str = ""
    url: str | None = None
    publish_by: date | None = None
    niche: str | None = None
    region: str = "global"
    region_precision: str = "global"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "title": self.title, "date": _iso(self.date),
            "date_precision": self.date_precision, "source": self.source, "url": self.url,
            "publish_by": _iso(self.publish_by), "niche": self.niche,
            "region": self.region, "region_precision": self.region_precision,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            kind=d["kind"], title=d["title"], date=_d(d["date"]),  # type: ignore[arg-type]
            date_precision=d.get("date_precision", "day"), source=d.get("source", ""),
            url=d.get("url"), publish_by=_d(d.get("publish_by")), niche=d.get("niche"),
            region=d.get("region") or "global", region_precision=d.get("region_precision") or "global",
        )


@dataclass
class FeedItem:
    """What outputs, notifications and MCP tools hand to people."""

    project: str
    niche: str | None
    kind: Literal["topic", "event", "gap"]
    title: str
    url: str | None
    why: str
    score: float
    at: datetime = field(default_factory=utcnow)
    region: str = "global"
    region_precision: str = "global"
    regions: list[str] = field(default_factory=list)
    # Model judgement, None without a model: topics get fit/reason, gaps get angle/questions.
    rule_score: float | None = None
    llm_fit: float | None = None
    llm_reason: str | None = None
    llm_note: str | None = None          # "not sent to model: source terms" (see nicheclock.llm.denylist)
    angle: str | None = None
    questions: list[str] | None = None
    # Topics only: the signals behind the item ({source, title, url, at, note}), shown when a row is opened.
    evidence: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "project": self.project, "niche": self.niche, "kind": self.kind,
            "title": self.title, "url": self.url, "why": self.why, "score": self.score,
            "at": _iso(self.at), "region": self.region,
            "region_precision": self.region_precision, "regions": self.regions,
        }
        d.update({k: getattr(self, k) for k in _LLM_ITEM if getattr(self, k) is not None})
        if self.evidence:
            d["evidence"] = self.evidence
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FeedItem":
        return cls(
            project=d["project"], niche=d.get("niche"), kind=d["kind"], title=d["title"],
            url=d.get("url"), why=d.get("why", ""), score=d.get("score", 0.0),
            at=_dt(d.get("at")) or utcnow(), region=d.get("region") or "global",
            region_precision=d.get("region_precision") or "global", regions=list(d.get("regions") or []),
            evidence=d.get("evidence") or None,
            **{k: d.get(k) for k in _LLM_ITEM},
        )

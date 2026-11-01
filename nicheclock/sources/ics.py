"""User-supplied iCalendar (.ics) URLs: dated events for the upcoming calendar."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from nicheclock.http import FetchError, RobotsDisallowed
from nicheclock.models import Event, Signal
from nicheclock.sources._common import opt

REGION_SUPPORT = "global"            # your own feeds carry no country: region=global
NAME = "ics"
TERMS_URL = "https://www.rfc-editor.org/rfc/rfc5545"
TERMS_NOTE = ("Calendars are supplied by the user and used under their publisher's terms (the user "
              "is responsible for them); only summary, date and URL of each event are kept.")


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(v: str) -> str:
    return re.sub(r"\\([,;nN\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def _parse_date(value: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", value.strip())
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse(text: str) -> list[dict[str, Any]]:
    """VEVENTs -> [{summary, start: date, url, rrule: dict|None}] (DTSTART date part, timezone ignored)."""
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if cur.get("start") and cur.get("summary"):
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            name, _, value = line.partition(":")
            key = name.split(";")[0].upper()
            if key == "SUMMARY":
                cur["summary"] = _unescape(value)
            elif key == "DTSTART":
                cur["start"] = _parse_date(value)
            elif key == "URL":
                cur["url"] = value.strip()
            elif key == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
    return events


def _add_months(d: date, n: int) -> date | None:
    y, m = divmod(d.month - 1 + n, 12)
    try:
        return d.replace(year=d.year + y, month=m + 1)
    except ValueError:
        return None


def occurrences(ev: dict[str, Any], start: date, end: date) -> list[date]:
    """Occurrence dates within [start, end]. Simple RRULEs only (FREQ DAILY/WEEKLY/MONTHLY/YEARLY +
    INTERVAL/COUNT/UNTIL); rules with BYxxx parts yield DTSTART only."""
    first: date = ev["start"]
    rule = ev.get("rrule")
    if not rule or any(k.startswith("BY") for k in rule):
        return [first] if start <= first <= end else []
    freq, interval = rule.get("FREQ"), int(rule.get("INTERVAL", 1))
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = _parse_date(rule["UNTIL"]) if "UNTIL" in rule else None
    out, i = [], 0
    while i < 4000:
        if freq == "DAILY":
            d: date | None = first + timedelta(days=i * interval)
        elif freq == "WEEKLY":
            d = first + timedelta(weeks=i * interval)
        elif freq == "MONTHLY":
            d = _add_months(first, i * interval)
        elif freq == "YEARLY":
            d = _add_months(first, 12 * i * interval)
        else:
            return [first] if start <= first <= end else []
        i += 1
        if d is None:
            continue
        if d > end or (until and d > until) or (count is not None and i > count):
            break
        if d >= start:
            out.append(d)
    return out


def collect(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Signal]:
    return []   # calendars feed only the upcoming list


def collect_events(client: Any, niche: Any, day: date, regions: list[str] | None = None) -> list[Event]:
    horizon = day + timedelta(days=int(opt(niche, "ics_horizon_days", 365)))
    out = []
    for url in niche.ics:
        try:
            events = parse(client.get_text(url, cache_ttl=3 * 3600))
        except (FetchError, RobotsDisallowed):
            continue
        for ev in events:
            for d in occurrences(ev, day, horizon):
                out.append(Event(kind="calendar", title=ev["summary"], date=d, source=NAME,
                                 url=ev.get("url") or url, niche=niche.name))
    return sorted(out, key=lambda e: (e.date, e.title))

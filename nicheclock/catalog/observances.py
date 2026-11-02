"""Recurring observances from Wikidata (CC0): items with ``P837`` ("day in year for periodic occurrence").

Wikidata has about 9,400 such items. The value of ``P837`` is itself an item whose English label is a date rule:

* ``July 20`` or ``20 July``                       a fixed day of the year
* ``second Saturday in May``                       the n-th (first to fourth, or last) weekday of a month

Any other label ("Easter + 60 days", "15th day of the 8th month of the Chinese lunisolar calendar") is not parsed and the
observance is skipped, never guessed. The next occurrence on or after ``today`` (within a year) becomes an upcoming event
of kind ``observance``.
"""

from __future__ import annotations

import calendar
import re
import time
from datetime import date, timedelta
from typing import Any, Callable

import requests

SPARQL = "https://query.wikidata.org/sparql"
QUERY = "SELECT ?item ?v ?sl WHERE { ?item wdt:P837 ?v . ?item wikibase:sitelinks ?sl . }"
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}          # Monday = 0
NTH = {"first": 1, "second": 2, "third": 3, "fourth": 4, "last": -1}
FIXED = re.compile(r"^([A-Z][a-z]+) (\d{1,2})$")
FIXED_DM = re.compile(r"^(\d{1,2}) ([A-Z][a-z]+)$")
NTH_WEEKDAY = re.compile(r"^(first|second|third|fourth|last) ([A-Za-z]+) (?:in|of) ([A-Z][a-z]+)$", re.I)


def parse_rule(label: str) -> tuple[Any, ...] | None:
    """``("fixed", month, day)`` or ``("nth", n, weekday, month)``; None for any label that is not one of the two patterns."""
    label = (label or "").strip()
    m = FIXED.match(label)
    if m and m.group(1).lower() in MONTHS:
        return ("fixed", MONTHS[m.group(1).lower()], int(m.group(2)))
    m = FIXED_DM.match(label)
    if m and m.group(2).lower() in MONTHS:
        return ("fixed", MONTHS[m.group(2).lower()], int(m.group(1)))
    m = NTH_WEEKDAY.match(label)
    if m and m.group(2).lower() in WEEKDAYS and m.group(3).lower() in MONTHS:
        return ("nth", NTH[m.group(1).lower()], WEEKDAYS[m.group(2).lower()], MONTHS[m.group(3).lower()])
    return None


def occurrence(rule: tuple[Any, ...], year: int) -> date | None:
    try:
        if rule[0] == "fixed":
            return date(year, rule[1], rule[2])
        _k, n, wd, month = rule
        days = [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]
        days = [d for d in days if d.weekday() == wd]
        return days[n - 1] if n > 0 else days[-1]
    except (ValueError, IndexError):
        return None


def next_occurrence(rule: tuple[Any, ...], today: date, horizon: int = 365) -> date | None:
    """The first date of the rule on or after ``today`` that is within ``horizon`` days."""
    for year in (today.year, today.year + 1):
        d = occurrence(rule, year)
        if d is not None and today <= d <= today + timedelta(days=horizon):
            return d
    return None


def fetch_statements(user_agent: str, contact: str, session: Any = None, attempts: int = 5,
                     sleep: Callable[[float], None] = time.sleep) -> list[tuple[str, str, int]]:
    """(item, value item, sitelinks) for every P837 statement whose value is an item. The query service sometimes answers 200 with a
    result cut off by its own timeout (invalid JSON): that is retried after a pause, then raised."""
    s = session or requests.Session()
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = s.get(SPARQL, params={"query": QUERY + (f" # retry {attempt}" if attempt else "")}, timeout=120,
                         headers={"User-Agent": user_agent, "From": contact, "Accept": "application/sparql-results+json"})
            resp.raise_for_status()
            bindings = resp.json()["results"]["bindings"]
            break
        except (requests.RequestException, ValueError, KeyError) as e:
            last = e
            sleep(15.0 * (attempt + 1))
    else:
        raise RuntimeError(f"the query service did not return a complete result: {last}")
    out = []
    for b in bindings:
        v = b["v"]["value"]
        if "/entity/Q" not in v:                 # "unknown value" statements come back as blank nodes
            continue
        out.append((b["item"]["value"].rsplit("/", 1)[-1], v.rsplit("/", 1)[-1], int(float(b["sl"]["value"]))))
    return out


def upcoming(statements: list[tuple[str, str, int]], rule_label: Callable[[str], str], today: date
             ) -> dict[str, dict[str, Any]]:
    """{item: {"date", "rule", "sitelinks"}}: the next occurrence of each parseable statement (the earliest if an item has several)."""
    out: dict[str, dict[str, Any]] = {}
    for item, value, sl in statements:
        rule = parse_rule(rule_label(value))
        if rule is None:
            continue
        d = next_occurrence(rule, today)
        if d is None:
            continue
        cur = out.get(item)
        if cur is None or d.isoformat() < cur["date"]:
            out[item] = {"date": d.isoformat(), "rule": rule_label(value), "sitelinks": sl}
    return out

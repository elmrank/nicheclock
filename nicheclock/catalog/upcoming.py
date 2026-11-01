"""Upcoming: dated events from Wikidata (future P585/P580/P577), the ``holidays`` library and the
Federal Register, each with ``publish_by = date - lag`` where the lag depends on the kind of event.

The lags are defaults per event type, not measurements: they say "start publishing this many days
before the date". They are the catalog's equivalent of nicheclock's ``default_lag_days`` (3, the index
lag) plus the lead time a type of content usually needs.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from nicheclock.catalog.categories import Classifier, category_label
from nicheclock.catalog.resolver import NOT_TOPIC
from nicheclock.catalog.wd import Wikidata
from nicheclock.http import FetchError, RobotsDisallowed

# Days before the event to publish by. Keys are matched as exact kinds first, then by suffix/property.
LAG_DAYS = {
    "film release": 14, "game release": 14, "album release": 7, "single release": 7, "book release": 14,
    "software release": 14, "tv premiere": 14, "release": 14,
    "sports event": 7, "election": 21, "festival": 28, "award ceremony": 14, "space mission": 14,
    "holiday": 28, "comment deadline": 14, "effective date": 21, "event": 14,
}
HORIZON_DAYS = 365
MIN_SITELINKS = 6
CHUNK_DAYS = 30                  # dates per query: exact-date VALUES join is fast; a date range scan times out

PROPS = {"P585": "event", "P580": "event", "P577": "release"}
FR_URL = "https://www.federalregister.gov/api/v1/documents.json"
FR_LICENSE = "US government work, public domain (17 U.S.C. 105)"
FR_HORIZON = 180
HOLIDAY_LICENSE = "MIT (holidays Python package); dates are facts"
HOLIDAY_URL = "https://github.com/vacanza/holidays"
HOLIDAY_COUNTRIES = ("IN", "CN", "US", "ID", "PK", "NG", "BR", "BD", "RU", "ET")      # the 10 regions (data/regions.json)
WD_LICENSE = "CC0 (Wikidata)"


def lag_for(kind: str) -> int:
    return LAG_DAYS.get(kind, LAG_DAYS["event"])


# Wikidata classes the event kind is read from (``Classification.via`` is the nearest rule class).
RELEASE_KINDS = {"Q11424": "film release", "Q482994": "album release", "Q7366": "single release",
                 "Q571": "book release", "Q5398426": "tv premiere"}
EVENT_KINDS = {"Q40231": "election", "Q132241": "festival", "Q4504495": "award ceremony", "Q618779": "award ceremony",
               "Q2133344": "space mission"}


def kind_for(prop_kind: str, cls: Any) -> str:
    """The event type from the property and the item's class: a film with a publication date is a
    "film release", an election with a point in time an "election"."""
    via = cls.via if cls.kind == "class" else ""
    if prop_kind == "release":
        if via in RELEASE_KINDS and cls.category == "entertainment":
            return RELEASE_KINDS[via]
        if cls.category == "games":
            return "game release"
        if cls.category == "technology":
            return "software release"
        return "release"
    if cls.category == "sports":
        return "sports event"
    return EVENT_KINDS.get(via, "event")


def _query(prop: str, days: list[date]) -> str:
    """Items with a day-precision ``prop`` equal to one of ``days`` (exact-value join: a range FILTER over
    all P585 statements times out on the public endpoint; 30 values per GET stay under its URL limit)."""
    vals = " ".join(f'"{d.isoformat()}T00:00:00Z"^^xsd:dateTime' for d in days)
    return f"""SELECT ?item ?d ?sl WHERE {{
  VALUES ?d {{ {vals} }}
  ?item p:{prop} ?st . ?st psv:{prop} ?v . ?v wikibase:timeValue ?d ; wikibase:timePrecision 11 .
  ?item wikibase:sitelinks ?sl . FILTER(?sl >= {MIN_SITELINKS})
}} LIMIT 2000"""


def wikidata_events(wd: Wikidata, classifier: Classifier, today: date, log: Any = print) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Events of the next year whose item has a day-precision date and at least MIN_SITELINKS sitelinks."""
    rows: dict[tuple[str, str], tuple[str, int]] = {}      # (qid, date) -> (prop, sitelinks)
    ok = failed = 0
    for prop in PROPS:
        for i in range(0, HORIZON_DAYS, CHUNK_DAYS):
            days = [today + timedelta(days=k) for k in range(i, min(i + CHUNK_DAYS, HORIZON_DAYS))]
            try:
                for r in wd.sparql(_query(prop, days)):
                    qid = r["item"].rsplit("/", 1)[-1]
                    rows.setdefault((qid, r["d"][:10]), (prop, int(float(r["sl"]))))
                ok += 1
            except (FetchError, RobotsDisallowed, KeyError, ValueError) as e:
                failed += 1
                log(f"  wikidata {prop} from {days[0]}: {type(e).__name__}: {str(e)[:100]}")
    recs = wd.entities([q for q, _d in rows])
    keep = [(q, d, v) for (q, d), v in rows.items()
            if recs.get(q) and not set(recs[q]["claims"].get("P31", [])) & (NOT_TOPIC.keys() | {"Q5"})
            and recs[q]["label"] and d >= today.isoformat()]
    classes = classifier.classify_many([q for q, _d, _v in keep])
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for q, d, (prop, sl) in sorted(keep, key=lambda x: x[1]):
        rec, cls = recs[q], classes[q]
        kind = kind_for(PROPS[prop], cls)
        ev = events.get((q, kind))
        if ev is not None:                     # several dates for one item (per-country releases): earliest wins
            ev["dates"].append(d)
            continue
        events[(q, kind)] = _event(rec["label"], d, kind, lag_for(kind), cls.category, "global", "wikidata",
                                   f"https://www.wikidata.org/wiki/{q}", WD_LICENSE, qid=q, popularity=sl, dates=[d])
    events = list(events.values())
    return events, {"ok": ok > 0 and failed <= ok // 4, "detail": f"{ok} queries ok, {failed} failed, {len(events)} events"}


def _event(title: str, d: str, kind: str, lag: int, category: str, region: str, source: str, url: str,
           license_: str, **extra: Any) -> dict[str, Any]:
    day = date.fromisoformat(d)
    ev = {"title": title, "date": d, "date_precision": "day", "publish_by": (day - timedelta(days=lag)).isoformat(),
          "lag_days": lag, "kind": kind, "category": category, "category_label": category_label(category),
          "region": region, "source": source, "source_url": url, "license": license_}
    ev.update(extra)
    return ev


def holiday_events(today: date, countries: Iterable[str] = HOLIDAY_COUNTRIES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Public holidays of the next year; one row per (date, name), the countries listed in ``regions``."""
    try:
        import holidays
    except ImportError:
        return [], {"ok": False, "detail": "the holidays package is not installed (pip install holidays)"}
    horizon = today + timedelta(days=HORIZON_DAYS)
    grouped: dict[tuple[str, str], set[str]] = {}
    countries = list(countries)
    skipped = []
    for cc in countries:
        try:
            try:
                cal = holidays.country_holidays(cc, years=[today.year, today.year + 1], language="en_US")
            except Exception:                  # no English translation for this country: package default names
                cal = holidays.country_holidays(cc, years=[today.year, today.year + 1])
        except Exception as e:
            skipped.append(f"{cc}: {type(e).__name__}")
            continue
        for d, name in cal.items():
            if today <= d <= horizon:
                for part in str(name).split("; "):
                    grouped.setdefault((d.isoformat(), part.split(" (observed)")[0].strip()), set()).add(cc)
    events = []
    for (d, name), ccs in sorted(grouped.items()):
        region = next(iter(ccs)) if len(ccs) == 1 else ("global" if len(ccs) * 2 >= len(countries) else "multi")
        events.append(_event(name, d, "holiday", lag_for("holiday"), "other", region, "holidays",
                             HOLIDAY_URL, HOLIDAY_LICENSE, regions=sorted(ccs)))
    detail = f"{len(events)} holidays in {len(countries) - len(skipped)} countries"
    return events, {"ok": len(skipped) < len(countries), "detail": detail + (f"; skipped {', '.join(skipped)}" if skipped else "")}


def federal_register_events(client: Any, classifier: Classifier, today: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Comment deadlines and effective dates of significant documents (US federal)."""
    horizon = today + timedelta(days=FR_HORIZON)
    fields = ["title", "html_url", "publication_date", "type", "agencies", "comments_close_on", "effective_on",
              "document_number"]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    failed = 0
    for cond, field_, kind in (("comment_date", "comments_close_on", "comment deadline"),
                               ("effective_date", "effective_on", "effective date")):
        for page in (1, 2, 3):
            try:
                data = client.get_json(FR_URL, params={
                    f"conditions[{cond}][gte]": today.isoformat(), f"conditions[{cond}][lte]": horizon.isoformat(),
                    "conditions[significant]": 1, "order": "newest", "per_page": 100, "page": page, "fields[]": fields})
            except (FetchError, RobotsDisallowed):
                failed += 1
                break
            for r in data.get("results", []):
                d = r.get(field_)
                if not d or not (today.isoformat() <= d <= horizon.isoformat()) or not r.get("title"):
                    continue
                slugs = [a.get("slug", "") for a in r.get("agencies", []) if isinstance(a, dict)]
                cat = classifier.classify_agency(slugs)
                out.setdefault((r["html_url"], kind), _event(
                    r["title"], d, kind, lag_for(kind), cat, "US", "federal_register", r["html_url"], FR_LICENSE,
                    regions=["US"], agencies=[a.get("name") for a in r.get("agencies", []) if isinstance(a, dict) and a.get("name")]))
            if page >= int(data.get("total_pages") or 1):
                break
    return list(out.values()), {"ok": failed < 2, "detail": f"{len(out)} dated documents, {failed} failed requests"}


def build_upcoming(rt: Any, wd: Wikidata, classifier: Classifier, today: date, log: Any = print
                   ) -> tuple[dict[str, Any], dict[str, Any]]:
    status: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    for name, fn in (("wikidata_events", lambda: wikidata_events(wd, classifier, today, log)),
                     ("holidays", lambda: holiday_events(today)),
                     ("federal_register", lambda: federal_register_events(rt.client, classifier, today))):
        try:
            ev, st = fn()
        except Exception as e:                      # one broken source never kills the run
            ev, st = [], {"ok": False, "detail": f"{type(e).__name__}: {e}"}
        events += ev
        status[name] = st
        log(f"  upcoming {name}: {st['detail']}")
    events.sort(key=lambda e: (e["date"], -e.get("popularity", 0), e["title"]))
    return {"schema": 1, "today": today.isoformat(),
            "lag_note": "publish_by = date - lag_days; lag_days is a default per event kind (see SCHEMA.md), not a measurement.",
            "events": events}, status

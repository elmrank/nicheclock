"""Small per-region digests of the published catalog, for the hosted MCP tools `trending_now` and `upcoming_events`.

The Worker has 10 ms of CPU per request on the free plan. `trending-<ID>.json` is 100 to 650 KB with 14-day series, evidence and
breakdowns the tools never return, so parsing one costs several milliseconds. The digest keeps only the fields a tool row has, with
one-letter keys, so one region is about 40 KB (about 1 ms to parse) and the events of the whole catalog are about 80 KB.

Input is what the site publishes (`hosted/site/catalog-data` after the hidden-source filter in site/trending/build.py), so the tools can
never serve a channel, topic or source the website does not show. Written next to the shards, under `catalog-data/tools/`:

  index.json            {schema, regions: [{id, label, topics, date, limited, limited_note}], categories: {id: label}, kinds: [...],
                         units: {short unit: full unit}, licenses: {channel key: {source, label, license, url}},
                         events_today, lag_note, event_months: {"YYYY-MM": number of events}}
  trending-<ID>.json    {schema, region, date, generated_at, rows: [row]}
  events-YYYY-MM.json   {schema, events: [event]}       the events dated in that month, in date order; a 30-day window reads one or two of them

Trending row (short keys):  q qid, l label, c category id, s state (rising|fading|ended|steady), r [24h, 48h, 7d] multiples or null,
                            v volume (int) or null, u short unit, x channel keys, w why (one line), a link, y [rising channel keys]
Event (short keys):         t title, d date, p publish_by, g lag days, k kind, c category id, e regions (list) or null, o region,
                            n source id, a source_url, i licence
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = 1
WINDOWS = ("24h", "48h", "7d")
CHANNEL_NAMES = {"wikipedia": "Wikipedia", "news": "news", "hn": "Hacker News", "tvmaze": "TVmaze", "openlibrary": "Open Library", "gdacs": "GDACS"}
QID = re.compile(r"Q\d+$")


def _short_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    return unit.split(" (", 1)[0].strip() or None


def _num(x: Any, nd: int = 2) -> float | None:
    return round(float(x), nd) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def state_of(t: dict) -> str:
    """One word for the row: steady (a top item that is not rising), ended, fading (past its peak) or rising."""
    if t.get("trend") == "steady" or t.get("status") == "steady":
        return "steady"
    if t.get("status") == "ended":
        return "ended"
    if t.get("trend") == "fading" or t.get("status") == "fading":
        return "fading"
    return "rising"


def why(t: dict, state: str) -> str:
    """A one-line reason built only from numbers in the row: each channel's last value and its multiple of its own 14-day median."""
    parts: list[str] = []
    for key, ch in (t.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        value, unit, ratio = ch.get("value"), _short_unit(ch.get("unit")), ch.get("ratio")
        if isinstance(value, (int, float)) and unit:
            bit = f"{CHANNEL_NAMES.get(key, key)} {_fmt(value)} {unit}"
            if key != "hn" and isinstance(ratio, (int, float)):
                bit += f", {ratio:g}x its 14-day median"
            parts.append(bit)
    text = "; ".join(parts) or "listed by the catalog"
    if state == "steady":
        return "Steady top item, not rising: " + text
    if state == "ended":
        return "Rose within 7 days and is back near its baseline: " + text
    if state == "fading":
        return "Past its peak: " + text
    return text


def trending_row(t: dict) -> dict:
    st = state_of(t)
    wins = t.get("windows") or {}
    row: dict[str, Any] = {
        "q": t.get("qid") if QID.match(str(t.get("qid") or "")) else None,
        "l": t.get("label"),
        "c": t.get("category"),
        "s": st,
        "r": [_num((wins.get(w) or {}).get("ratio")) if isinstance(wins.get(w), dict) else None for w in WINDOWS],
        "v": int(t["volume"]) if isinstance(t.get("volume"), (int, float)) else None,
        "u": _short_unit(t.get("volume_unit")),
        "x": sorted((t.get("channels") or {}).keys()),
        "w": why(t, st),
        "a": t.get("wikipedia_url") or t.get("source_url"),
        "y": list(t.get("rising_channels") or []),
    }
    act = [bool(isinstance(wins.get(w), dict) and wins[w].get("active")) for w in WINDOWS]
    if any(act) and st == "rising":
        row["z"] = act
    return row


def event_row(e: dict) -> dict:
    return {
        "t": e.get("title"), "d": e.get("date"), "p": e.get("publish_by"), "g": e.get("lag_days"), "k": e.get("kind"),
        "c": e.get("category"), "e": e.get("regions") or None, "o": e.get("region"),
        "n": e.get("source"), "a": e.get("source_url"), "i": e.get("license"),
    }


def _dump(path: Path, doc: dict) -> int:
    blob = json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(blob, encoding="utf-8")
    return len(blob.encode("utf-8"))


def build(catalog: Path, out: Path) -> dict:
    """Write the digests of the published files in `catalog` to `out`. Returns {file: bytes} for the build log and the tests."""
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((catalog / "meta.json").read_text(encoding="utf-8"))
    sizes: dict[str, int] = {}
    ledger = {x.get("id"): x for x in meta.get("sources", []) if isinstance(x, dict)}
    regions, units, licenses = [], {}, {}
    for r in meta.get("regions", []):
        if not isinstance(r, dict) or "id" not in r:
            continue          # the sample fixture lists regions as plain strings: no digest for it
        rid = r["id"]
        f = catalog / ("trending.json" if rid == "global" else f"trending-{rid}.json")
        if not f.is_file():
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        rows = [trending_row(t) for t in doc.get("topics", [])]
        for t in doc.get("topics", []):
            for key, ch in (t.get("channels") or {}).items():
                if isinstance(ch, dict):
                    su = _short_unit(ch.get("unit"))
                    if su and su not in units and ch.get("unit"):
                        units[su] = ch["unit"]
                    if key not in licenses and ch.get("license"):
                        led = ledger.get(ch.get("source")) or {}
                        licenses[key] = {"source": ch.get("source"), "label": led.get("label") or CHANNEL_NAMES.get(key, key),
                                         "license": ch.get("license"), "url": led.get("url")}
            su = _short_unit(t.get("volume_unit"))
            if su and su not in units:
                units[su] = t["volume_unit"]
        sizes[f"trending-{rid}.json"] = _dump(out / f"trending-{rid}.json", {
            "schema": SCHEMA, "region": rid, "date": doc.get("date"), "generated_at": doc.get("generated_at"), "rows": rows})
        regions.append({"id": rid, "label": r.get("label"), "topics": len(rows), "date": doc.get("date"),
                        "limited": bool(r.get("limited")), "limited_note": r.get("limited_note")})
    ev_doc = json.loads((catalog / "upcoming.json").read_text(encoding="utf-8"))
    events = [event_row(e) for e in ev_doc.get("events", [])]
    months: dict[str, list[dict]] = {}
    for e in events:
        months.setdefault(str(e["d"])[:7], []).append(e)
    for month, rows in sorted(months.items()):
        sizes[f"events-{month}.json"] = _dump(out / f"events-{month}.json", {"schema": SCHEMA, "events": rows})
    sizes["index.json"] = _dump(out / "index.json", {
        "schema": SCHEMA, "regions": regions,
        "categories": {c["id"]: c["label"] for c in meta.get("categories", []) if isinstance(c, dict) and "id" in c},
        "kinds": sorted({e["k"] for e in events if e["k"]}),
        "units": units, "licenses": licenses, "events_today": ev_doc.get("today"), "lag_note": ev_doc.get("lag_note"),
        "event_months": {m: len(r) for m, r in sorted(months.items())},
    })
    return sizes

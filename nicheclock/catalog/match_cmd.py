"""``python -m nicheclock.catalog match``: the niche pool, the concept index and the handles on upcoming events.

Runs after ``build`` (it reads the same country pageview files and ``trending-*.json`` / ``upcoming.json`` in ``--out``) and
writes ``<out>/match/``::

    pool-<REGION>.json   rows [qid, title, views, ratio, handles, series | 0, flags]   (pool.py)
    concepts.json        {rows: [qid, label, description, popularity, aliases], labels: [qid, label]}   the typing index, and the names
                         of every class / occupation / subject a row points at (for "why it fits")
    bridges.json         {qid: [[neighbour, rows it matches], ...]}                    step 2: topics that travel together
    observances.json     rows [qid, title, date, rule, sitelinks, category, handles, flags]   yearly observances (P837), next date
    manifest.json        files, row counts, gzip sizes, budget notes

and rewrites ``<out>/upcoming.json`` with ``niche`` handles on every Wikidata event (the plain Upcoming list is otherwise unchanged:
observances are a file of their own, read only by "For your niche").
Wikidata is read in batches of 50 by at most 5 threads (wdbulk.py); a second run reads only what is new or older than 30 days.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nicheclock.catalog import bridges as bridges_mod
from nicheclock.catalog import countrypv, junk, observances, pool
from nicheclock.catalog.categories import Classifier
from nicheclock.catalog.pool import REGIONS, Region
from nicheclock.catalog.runtime import identity_from, make_runtime
from nicheclock.catalog.upcoming import WD_LICENSE, lag_for
from nicheclock.catalog.wd import Wikidata
from nicheclock.catalog.wdbulk import MISSING, BulkFetcher, EntityStore, Throttled

CONCEPT_MIN_VIEWS = 700          # a pool page this popular in any country is typeable
CONCEPT_MIN_DF = 3               # a class / subject on at least this many rows is typeable
SERIES_NOTE = "views/day of the last 7 days, oldest first; steady rows have none"


def _write(path: Path, doc: Any) -> int:
    data = pool.encode_json(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + b"\n")
    return pool.gz_size(data)


def event_handles(w: pool.Walk, df: Counter, n: int, rising: bool = False) -> dict[str, Any]:
    return {"h": pool.select_handles(w, df, n), "f": pool.flags_of(w, rising)}


def annotate_events(out: Path, mdir: Path, store: EntityStore, fetcher: BulkFetcher, rt: Any, df_all: Counter, n_all: int, today: date,
                    user_agent: str, contact: str, log: Any) -> dict[str, Any]:
    """Put ``niche`` handles on every Wikidata event of upcoming.json and write observances.json (the yearly observances that have a handle)."""
    path = out / "upcoming.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    events = [e for e in doc["events"] if e.get("kind") != "observance"]       # an older run put observances here
    for e in events:
        e.pop("niche", None)
        e.pop("niche_only", None)
    stats: dict[str, Any] = {"events": len(events)}
    # observances: parse the rule of every P837 statement, keep those with a handle that is not shared by the whole set
    try:
        statements = observances.fetch_statements(user_agent, contact)
    except Exception as e:                       # keep the previous observances.json and say so; the events themselves still get handles
        stats["observances_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        log(f"  observances: {stats['observances_error']}")
        statements = None
    fetcher.fetch({v for _i, v, _s in statements or []})
    label = lambda v: (store.get(v) or {}).get("l", "") if store.get(v) is not MISSING else ""
    nxt = observances.upcoming(statements or [], label, today)
    log(f"  observances: {len(statements or [])} statements, {len(nxt)} with a parseable rule in the next year")
    walks = pool.resolve(store, fetcher, list(nxt) + [e["qid"] for e in events if e.get("qid")], log)
    obs_df: Counter = Counter()
    for q in nxt:
        if q in walks:
            for t in walks[q].targets():
                obs_df[t] += 1
    n_obs = sum(1 for q in nxt if q in walks)
    cls = Classifier(Wikidata(rt.client, rt.cache))
    keep = []
    for q, o in nxt.items():
        w = walks.get(q)
        if w is None or junk.adult_reason(w.label, w.description, sorted(w.targets())):
            continue
        h = pool.select_handles(w, obs_df, n_obs)
        if h:
            keep.append((q, o, w, h))
    classes = cls.classify_many([q for q, *_ in keep])
    existing = {e.get("qid") for e in events if e.get("qid")}
    rows = [[int(q[1:]), w.label, o["date"], o["rule"], o["sitelinks"], classes[q].category, h, pool.flags_of(w, False)]
            for q, o, w, h in sorted(keep, key=lambda k: (k[1]["date"], k[2].label)) if q not in existing]
    for e in events:
        w = walks.get(e.get("qid", ""))
        if w is not None:
            e["niche"] = event_handles(w, df_all, n_all)
    stats.update(observances=len(rows), annotated=sum(1 for e in events if "niche" in e))
    doc["events"] = events
    doc["events_note"] = "niche = handles for matching a profile (see hosted/site/catalog-data/match/); yearly observances are in match/observances.json"
    _write(path, doc)
    if statements is None:
        return stats
    _write(mdir / "observances.json", {
        "schema": 1, "today": today.isoformat(), "kind": "observance", "lag_days": lag_for("observance"), "source": "wikidata", "license": WD_LICENSE,
        "recurrence": "yearly rule (Wikidata P837)", "row": "[qid, title, date, rule, sitelinks, category, handles, flags]", "rows": rows})
    return stats


def build_concepts(store: EntityStore, rows_by_region: dict[str, list[list[Any]]], extra_targets: set[str]
                   ) -> tuple[list[list[Any]], list[list[Any]]]:
    """(typing rows, label rows). Typing rows: popular pool pages, classes / subjects that many rows point at, the samples; the
    most popular first. Label rows: the name of every other target of a handle, so a class or occupation can be read by its name."""
    pop: dict[str, int] = {}
    df: Counter = Counter()
    for rows in rows_by_region.values():
        for r in rows:
            q = "Q" + str(r[0])
            pop[q] = max(pop.get(q, 0), r[2])
            for h in r[4]:
                df[pool.decode(h)[0]] += 1
    typeable = {q for q, v in pop.items() if v >= CONCEPT_MIN_VIEWS} | {q for q, c in df.items() if c >= CONCEPT_MIN_DF} | extra_targets | set(pool.SAMPLE_QIDS)
    rows, labels = [], []
    for q in set(pop) | set(df) | extra_targets | set(pool.SAMPLE_QIDS):
        rec = store.get(q)
        if rec is MISSING or rec is None or not rec["l"] or junk.adult_reason(rec["l"], rec["d"], []):
            continue
        if q in typeable:
            rows.append([int(q[1:]), rec["l"], rec["d"][:70], pop.get(q, 0), rec["a"][:2] if pop.get(q, 0) else []])
        elif q in df:
            labels.append([int(q[1:]), rec["l"]])
    rows.sort(key=lambda r: (-r[3], -df.get("Q" + str(r[0]), 0), r[0]))
    labels.sort()
    return rows, labels


def cmd_match(a: argparse.Namespace) -> int:
    ident = identity_from(a.config)
    if not ident.is_set():
        print("set NICHECLOCK_USER_AGENT and NICHECLOCK_CONTACT_EMAIL (or pass --config nicheclock.yaml)", file=sys.stderr)
        return 2
    log = lambda m: print(m, flush=True)
    started = time.monotonic()
    out = Path(a.out)
    mdir = Path(a.match_out) if a.match_out else out / "match"
    state = Path(a.state)
    store = EntityStore(state / "match-entities.sqlite")
    log(f"entity store: {store.load()} fresh records")
    fetcher = BulkFetcher(store, ident.user_agent, ident.contact_email, log=log)
    rt = make_runtime(ident, a.state)
    isos = [r for r in (a.regions or REGIONS)]
    cend = countrypv.latest_day(rt.client)
    if cend is None:
        days = sorted(p.name[:10] for p in (state / "countrypv").glob("*.json.gz"))
        cend = date.fromisoformat(days[-1]) if days else None
    if cend is None:
        print("no country pageview file: run `build` first", file=sys.stderr)
        return 3
    samples = countrypv.gather(rt.client, state, cend, isos, 22)
    end = max(samples)
    log(f"country pageviews up to {end} ({len(samples)} days)")
    today = date.fromisoformat(a.today) if a.today else datetime.now(timezone.utc).date()
    regions: dict[str, Region] = {}
    for iso in isos:
        extra: list[str] = []
        f = out / f"trending-{iso}.json"
        if f.exists():
            extra = [t["qid"] for t in json.loads(f.read_text(encoding="utf-8")).get("topics", []) if t.get("qid")]
        pages = pool.select_pages(samples, end, iso, extra)
        regions[iso] = Region(iso, pages)
        log(f"{iso}: {len(pages)} pool pages ({sum(1 for p in pages if p['rising'])} rising)")
    try:
        everything = list(dict.fromkeys([p["qid"] for r in regions.values() for p in r.pages] + list(pool.SAMPLE_QIDS)))
        log(f"reading {len(everything)} pages and their classes from Wikidata")
        walks = pool.resolve(store, fetcher, everything, log)
        for r in regions.values():
            r.walks = {p["qid"]: walks[p["qid"]] for p in r.pages if p["qid"] in walks}
        fetcher.fetch(pool.SAMPLE_QIDS)
    except Throttled as e:
        log(f"STOPPED: {e}. What was read is kept; run again later.")
        store.close()
        return 4
    manifest: dict[str, Any] = {"schema": 1, "date": end.isoformat(), "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "handle": "qid * 64 + kind * 16 + sub; kind 0 class (sub = distance), 1 occupation (distance), 2 subject (property code), 3 taxon (distance)",
                                "row": "[qid, title, views, ratio, handles, series or 0, flags]; flags: 1 person, 2 fictional, 4 clade page, 8 rising, bits 4-7 occupations",
                                "series": SERIES_NOTE, "regions": {}}
    rows_by_region: dict[str, list[list[Any]]] = {}
    df_all: Counter = Counter()
    n_all = 0
    for iso, r in regions.items():
        rows, stats, size = pool.fit_budget(r, log)
        rows_by_region[iso] = rows
        for row in rows:
            for h in row[4]:
                df_all[pool.decode(h)[0]] += 1
        n_all += len(rows)
        doc = {"schema": 1, "region": iso, "date": end.isoformat(), "unit": countrypv.UNIT, "source": countrypv.NAME,
               "license": countrypv.LICENSE, "rows": rows}
        _write(mdir / f"pool-{iso}.json", doc)
        manifest["regions"][iso] = {"file": f"match/pool-{iso}.json", **stats}
        log(f"{iso}: wrote {len(rows)} rows, {size / 1e6:.2f} MB gzip (budget {stats['budget_gzip_bytes'] / 1e6:.1f} MB)")
    try:
        manifest["events"] = annotate_events(out, mdir, store, fetcher, rt, df_all, n_all, today, ident.user_agent, ident.contact_email, log)
        log(f"events: {manifest['events']}")
    except Throttled as e:
        log(f"events skipped: {e}")
        manifest["events"] = {"error": str(e)}
    # targets that events point at need labels too
    up = json.loads((out / "upcoming.json").read_text(encoding="utf-8"))
    ev_targets = {pool.decode(h)[0] for e in up["events"] for h in (e.get("niche") or {}).get("h", [])}
    obs = json.loads((mdir / "observances.json").read_text(encoding="utf-8")) if (mdir / "observances.json").exists() else {"rows": []}
    ev_targets |= {pool.decode(h)[0] for r in obs["rows"] for h in r[6]}
    fetcher.fetch(ev_targets)
    concepts, labels = build_concepts(store, rows_by_region, ev_targets)
    size = _write(mdir / "concepts.json", {"schema": 1, "date": end.isoformat(), "rows": concepts, "labels": labels})
    manifest["concepts"] = {"file": "match/concepts.json", "rows": len(concepts), "labels": len(labels), "gzip_bytes": size}
    log(f"concepts: {len(concepts)} typeable + {len(labels)} names, {size / 1e6:.2f} MB gzip")
    manifest["observances"] = {"file": "match/observances.json", "rows": len(obs["rows"]), "gzip_bytes": pool.gz_size((mdir / "observances.json").read_bytes())}
    try:
        br = bridges_mod.build(store, fetcher, concepts + labels, rows_by_region, rt, ident.user_agent, ident.contact_email, log)
        size = _write(mdir / "bridges.json", {"schema": 1, "date": end.isoformat(), "note": br["note"], "bridges": br["bridges"]})
        manifest["bridges"] = {"file": "match/bridges.json", "anchors": len(br["bridges"]), "gzip_bytes": size}
        log(f"bridges: {len(br['bridges'])} anchors, {size / 1e3:.0f} KB gzip")
    except Throttled as e:
        log(f"bridges skipped: {e}")
    manifest["failed_ids"] = len(set(fetcher.failed))
    manifest["wikidata_calls"] = fetcher.calls
    manifest["seconds"] = round(time.monotonic() - started)
    _write(mdir / "manifest.json", manifest)
    store.close()
    rt.cache.close()
    log(f"done in {manifest['seconds']} s, {fetcher.calls} Wikidata calls, {len(set(fetcher.failed))} ids unread")
    return 0


def add_parser(sub: Any) -> None:
    m = sub.add_parser("match", help="niche pool, concept index and event handles (needs `build` first)")
    m.add_argument("--out", default="hosted/site/catalog-data", help="the catalog data dir: trending-*.json are read, upcoming.json gets the event handles")
    m.add_argument("--match-out", help="where the big niche files (pool, concepts, bridges, observances, manifest) are written; default <out>/match. "
                   "The daily job uses a git-ignored dir and deploy-site.sh copies it into the build (they are not committed)")
    m.add_argument("--regions", nargs="+", choices=list(REGIONS), help="country pools to build (default: all four)")
    m.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")

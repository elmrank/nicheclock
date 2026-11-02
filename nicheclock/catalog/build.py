"""``python -m nicheclock.catalog build|resolve|verify-map``: the daily catalog build and its tools."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from nicheclock.catalog.categories import Classifier, category_ids, category_label, check_symmetry, load_categories, load_map
from nicheclock.catalog.resolver import Resolver
from nicheclock.catalog.runtime import identity_from, make_runtime
from nicheclock.catalog.upcoming import build_upcoming
from nicheclock.catalog.wd import Wikidata

def write_json(path: Path, doc: Any) -> None:
    """Compact JSON (the site downloads these files); meta.json stays readable."""
    indent = 1 if path.name == "meta.json" else None
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")) + "\n",
                    encoding="utf-8")


SOURCE_FILTER = [
    {"key": "wikipedia", "label": "Wikipedia", "family": "wiki"},
    {"key": "news", "label": "News (GDELT)", "family": "news"},
    {"key": "hn", "label": "Hacker News", "family": "community"},
    {"key": "youtube", "label": "YouTube", "family": "video"},
    {"key": "github", "label": "GitHub", "family": "developer"},
    {"key": "gdacs", "label": "GDACS disaster alerts", "family": "hazards"},
    {"key": "tvmaze", "label": "TVmaze (TV schedules)", "family": "tv"},
    {"key": "stackexchange", "label": "Stack Overflow", "family": "developer"},
    {"key": "openlibrary", "label": "Open Library", "family": "books"},
]


EXPERIMENTAL_IDS: tuple[str, ...] = ()


def _ledger_runtime(entry: dict[str, Any], rstat: dict[str, dict[str, dict[str, Any]]],
                    ustat: dict[str, Any], experimental: bool = False) -> tuple[str, bool | None, str]:
    """(status, ok, detail) of one ledger entry for this build."""
    sid = entry["id"]
    status = entry["status"]
    if sid in EXPERIMENTAL_IDS and not experimental:
        return "experimental_off", None, "Experimental source: off until its terms are re-read; no row of it is published."
    if status in ("excluded", "planned"):
        return status, None, entry["decision"]
    chan = {"wikimedia_pageviews": ("global", "wikipedia"), "hn": ("global", "hn"),
            "github": ("global", "github"),
            "gdacs": ("global", "gdacs"), "tvmaze": ("global", "tvmaze"), "stackexchange": ("global", "stackexchange"),
            "openlibrary": ("global", "openlibrary")}.get(sid)
    if chan:
        s = rstat.get(chan[0], {}).get(chan[1])
        return status, (s["status"] == "ok") if s else None, s["detail"] if s else "not run"
    if sid in ("wikimedia_country", "gdelt", "youtube"):
        key = {"wikimedia_country": "wikipedia", "gdelt": "news", "youtube": "youtube"}[sid]
        seen = [(r, v[key]) for r, v in rstat.items() if key in v and (sid != "wikimedia_country" or "per-country" in v[key]["detail"])]
        if sid == "gdelt":
            seen = [(r, v) for r, v in seen if r != "global"] or seen
        if not seen:
            return status, None, "not run"
        ok = [r for r, v in seen if v["status"] == "ok"]
        if sid == "youtube" and not ok:
            pend = any(v["status"] == "pending_key" for _r, v in seen)
            return ("pending_key" if pend else status), False, seen[0][1]["detail"]
        return status, bool(ok), f"ok in {len(ok)} of {len(seen)} regions"
    u = ustat.get({"wikidata": "wikidata_events"}.get(sid, sid))
    if u:
        return status, bool(u["ok"]), u["detail"]
    return status, None, "not run"


def meta_doc(docs: dict[str, dict[str, Any]], upcoming: dict[str, Any] | None, rstat: dict[str, Any],
             ustat: dict[str, Any], now: datetime, experimental: bool = False) -> dict[str, Any]:
    from nicheclock.catalog import regions as rg

    events = (upcoming or {}).get("events", [])
    world = docs.get("global", {"topics": []})
    topics = world["topics"]
    cats = []
    for c in load_categories()["categories"]:
        cats.append({"id": c["id"], "label": c["label"],
                     "topics": sum(1 for t in topics if t["category"] == c["id"]),
                     "multi_source": sum(1 for t in topics if t["category"] == c["id"] and t["multi_source"]),
                     "events": sum(1 for e in events if e["category"] == c["id"])})
    regions_out = []
    for r in rg.regions():
        rid = r["id"]
        doc = docs.get(rid)
        st = rstat.get(rid, {})
        have = [k for k, v in st.items() if v.get("status") in ("ok", "limited") and v.get("topics")]
        wiki = st.get("wikipedia", {})
        limited = wiki.get("status") == "limited"
        regions_out.append({
            "id": rid, "label": r["label"], "native_label": r["native_label"], "rank": r["rank"],
            "population_2026": r["population_2026"], "languages": r["languages"], "timezone": r["timezone"],
            "limited": limited, "limited_note": (wiki.get("detail") if limited else None),
            "sources": have, "source_status": {k: {"status": v["status"], "detail": v["detail"], "topics": v.get("topics")} for k, v in st.items()
                              if v.get("status") != "off"},
            "topics": len(doc["topics"]) if doc else 0, "date": doc["date"] if doc else None,
            "filtered": doc.get("filtered") if doc else None,
            "categories_below_10": sum(1 for v in doc.get("coverage", {}).values() if v["limited"]) if doc else None,
            "file": rg.trending_file(rid)})
    sources = []
    for e in rg.ledger():
        status, ok, detail = _ledger_runtime(e, rstat, ustat, experimental)
        sources.append({"id": e["id"], "label": e["label"], "family": e["family"], "status": status, "ok": ok,
                        "detail": detail, "license": e["license"], "url": e["url"], "terms_url": e["terms_url"],
                        "terms_quote": e["terms_quote"], "unit": e["unit"]})
    from nicheclock.catalog.seeds import check_seed_symmetry

    problems = check_symmetry() + check_seed_symmetry()
    return {
        "schema": 2, "generated_at": now.strftime("%Y-%m-%dT%H:%MZ"), "date": world.get("date"),
        "today": (upcoming or {}).get("today"),
        "category_source": load_categories()["source"], "population_source": rg.load()["population_source"],
        "counts": {"topics": len(topics), "multi_source": sum(1 for t in topics if t["multi_source"]),
                   "events": len(events), "categories_with_topics": sum(1 for c in cats if c["topics"]),
                   "categories_with_events": sum(1 for c in cats if c["events"]), "categories": len(cats),
                   "regions": len(regions_out), "topics_all_regions": sum(len(d["topics"]) for d in docs.values())},
        "experimental": experimental,
        "categories": cats, "regions": regions_out,
        "coverage": {rid: d["coverage"] for rid, d in docs.items() if "coverage" in d}, "sources": sources, "source_filter": [{k: v for k, v in f.items() if k != "experimental"} for f in SOURCE_FILTER
                          if (experimental or not f.get("experimental"))
                          and (rstat.get("global", {}).get(f["key"]) or {}).get("status") != "pending_key"],
        "symmetry": {"ok": not problems, "problems": problems},
        "partial": [r["id"] for r in regions_out if r["source_status"].get("build", {}).get("status") in ("skipped", "failed")],
        "units": {"wikipedia": "pageviews/day", "news": "articles/day (estimated)", "hn": "points",
                  "youtube": "video views as reported by YouTube (chart presence, no ratio)",
                  "note": "There is no search volume in this catalog. Channels are never summed or converted, "
                          "and a source with no data for a region is absent there, never 0."},
    }


def cmd_build(a: argparse.Namespace) -> int:
    from nicheclock.catalog import regions as rg
    from nicheclock.catalog.trending import build_all

    from nicheclock.catalog.seeds import check_seed_symmetry

    problems = check_symmetry() + check_seed_symmetry()
    if problems:
        print("symmetry check failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 3
    ident = identity_from(a.config)
    if not ident.is_set():
        print("set NICHECLOCK_USER_AGENT and NICHECLOCK_CONTACT_EMAIL (or pass --config nicheclock.yaml)", file=sys.stderr)
        return 2
    rt = make_runtime(ident, a.state)
    wd = Wikidata(rt.client, rt.cache)
    resolver, classifier = Resolver(wd), Classifier(wd)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    today = date.fromisoformat(a.today) if a.today else datetime.now(timezone.utc).date()
    now = datetime.now(timezone.utc)
    docs: dict[str, dict[str, Any]] = {}
    rstat: dict[str, Any] = {}
    ustat: dict[str, Any] = {}
    upcoming = None
    wanted = [r for r in rg.regions() if not a.regions or r["id"] in a.regions]
    if "trending" in a.only:
        print("trending:")
        docs, rstat, extra = build_all(rt, wd, resolver, classifier, today, wanted, max_seconds=a.max_minutes * 60 if a.max_minutes else None,
                                       experimental=a.experimental)
        from nicheclock.catalog.trending import split_youtube

        for rid in list(docs):
            docs[rid], side, extra_rows = split_youtube(docs[rid])
            # split_youtube set docs[rid]["coverage"] from the public rows: that is what a fresh clone serves.
            # The chart-only sidecar rows are NOT counted (the sidecar is git-ignored, a fresh clone has no file for them).
            docs[rid]["sidecar_rows"] = len(extra_rows)
            write_json(out / rg.trending_file(rid).replace("trending", "youtube"), {"schema": 1, "region": rid, "date": docs[rid]["date"],
                       "note": "YouTube chart data, regenerated by every build, not stored in git (30-day rule of the YouTube API terms)",
                       "topics": side, "extra_topics": extra_rows,
                       "extra_note": "complete rows of topics that exist only because of the chart; append them to the region's rows"})
            write_json(out / rg.trending_file(rid), docs[rid])
        for name, doc in extra.items():
            write_json(out / name, doc)
    if "upcoming" in a.only:
        print("upcoming:")
        upcoming, ustat = build_upcoming(rt, wd, classifier, today)
        write_json(out / "upcoming.json", upcoming)
    # a partial run keeps the other files' numbers in meta.json
    for r in rg.regions():
        f = out / rg.trending_file(r["id"])
        if r["id"] not in docs and f.exists():
            docs[r["id"]] = json.loads(f.read_text())
    if upcoming is None and (out / "upcoming.json").exists():
        upcoming = json.loads((out / "upcoming.json").read_text())
    old = json.loads((out / "meta.json").read_text()) if (out / "meta.json").exists() else {}
    for r in old.get("regions", []):                     # regions not rebuilt this time keep their source status
        if r["id"] not in rstat:
            rstat[r["id"]] = {k: dict(v) for k, v in r["source_status"].items()}
    meta = meta_doc(docs, upcoming, rstat, ustat, now, a.experimental)
    write_json(out / "meta.json", meta)
    (out / "COVERAGE.md").write_text(coverage_markdown(meta), encoding="utf-8")
    rt.cache.close()
    c = meta["counts"]
    print(f"topics {c['topics']} Worldwide ({c['multi_source']} multi-source), {c['topics_all_regions']} in all regions, "
          f"events {c['events']}, categories with topics {c['categories_with_topics']}/{c['categories']}")
    for r in meta["regions"]:
        print(f"  {r['id']:7} topics={r['topics']:4} limited={r['limited']} sources={r['sources']}")
    for s in meta["sources"]:
        print(f"  {s['id']:20} {s['status']:12} ok={s['ok']}  {str(s['detail'])[:110]}")
    return 0


def coverage_markdown(meta: dict[str, Any]) -> str:
    """The region x category table (rising / steady rows) and the self-check lines."""
    cats = [(c["id"], c["label"]) for c in meta["categories"]]
    regs = [r for r in meta["regions"] if r["id"] in meta.get("coverage", {})]
    lines = ["# Coverage: rows per region and category", "",
             f"Build of {meta['generated_at']} (data day {meta['date']}). Counts are the rows a fresh clone of the repository serves "
             "under the page's default 7d window: the git-tracked trending files only. YouTube sidecar rows (git-ignored, absent in a "
             "fresh clone) are not counted. Each cell is `rising + steady`: rising = a real rise "
             "(strong, or mild when a category was thin), steady = top items by volume with no real rise, shown as "
             "\"Steady / Top\". A cell marked `*` has fewer than 10 rows: the data has no more, nothing is invented "
             "(\"limited data\").", "",
             "| Category | " + " | ".join(r["id"] for r in regs) + " |", "|---|" + "---|" * len(regs)]
    for cid, label in cats:
        cells = []
        for r in regs:
            v = meta["coverage"][r["id"]][cid]
            cells.append(f"{v['rising']}+{v['steady']}" + ("*" if v["limited"] else ""))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "## Self-check", ""]
    for r in regs:
        below = [label for cid, label in cats if meta["coverage"][r["id"]][cid]["limited"]]
        lines.append(f"- {r['label']}: {len(cats) - len(below)}/{len(cats)} categories have 10 or more rows"
                     + (f"; limited: {', '.join(below)}" if below else ""))
    return "\n".join(lines) + "\n"


def cmd_resolve(a: argparse.Namespace) -> int:
    rt = make_runtime(identity_from(a.config), a.state)
    wd = Wikidata(rt.client, rt.cache)
    resolver, classifier = Resolver(wd), Classifier(wd)
    res = resolver.resolve(a.text, hint=a.hint, context=a.context or "")
    doc = res.to_dict()
    if res.qid:
        c = classifier.classify(res.qid)
        doc["category"] = c.category
        doc["category_label"] = category_label(c.category)
        doc["category_via"] = c.to_dict()
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    rt.cache.close()
    return 0 if res.qid else 1


def cmd_verify_map(a: argparse.Namespace) -> int:
    """Every class QID in category_map.yaml must still exist (needs network); ``--labels`` prints them."""
    rt = make_runtime(identity_from(a.config), a.state)
    wd = Wikidata(rt.client, rt.cache)
    cmap = load_map()
    bad = 0
    for section in ("class_rules", "occupation_rules"):
        for cat, items in cmap[section].items():
            recs = wd.entities([it["class"] for it in items])
            for it in items:
                rec = recs.get(it["class"])
                if rec is None or rec["id"] != it["class"]:
                    bad += 1
                    print(f"MISSING OR REDIRECTED {section} {cat} {it['class']}")
                elif a.labels:
                    print(f"{section[:-6]:11} {cat:22} {rec['id']:11} {rec['label']}")
    print("all QIDs exist" if not bad else f"{bad} problems")
    rt.cache.close()
    return 1 if bad else 0


def cmd_map(a: argparse.Namespace) -> int:
    """The category mapping as a markdown table with live Wikidata labels."""
    rt = make_runtime(identity_from(a.config), a.state)
    wd = Wikidata(rt.client, rt.cache)
    cmap = load_map()
    print("| category | rule | class | label |\n|---|---|---|---|")
    for section in ("class_rules", "occupation_rules"):
        for cat, items in cmap[section].items():
            recs = wd.entities([it["class"] for it in items])
            for it in items:
                print(f"| {category_label(cat)} | {section[:-6]} | {it['class']} | {(recs.get(it['class']) or {}).get('label', '?')} |")
    rt.cache.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m nicheclock.catalog")
    p.add_argument("--config", help="nicheclock.yaml to read the identity (User-Agent, contact email) from")
    p.add_argument("--state", default="~/.nicheclock-catalog", help="cache + daily samples (default ~/.nicheclock-catalog)")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build trending.json, upcoming.json and meta.json")
    b.add_argument("--out", default="hosted/site/catalog-data")
    b.add_argument("--only", nargs="+", choices=["trending", "upcoming"], default=["trending", "upcoming"])
    b.add_argument("--today", help="YYYY-MM-DD (default: today, UTC)")
    b.add_argument("--max-minutes", type=float, default=40, help="time budget for the trending build (0 = none); a region not started "
                   "in time is skipped and its previous file stays published, `partial` is set in meta.json")
    b.add_argument("--experimental", action="store_true", help="also build the experimental sources (none right now: the GitHub rank runs on the "
                   "official REST API, Stack Overflow tags wait for a key, PyPI was removed)")
    b.add_argument("--regions", nargs="+", help="region ids to build (default: all; e.g. global IN)")
    r = sub.add_parser("resolve", help="text, QID or Wikipedia URL -> QID + category")
    r.add_argument("text")
    r.add_argument("--hint", choices=["person", "org"])
    r.add_argument("--context", help="extra words that favour a candidate (e.g. 'planet')")
    v = sub.add_parser("verify-map", help="check that the category_map.yaml QIDs exist on Wikidata")
    v.add_argument("--labels", action="store_true", help="print each rule with its live label")
    sub.add_parser("map", help="print category_map.yaml as a table with live labels")
    a = p.parse_args(argv)
    a.state = str(Path(a.state).expanduser())
    return {"build": cmd_build, "resolve": cmd_resolve, "verify-map": cmd_verify_map, "map": cmd_map}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())

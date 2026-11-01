"""Write the R2 JSON layout of the hosted API from a local nicheclock store (hosted/SCHEMA.md).

    python -m nicheclock.hosted_export --config nicheclock.yaml --out ./hosted-out [--project P]
    python -m nicheclock.hosted_export --config job.yaml --out ./hosted-out --project P \\
        --custom-key k_0123456789abcdef --niche-id custom-example-com
    python -m nicheclock.hosted_export ... --print-upload nicheclock-data      # prints, never runs

Only the hosted allow-list leaves this module (the Worker re-checks at serving time): signals of any
other source are dropped, topics that used one are re-scored from the remaining signals so that
neither the score nor the ``why`` text carries data from a source we may not redistribute. Upload is a
separate, manual step with ``wrangler r2 object put`` (see ``upload_commands``); this module makes no
network call and never runs wrangler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nicheclock.cluster import LABEL_PRIORITY, score_topic
from nicheclock import regions as rg
from nicheclock.models import Event, Signal, Topic
from nicheclock.store import Store

SCHEMA_VERSION = 1
CUSTOM_PREFIX = "custom-"

# local source name -> hosted source id. Anything not listed here is never exported.
PUBLIC_MAP = {
    "wikipedia": "wikimedia_pageviews",   # nicheclock's `wikipedia` source = Wikimedia pageviews (CC0)
    "mediawiki": "wikipedia",             # MediaWiki API facts: title / short description / link only
    "wikidata": "wikidata",
    "gdelt": "gdelt",
    "hn": "hn",
    "federal_register": "federal_register",
}
# Internal-scope sources (internal use is not redistribution). Written only
# with include_internal=True AND a custom/<key_id>/ target, tagged "scope": "internal" on every item, evidence entry
# and event that touches them; the Worker shows them to internal/admin keys only and hides tagged items from external ones.
INTERNAL_MAP = {"nager": "nager", "google_trends": "google_trends", "github": "github", "apple": "apple"}
INTERNAL_IDS = frozenset(INTERNAL_MAP.values())

# Provenance every exported item/signal carries (source_url is the fallback when a signal has no URL).
PROVENANCE: dict[str, dict[str, str]] = {
    "wikimedia_pageviews": {"source_url": "https://dumps.wikimedia.org/other/pageviews/", "license": "CC0-1.0"},
    "wikipedia": {"source_url": "https://en.wikipedia.org/", "license": "CC-BY-SA-4.0",
                  "attribution": "Wikipedia contributors (title, date and link only; no article text)"},
    "wikidata": {"source_url": "https://www.wikidata.org/", "license": "CC0-1.0"},
    "gdelt": {"source_url": "https://www.gdeltproject.org/", "attribution": "GDELT Project"},
    "hn": {"source_url": "https://news.ycombinator.com/",
           "attribution": "Hacker News (Y Combinator) via Algolia HN Search API"},
    "federal_register": {"source_url": "https://www.federalregister.gov/", "license": "Public domain (17 U.S.C. 105)"},
    "nager": {"source_url": "https://date.nager.at/", "license": "MIT (internal use only)"},
    "google_trends": {"source_url": "https://trends.google.com/", "attribution": "Google Trends (internal use only)"},
    "github": {"source_url": "https://github.com/", "attribution": "GitHub (internal use only)"},
    "apple": {"source_url": "https://rss.marketingtools.apple.com/", "attribution": "Apple Marketing Tools RSS (internal use only)"},
}

MAX_ITEMS_PER_NICHE = 100     # keeps trends.json small: the Worker has 10 ms CPU on the free tier
MAX_EVIDENCE_PER_ITEM = 20


class ExportError(ValueError):
    pass


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48].strip("-")
    if not s:
        raise ExportError(f"cannot derive a niche id from {name!r}")
    return s


def hosted_source(local: str, include_internal: bool = False) -> str | None:
    if local in PUBLIC_MAP:
        return PUBLIC_MAP[local]
    return INTERNAL_MAP.get(local) if include_internal else None


def _prov(hosted: str, url: str | None) -> dict[str, str]:
    p = dict(PROVENANCE[hosted])
    if url and url.startswith(("http://", "https://")) and len(url) <= 2048:
        p["source_url"] = url
    out = {"source": hosted, **p}
    if hosted in INTERNAL_IDS:
        out["scope"] = "internal"
    return out


def _iso(d: datetime | date | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return d.isoformat()


def _h(*parts: str, n: int = 12) -> str:
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()[:n]


def _prio(s: Signal) -> tuple[int, int]:
    i = LABEL_PRIORITY.index(s.source) if s.source in LABEL_PRIORITY else len(LABEL_PRIORITY)
    return (i, s.rank or 10_000)


# ---------------------------------------------------------------------------------------------

def clean_topic(topic: Topic, include_internal: bool, now: datetime, entity_names: set[str]) -> Topic | None:
    """The topic restricted to hosted-allowed signals, re-scored if anything was dropped."""
    kept = [s for s in topic.signals if hosted_source(s.source, include_internal)]
    if not kept:
        return None
    if len(kept) == len(topic.signals):
        return topic
    label = topic.label
    if label not in {s.term for s in kept} and label.lower() not in entity_names:
        label = min(kept, key=lambda s: (_prio(s), len(s.term))).term   # label came from a dropped source
    region, precision, countries = rg.combine(kept)     # dropped signals may have been the ones of another country
    return score_topic(Topic(key=topic.key, label=label, signals=kept, niche=topic.niche, region=region,
                             region_precision=precision, regions=countries), now)


def trend_item(niche_id: str, topic: Topic, include_internal: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    # public sources first, so an item's own `source` is redistributable whenever one exists
    sigs = sorted(topic.signals, key=lambda s: (hosted_source(s.source, include_internal) in INTERNAL_IDS, _prio(s), -(s.score or 0)))
    primary = sigs[0]
    hs = hosted_source(primary.source, include_internal)
    assert hs is not None
    item_id = f"{niche_id}~{_h(topic.key)}"
    url = next((s.url for s in sigs if s.url), None)
    evidence = []
    for s in sigs[:MAX_EVIDENCE_PER_ITEM]:
        e = {**_prov(hosted_source(s.source, include_internal) or hs, s.url), "term": s.term, "seen_at": _iso(s.seen_at),
             "region": s.region, "region_precision": s.region_precision}
        if s.score is not None:
            e["score"] = s.score
        if s.rank is not None:
            e["rank"] = s.rank
        evidence.append(e)
    item = {
        "id": item_id, "title": topic.label, "score": round(topic.score, 1), "why": topic.why, "url": url,
        "at": _iso(max(s.seen_at for s in sigs)),
        **_prov(hs, primary.url),
        "sources": sorted({hosted_source(s.source, include_internal) for s in sigs}),   # type: ignore[type-var]
        "evidence_count": len(sigs),
        "region": topic.region, "region_precision": topic.region_precision, "regions": list(topic.regions),
    }
    record = {"title": topic.label, **_prov(hs, primary.url), "evidence": evidence}
    if set(item["sources"]) & INTERNAL_IDS:
        # score and why of this topic may derive from an internal-only source: the whole item is internal
        item["scope"] = record["scope"] = "internal"
    return item, record


def upcoming_item(niche_id: str, e: Event, include_internal: bool) -> dict[str, Any] | None:
    hs = hosted_source(e.source, include_internal)
    if hs is None:
        return None
    return {
        "id": f"{niche_id}~e{_h(e.kind, e.title, e.date.isoformat(), n=11)}", "kind": e.kind, "title": e.title,
        "date": e.date.isoformat(), "date_precision": e.date_precision,
        "publish_by": e.publish_by.isoformat() if e.publish_by else None, "url": e.url,
        "region": e.region, "region_precision": e.region_precision,
        **_prov(hs, e.url),
    }


@dataclass
class NicheOut:
    id: str
    name: str
    project: str
    niche_names: list[str | None]          # local niche names feeding this hosted niche (None = whole project)
    url: str | None = None


@dataclass
class Summary:
    files: list[Path] = field(default_factory=list)
    niches: dict[str, dict[str, int]] = field(default_factory=dict)
    dropped_topics: int = 0
    dropped_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"files": [str(f) for f in self.files], "niches": self.niches,
                "dropped_topics": self.dropped_topics, "dropped_events": self.dropped_events}


def _write(out: Path, rel: str, data: dict[str, Any], summary: Summary) -> None:
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    summary.files.append(path)


def export(cfg: Any, out_dir: str | Path, *, project: str | None = None, days: int = 30,
           upcoming_days: int = 365, custom_key: str | None = None, niche_id: str | None = None,
           name: str | None = None, include_internal: bool = False, now: datetime | None = None) -> Summary:
    """Write ``<out>/catalog/...`` (every niche of the selected projects) or, with ``custom_key``,
    ``<out>/custom/<key_id>/...`` for ONE hosted niche made of the whole project."""
    now = now or datetime.now(timezone.utc)
    out = Path(out_dir)
    projects = cfg.selected(project)
    if not projects:
        raise ExportError("no projects configured")
    planned: list[NicheOut] = []
    if custom_key:
        if not re.fullmatch(r"k_[0-9a-f]{16}", custom_key):
            raise ExportError("--custom-key must be a key id like k_0123456789abcdef")
        if len(projects) != 1 or not niche_id:
            raise ExportError("custom export needs exactly one --project and --niche-id custom-<slug>")
        if not re.fullmatch(CUSTOM_PREFIX + r"[a-z0-9][a-z0-9-]{0,56}", niche_id):
            raise ExportError(f"--niche-id must look like {CUSTOM_PREFIX}<slug>")
        p = projects[0]
        planned.append(NicheOut(niche_id, name or (p.urls[0] if p.urls else p.name), p.name, [None], p.urls[0] if p.urls else None))
        base = f"custom/{custom_key}"
    else:
        for p in projects:
            for n in p.niches:
                nid = slug(n.name)
                if nid.startswith(CUSTOM_PREFIX):
                    raise ExportError(f"catalog niche id {nid!r} may not start with {CUSTOM_PREFIX!r} (reserved for custom niches)")
                if any(x.id == nid for x in planned):
                    raise ExportError(f"two niches map to the same hosted id {nid!r}")
                planned.append(NicheOut(nid, n.name, p.name, [n.name]))
        base = "catalog"

    if include_internal and not custom_key:
        raise ExportError("internal sources may only be written under custom/<key_id>/ of an internal key (--custom-key), never catalog/")
    summary = Summary()
    store = Store(cfg.data_dir)
    try:
        listing: list[dict[str, Any]] = []
        for no in planned:
            proj = next(p for p in projects if p.name == no.project)
            entity_names = {e.lower() for n in proj.niches for e in n.entities}
            since = now - timedelta(days=days)
            best: dict[str, Topic] = {}
            for nn in no.niche_names:
                for t in store.topics(no.project, nn, since=since):
                    c = clean_topic(t, include_internal, now, entity_names)
                    if c is None:
                        summary.dropped_topics += 1
                    elif c.key not in best or c.score > best[c.key].score:
                        best[c.key] = c
            topics = sorted(best.values(), key=lambda t: (-t.score, t.label))[:MAX_ITEMS_PER_NICHE]
            items, records = [], {}
            for t in topics:
                it, rec = trend_item(no.id, t, include_internal)
                items.append(it)
                records[it["id"]] = rec
            today = now.date()
            events: list[dict[str, Any]] = []
            for nn in no.niche_names:
                for e in store.events(no.project, since=today, until=today + timedelta(days=upcoming_days), niche=nn):
                    u = upcoming_item(no.id, e, include_internal)
                    if u is None:
                        summary.dropped_events += 1
                    else:
                        events.append(u)
            events = sorted({e["id"]: e for e in events}.values(), key=lambda e: (e["date"], e["title"]))
            gen = _iso(now)
            _write(out, f"{base}/{no.id}/trends.json", {"schema": SCHEMA_VERSION, "niche": no.id, "generated_at": gen, "items": items}, summary)
            _write(out, f"{base}/{no.id}/evidence.json", {"schema": SCHEMA_VERSION, "niche": no.id, "generated_at": gen, "items": records}, summary)
            _write(out, f"{base}/{no.id}/upcoming.json", {"schema": SCHEMA_VERSION, "niche": no.id, "generated_at": gen, "items": events}, summary)
            used = sorted({s for it in items for s in it["sources"]} | {e["source"] for e in events})
            entry: dict[str, Any] = {"id": no.id, "name": no.name, "sources": used, "updated_at": gen,
                                     "counts": {"trends": len(items), "upcoming": len(events)}}
            if custom_key and no.url:
                entry["url"] = no.url
            listing.append(entry)
            summary.niches[no.id] = entry["counts"]
    finally:
        store.close()

    index = out / base / "niches.json"
    if custom_key and index.is_file():   # a key has several custom niches: merge by id, replace the same id
        try:
            old = json.loads(index.read_text(encoding="utf-8")).get("niches", [])
        except (OSError, ValueError):
            old = []
        keep = [n for n in old if isinstance(n, dict) and n.get("id") not in {e["id"] for e in listing}]
        listing = sorted(keep + listing, key=lambda n: n["id"])
    _write(out, f"{base}/niches.json", {"schema": SCHEMA_VERSION, "generated_at": _iso(now), "niches": listing}, summary)
    return summary


def upload_commands(out_dir: str | Path, bucket: str) -> list[list[str]]:
    """``wrangler r2 object put`` argv lists for every exported file. NOT executed here."""
    out = Path(out_dir)
    cmds = []
    for f in sorted(p for p in out.rglob("*.json") if p.is_file()):
        key = f.relative_to(out).as_posix()
        cmds.append(["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}", "--file", str(f),
                     "--content-type", "application/json", "--remote"])
    return cmds


def main(argv: list[str] | None = None) -> int:
    from nicheclock.config import ConfigError, load_config, resolve_config_path

    ap = argparse.ArgumentParser(prog="python -m nicheclock.hosted_export", description=__doc__.split("\n")[0])
    ap.add_argument("--config", "-c", default=None)
    ap.add_argument("--out", required=True, help="staging directory for the R2 layout")
    ap.add_argument("--project", default=None, help="project name (default: all, catalog mode)")
    ap.add_argument("--days", type=int, default=30, help="topics window (the API serves up to 365)")
    ap.add_argument("--upcoming-days", type=int, default=365)
    ap.add_argument("--custom-key", default=None, help="key id: write custom/<key_id>/... instead of catalog/")
    ap.add_argument("--niche-id", default=None, help="hosted niche id for --custom-key, custom-<slug>")
    ap.add_argument("--name", default=None, help="display name for a custom niche")
    ap.add_argument("--include-internal", action="store_true",
                    help="also export internal-only sources (nager, google_trends, github, apple), tagged scope=internal; "
                         "requires --custom-key and that key MUST be an internal/admin key")
    ap.add_argument("--print-upload", metavar="BUCKET", default=None, help="print the wrangler upload commands (not run)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(resolve_config_path(args.config))
        s = export(cfg, args.out, project=args.project, days=args.days, upcoming_days=args.upcoming_days,
                   custom_key=args.custom_key, niche_id=args.niche_id, name=args.name,
                   include_internal=args.include_internal)
    except (ConfigError, ExportError) as e:
        print(f"hosted_export: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in s.to_dict().items() if k != "files"}, indent=2))
    print(f"wrote {len(s.files)} files under {args.out}", file=sys.stderr)
    if args.print_upload:
        for c in upload_commands(args.out, args.print_upload):
            print(shlex.join(c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

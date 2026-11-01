from __future__ import annotations

import json

from nicheclock.output import Feed


def render(feed: Feed) -> str:
    doc = {
        "project": feed.project,
        "urls": feed.urls,
        "generated_at": feed.generated_at.isoformat(),
        "regions": feed.regions,
        "region_mode": feed.region_mode,
        "niches": [{"name": n.name, "keywords": n.keywords, "entities": n.entities,
                    "formats": n.formats, "regions": n.regions, "region_mode": n.region_mode}
                   for n in feed.niches],
        "items": [i.to_dict() for i in feed.items],
        "upcoming": [e.to_dict() for e in feed.upcoming],
        "gaps": [g.to_dict() for g in feed.gaps],
        "low_confidence": [i.to_dict() for i in feed.low_confidence],
        "sources": {k: {"terms_url": u, "note": n} for k, (u, n) in feed.sources.items()},
    }
    if feed.region:
        doc["region"] = feed.region      # present on a per-country view: that country plus global items
    if feed.llm:
        doc["llm"] = feed.llm
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"

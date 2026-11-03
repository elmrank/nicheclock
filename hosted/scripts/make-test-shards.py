#!/usr/bin/env python3
"""Write test/shards/ (what the Worker's tests read through a fake ASSETS binding) from the fixture niche data of the page's tests:
the same shard.py the site build uses, run on site/trending/fixture/match/. Run it again after changing shard.py or the fixture.

    python3 hosted/scripts/make-test-shards.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from nicheclock.catalog import shard  # noqa: E402

SRC = REPO / "hosted" / "site" / "trending" / "fixture" / "match"
OUT = REPO / "hosted" / "test" / "shards"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    concepts = json.loads((SRC / "concepts.json").read_text(encoding="utf-8"))
    label_of = {r[0]: r[1] for r in concepts.get("labels", [])}
    label_of.update({r[0]: r[1] for r in concepts["rows"]})
    pool = json.loads((SRC / "pool-US.json").read_text(encoding="utf-8"))
    shard.build_pool("US", pool["rows"], label_of, OUT, pool["date"])
    shard.build_concepts(concepts["rows"], OUT, concepts["date"])
    obs = json.loads((SRC / "observances.json").read_text(encoding="utf-8"))
    rows = [[r[0], r[1], 0, 0, r[6], r[7], ["o", r[2], r[3], r[4], r[5]]] for r in obs["rows"]]
    meta = {k: obs[k] for k in ("lag_days", "source", "license", "recurrence") if k in obs}
    meta["categories"] = {"sports": "Sports", "other": "Other"}
    shard.build_events(rows, label_of, OUT, meta)
    print(f"wrote {len(list(OUT.iterdir()))} files, {sum(f.stat().st_size for f in OUT.iterdir()) // 1024} KB, to {OUT}")


if __name__ == "__main__":
    main()

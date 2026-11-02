#!/usr/bin/env python3
"""Write fixture/match/ from a real catalog build: a small cut of hosted/site/catalog-data/match/ for the offline tests of the
"Your niche" page (test-privacy.js, serve.py without catalog data). It keeps the United States pool rows that tie to the three
sample profiles plus the 500 most viewed, the names those rows need, and the sample bridges. Numbers are real pageviews of the
day in manifest.json; the page's sample-data banner (meta.sample) still says the rest of the fixture is invented.

    python3 hosted/site/trending/fixture/make_match_fixture.py [--from DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = {718, 103632, 685629, 5113, 904889, 3360560, 7802}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", type=Path, default=Path.home() / ".nicheclock-catalog" / "match", help="the dir `catalog match --match-out` wrote")
    a = ap.parse_args()
    out = HERE / "match"
    out.mkdir(exist_ok=True)
    pool = json.loads((a.src / "pool-US.json").read_text(encoding="utf-8"))
    keep = [r for r in pool["rows"] if any(h // 64 in SAMPLES for h in r[4]) or r[0] in SAMPLES]
    keep += sorted(pool["rows"], key=lambda r: -r[2])[:500]
    seen, rows = set(), []
    for r in keep:
        if r[0] not in seen:
            seen.add(r[0])
            rows.append(r)
    pool["rows"] = rows
    targets = {h // 64 for r in rows for h in r[4]} | SAMPLES
    concepts = json.loads((a.src / "concepts.json").read_text(encoding="utf-8"))
    obs = json.loads((a.src / "observances.json").read_text(encoding="utf-8"))
    obs_rows = [r for r in obs["rows"] if any(h // 64 in SAMPLES for h in r[6])][:30] + obs["rows"][:20]
    obs["rows"] = [r for i, r in enumerate(obs_rows) if r not in obs_rows[:i]]
    for r in obs["rows"]:
        targets |= {h // 64 for h in r[6]}
    bridges = json.loads((a.src / "bridges.json").read_text(encoding="utf-8"))
    bridges["bridges"] = {f"Q{q}": v for q, v in ((q, bridges["bridges"].get(f"Q{q}")) for q in SAMPLES) if v}
    needed = targets | seen | {x[0] for v in bridges["bridges"].values() for x in v}
    concepts["rows"] = [c for c in concepts["rows"] if c[0] in needed]
    concepts["labels"] = [c for c in concepts["labels"] if c[0] in needed]
    manifest = {"schema": 1, "date": pool["date"], "note": "a cut of a real build for tests (make_match_fixture.py)",
                "regions": {"US": {"file": "match/pool-US.json", "rows": len(rows), "rising": sum(1 for r in rows if r[6] & 8)}}}
    for name, doc in (("pool-US.json", pool), ("concepts.json", concepts), ("observances.json", obs), ("bridges.json", bridges), ("manifest.json", manifest)):
        (out / name).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        print(name, (out / name).stat().st_size)


if __name__ == "__main__":
    main()

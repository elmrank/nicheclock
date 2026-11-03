"""Shards of the niche pool for the Worker (nicheclock/catalog/shard.py): every row is reachable through its own qid and the targets of its
handles, the "named" units carry the class labels, no shard is above the cap, and the hash is the one the Worker computes."""
from __future__ import annotations

import json
import random
from pathlib import Path

from nicheclock.catalog import shard


def handle(qid: int, kind: int, sub: int) -> int:
    return qid * 64 + kind * 16 + sub


LABELS = {718: "chess", 10873124: "chess player", 5113: "bird", 7802: "bread", 99: "human"}


def rows(n: int = 400) -> list[list]:
    rnd = random.Random(7)
    out = [[718, "chess", 5000, 1.0, [], 0, 0, 0]]
    for i in range(n):
        hs = [handle(rnd.choice([10873124, 5113, 7802, 99]), rnd.choice([0, 1]), rnd.choice([0, 1, 2])), handle(718 if i % 3 == 0 else 5113, 2, 0)]
        out.append([1000 + i, f"Row {i}", rnd.randint(100, 9000), 1.0 + (i % 5) / 4, hs, 0, 8 if i % 2 else 0])
    return [[r[0], r[1], r[2], r[3], r[4], 0, r[6]] for r in out]


def read(out: Path, name: str, key: str) -> dict:
    man = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
    n = man["chunks"].get(key, 1) if "chunks" in man else 1
    keys = [key] if n == 1 else [f"{key}~{i}" for i in range(n)]
    merged: dict = {"r": [], "l": {}}
    for k in keys:
        f = out / f"{name}-{shard.fnv1a(k) % man['shards']:03d}.json"
        u = json.loads(f.read_text(encoding="utf-8"))["u"].get(k)
        if u:
            merged["r"] += u.get("r", [])
            merged["l"].update(u.get("l", {}))
    return merged


def test_fnv1a_vectors_match_the_worker() -> None:
    # the same numbers are asserted in hosted/test/profile-tools.test.ts
    assert shard.fnv1a("q718") == 2578526312
    assert shard.fnv1a("wchess") == 2139547790
    assert shard.fnv1a("tchess") == 2868594269
    assert shard.fnv1a("\u00e9") == 513665217


def test_every_row_is_in_its_own_unit_and_in_the_unit_of_each_handle_target(tmp_path: Path) -> None:
    rs = rows()
    shard.build_pool("US", rs, LABELS, tmp_path, "2026-11-01")
    for r in rs[:60]:
        assert r[0] in {x[0] for x in read(tmp_path, "pool-US", f"q{r[0]}")["r"]}
        for h in r[4]:
            assert r[0] in {x[0] for x in read(tmp_path, "pool-US", f"q{h // 64}")["r"]}
    chess = read(tmp_path, "pool-US", "q718")["r"]
    assert {r[0] for r in rs if any(h // 64 == 718 for h in r[4])} <= {x[0] for x in chess}
    assert all(len(x) == 6 for x in chess)                       # the 7-day series is not kept
    assert chess == sorted(chess, key=lambda r: (-r[2], r[0]))


def test_named_units_hold_the_rows_with_a_class_whose_name_has_the_word_and_its_label(tmp_path: Path) -> None:
    rs = rows()
    shard.build_pool("US", rs, LABELS, tmp_path, "2026-11-01")
    u = read(tmp_path, "pool-US", "wchess")
    want = {r[0] for r in rs if any(h // 64 == 10873124 and (h // 16) % 4 in (0, 1) and h % 16 <= 2 for h in r[4])}
    assert want and want == {x[0] for x in u["r"]}
    assert u["l"] == {"10873124": "chess player"}


def test_no_shard_is_over_the_cap_and_big_units_are_cut_into_chunks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shard, "UNIT_CAP", 4000)
    monkeypatch.setattr(shard, "SHARD_CAP", 20000)
    monkeypatch.setattr(shard, "SHARD_TARGET", 8000)
    rs = rows(900)
    man = shard.build_pool("US", rs, LABELS, tmp_path, "2026-11-01")
    assert man["chunks"], "a unit of hundreds of rows must be cut"
    assert man["max_shard_bytes"] <= 20000
    for f in tmp_path.glob("pool-US-*.json"):
        assert f.stat().st_size <= 20000
    big = max(man["chunks"], key=lambda k: man["chunks"][k])
    joined = read(tmp_path, "pool-US", big)["r"]
    assert len(joined) == len({r[0] for r in joined})
    assert joined == sorted(joined, key=lambda r: (-r[2], r[0]))


def test_concepts_are_found_by_qid_and_by_every_name(tmp_path: Path) -> None:
    rows_ = [[718, "chess", "abstract strategy game", 900, ["Chess game", "  Western  chess "]], [999, "Chess", "record label", 50, []]]
    shard.build_concepts(rows_, tmp_path, "2026-11-01")
    assert read(tmp_path, "concepts", "q718")["r"] == [] and json.loads(next(tmp_path.glob("concepts-*.json")).read_text())["u"]
    man = json.loads((tmp_path / "concepts.json").read_text())
    def unit(key: str) -> dict:
        return json.loads((tmp_path / f"concepts-{shard.fnv1a(key) % man['shards']:03d}.json").read_text())["u"][key]
    assert unit("tchess")["c"] == [[718, "chess", "abstract strategy game", 900], [999, "Chess", "record label", 50]]
    assert unit("twestern chess")["c"][0][0] == 718
    assert unit("q718")["c"] == ["chess", "abstract strategy game"]

"""Hash-sharded copies of the niche pool, the concept index and the events, for the Worker (MCP / REST profile tools).

The page downloads one whole pool per country and matches in the browser. The Worker has 10 ms of CPU per request on the free plan,
and JSON.parse of the 4.9 MB United States pool alone is several times that, so for the profile tools the same data is cut at build
time into shards of at most SHARD_CAP bytes. A call reads only the shards that hold the units it asks for.

Units (a unit is a list of pool rows, or of concepts, behind one key):

  q<qid>   every row whose own qid is <qid> or that has <qid> as a handle target (class, occupation, subject, taxon). A profile topic
           finds the rows tied to it in 1 to 3 shards.
  w<word>  every row that has a class or occupation handle (at most 2 steps) whose label contains <word>: the rows that can hold the
           "named" tie ("chess player" for the topic chess). `l` holds the labels of those classes, because the matcher needs them.

A unit larger than UNIT_CAP is cut into chunks `key~0`, `key~1`, ... (ordered by views, descending); the manifest lists the keys that
have more than one chunk. A unit goes to shard fnv1a(key) % n, and n is raised until no shard is above SHARD_CAP.

What a Worker answer can not contain, by construction: a row whose only tie to the topic is its title (fit at most 0.40) is in no unit
of the topic, because finding it needs a scan of every title. match.js scores such a row 0.40 for a main topic (0.32 for a secondary
one), so it is never at or above the 0.50 line; it can only be missing from the "weaker" band (0.35 to 0.49).

Files, per region ISO (pool) and for `events` and `concepts`:  <name>.json (manifest) and <name>-<k>.json (shards, `{"u": {key: unit}}`).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SHARD_CAP = 150_000          # bytes of JSON text per shard file (the Worker parses at most this per shard)
SHARD_TARGET = 24_000        # the first guess for the average shard size: small, because the Worker pays about 1 ms of CPU per 50 KB it parses
UNIT_CAP = 12_000            # a unit above this is cut into chunks, so hashing can never put one past SHARD_CAP
SCHEMA = 1
WORD = re.compile(r"[^\W_]+")
STOP = frozenset("of the and in on at to for a an de la le les el di der die das".split())

K_CLASS, K_OCC = 0, 1


def fnv1a(s: str) -> int:
    """32-bit FNV-1a over the UTF-8 bytes of s. The Worker (shards.ts) computes the same number."""
    h = 0x811C9DC5
    for b in s.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h


def words(label: str) -> set[str]:
    """Lower-cased letter-and-digit words of a label, without short ones and stop words. The topic's name is matched as a whole phrase
    by match.js; every phrase that matches contains each of its words, so the Worker looks up one of them (the longest)."""
    return {w for w in WORD.findall(label.lower()) if len(w) >= 2 and w not in STOP}


def _size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _chunks(rows: list[list[Any]]) -> list[list[list[Any]]]:
    out: list[list[list[Any]]] = [[]]
    used = 0
    for r in rows:
        n = _size(r) + 1
        if used + n > UNIT_CAP and out[-1]:
            out.append([])
            used = 0
        out[-1].append(r)
        used += n
    return out


def _place(units: dict[str, dict[str, Any]], out: Path, name: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Assign units to shards and write them. Returns the manifest."""
    sizes = {k: _size(u) + len(k) + 4 for k, u in units.items()}
    total = sum(sizes.values())
    n = max(1, -(-total // SHARD_TARGET))
    while True:
        load = [0] * n
        for k, s in sizes.items():
            load[fnv1a(k) % n] += s
        if max(load) + 16 <= SHARD_CAP:
            break
        n += max(1, n // 50)
    shards: list[dict[str, Any]] = [{} for _ in range(n)]
    for k, u in units.items():
        shards[fnv1a(k) % n][k] = u
    out.mkdir(parents=True, exist_ok=True)
    biggest = 0
    for i, sh in enumerate(shards):
        blob = json.dumps({"u": sh}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        biggest = max(biggest, len(blob))
        (out / f"{name}-{i:03d}.json").write_bytes(blob)
    manifest = {"schema": SCHEMA, "name": name, "shards": n, "units": len(units), "bytes": total, "max_shard_bytes": biggest, "shard_cap": SHARD_CAP, **extra}
    (out / f"{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return manifest


def _add(units: dict[str, dict[str, Any]], chunked: dict[str, int], key: str, rows: list[list[Any]], labels: dict[str, str] | None = None) -> None:
    """Add a unit; cut it into chunks when it is large. `labels` maps class qid (as text) to label for the rows of the unit."""
    rows = sorted(rows, key=lambda r: (-r[2], r[0]))
    parts = _chunks(rows)
    if len(parts) > 1:
        chunked[key] = len(parts)
    for i, part in enumerate(parts):
        k = f"{key}~{i}" if len(parts) > 1 else key
        u: dict[str, Any] = {"r": part}
        if labels:
            need = {str(h // 64) for r in part for h in r[4]}
            lab = {q: t for q, t in labels.items() if q in need}
            if lab:
                u["l"] = lab
        units[k] = u


def _index_rows(rows: Iterable[list[Any]], label_of: dict[int, str]) -> tuple[dict[str, list[list[Any]]], dict[str, list[list[Any]]], dict[str, dict[str, str]]]:
    """rows are [qid, title, views, ratio, handles, flags, ...extra]. Returns the q-units and w-units (with their class labels)."""
    q: dict[str, list[list[Any]]] = defaultdict(list)
    w: dict[str, list[list[Any]]] = defaultdict(list)
    wl: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        for t in {r[0]} | {h // 64 for h in r[4]}:
            q[f"q{t}"].append(r)
    for r in rows:
        seen: set[str] = set()
        for h in r[4]:
            kind, sub = (h // 16) % 4, h % 16
            if kind not in (K_CLASS, K_OCC) or sub > 2:
                continue
            lab = label_of.get(h // 64)
            if not lab:
                continue
            for word in words(lab):
                wl[f"w{word}"][str(h // 64)] = lab
                if f"w{word}" not in seen:
                    seen.add(f"w{word}")
                    w[f"w{word}"].append(r)
    return q, w, wl


def build_pool(region: str, rows: list[list[Any]], label_of: dict[int, str], out: Path, date: str = "") -> dict[str, Any]:
    """rows: pool rows [qid, title, views, ratio, handles, series, flags]. The 7-day series is not kept in the shards (a profile tool
    returns the last day's volume and the rise, not a sparkline)."""
    slim = [[r[0], r[1], r[2], r[3], r[4], r[6]] for r in rows]
    q, w, wl = _index_rows(slim, label_of)
    units: dict[str, dict[str, Any]] = {}
    chunked: dict[str, int] = {}
    for key, rs in q.items():
        _add(units, chunked, key, rs)
    for key, rs in w.items():
        _add(units, chunked, key, rs, wl[key])
    return _place(units, out, f"pool-{region}", {"region": region, "date": date, "rows": len(slim), "chunks": chunked})


def build_events(rows: list[list[Any]], label_of: dict[int, str], out: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """rows: [qid, title, 0, 0, handles, flags, extra] where extra is ["e", event dict] or ["o", date, rule, popularity, category]."""
    q, w, wl = _index_rows(rows, label_of)
    units: dict[str, dict[str, Any]] = {}
    chunked: dict[str, int] = {}
    for key, rs in q.items():
        _add(units, chunked, key, rs)
    for key, rs in w.items():
        _add(units, chunked, key, rs, wl[key])
    return _place(units, out, "events", {"rows": len(rows), "chunks": chunked, "meta": meta})


def norm_text(s: str) -> str:
    """The key of a concept's name: lower case, runs of whitespace as one space. The Worker applies the same function."""
    return " ".join(s.lower().split())


def build_concepts(rows: list[list[Any]], out: Path, date: str) -> dict[str, Any]:
    """rows: concepts.json rows [qid, label, description, popularity, aliases]. Units: q<qid> -> {"c": [label, description]}, and
    t<name> -> {"c": [[qid, label, description, popularity], ...]} for the label and every alias (best 8 by popularity)."""
    units: dict[str, dict[str, Any]] = {}
    by_text: dict[str, list[list[Any]]] = defaultdict(list)
    for r in rows:
        units[f"q{r[0]}"] = {"c": [r[1], r[2]]}
        for name in {norm_text(r[1])} | {norm_text(a) for a in r[4]}:
            if name:
                by_text[name].append([r[0], r[1], r[2], r[3]])
    for name, cs in by_text.items():
        cs.sort(key=lambda c: (-c[3], c[0]))
        units[f"t{name}"] = {"c": cs[:8]}
    return _place(units, out, "concepts", {"rows": len(rows), "date": date})

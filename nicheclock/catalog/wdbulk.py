"""Bulk Wikidata reader for the niche pool: lean entity records in their own SQLite file, fetched in batches.

The pool needs the statements of about a hundred thousand entities (pages, their classes, occupations, subjects and
taxon parents), so it does not go through ``Wikidata.entities`` (which keeps labels in 13 languages and sitelinks).
This module keeps only what matching reads and asks Wikidata politely:

* ``wbgetentities`` with 50 ids per call (the documented limit), ``maxlag=5``, an identity User-Agent and ``From``;
* at most ``THREADS`` (5) calls in flight;
* ``Retry-After`` is honoured for every thread at once (one shared pause), 5xx and ``maxlag`` answers back off, and a
  run that is throttled four times in a row stops instead of pushing on.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

API = "https://www.wikidata.org/w/api.php"
BATCH = 50
THREADS = 5
TTL = 30 * 86400                  # statements about classes and pages move slowly; a month is plenty
MAX_PAUSE = 300.0                 # a longer Retry-After ends the run instead of sleeping on
# Statements kept per entity: classes, occupations, the properties that tie a page to its subject, the taxon parent and rank.
PROPS = ("P31", "P279", "P106", "P641", "P921", "P361", "P179", "P8345", "P136", "P800", "P171", "P105")

MISSING = object()                # store.get() result for an id that was never read


class Throttled(Exception):
    """Wikidata kept answering 429 / maxlag: the run stops and keeps what it has."""


def lean_entity(raw: dict[str, Any]) -> dict[str, Any] | None:
    """``{"l": label, "d": description, "a": [aliases], "c": {property: [qids]}}``; None when the entity is missing."""
    if not raw or "missing" in raw:
        return None
    labels, descs, aliases = raw.get("labels") or {}, raw.get("descriptions") or {}, raw.get("aliases") or {}
    claims: dict[str, list[str]] = {}
    for prop in PROPS:
        vals: list[str] = []
        for st in (raw.get("claims") or {}).get(prop, []):
            if st.get("rank") == "deprecated":
                continue
            snak = st.get("mainsnak") or {}
            v = (snak.get("datavalue") or {}).get("value")
            if snak.get("snaktype") == "value" and isinstance(v, dict) and v.get("id"):
                vals.append(v["id"])
        if vals:
            claims[prop] = vals
    return {"l": (labels.get("en") or {}).get("value") or "", "d": ((descs.get("en") or {}).get("value") or "")[:110],
            "a": [a["value"] for a in aliases.get("en", [])][:5], "c": claims}


class EntityStore:
    """``store.get(qid)`` -> lean record, ``None`` (known to be missing) or ``MISSING`` (never read / stale)."""

    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time, ttl: float = TTL) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS ent (q TEXT PRIMARY KEY, v TEXT, at REAL)")
        self._clock, self.ttl = clock, ttl
        self._mem: dict[str, Any] = {}

    def load(self) -> int:
        """Read every fresh record into memory (a walk touches the same classes thousands of times)."""
        cutoff = self._clock() - self.ttl
        self._mem = {q: json.loads(v) for q, v in self.db.execute("SELECT q, v FROM ent WHERE at >= ?", (cutoff,))}
        return len(self._mem)

    def get(self, qid: str) -> Any:
        return self._mem.get(qid, MISSING)

    def put_many(self, items: dict[str, dict[str, Any] | None]) -> None:
        now = self._clock()
        # up to 30% of the ttl is taken off each record's age by its id, so a first backfill does not expire all at once a month later
        age = lambda q: self.ttl * 0.3 * (zlib.crc32(q.encode()) % 1000) / 1000
        self.db.executemany("INSERT OR REPLACE INTO ent (q, v, at) VALUES (?, ?, ?)",
                            [(q, json.dumps(v, ensure_ascii=False, separators=(",", ":")), now - age(q)) for q, v in items.items()])
        self.db.commit()
        self._mem.update(items)

    def close(self) -> None:
        self.db.commit()
        self.db.close()


class BulkFetcher:
    def __init__(self, store: EntityStore, user_agent: str, contact: str, *, threads: int = THREADS,
                 sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print,
                 session_factory: Callable[[], Any] = requests.Session) -> None:
        if not user_agent or not contact:
            raise ValueError("an identity (user agent and contact email) is required")
        self.store, self.ua, self.contact = store, user_agent, contact
        self.threads, self._sleep, self.log, self._session_factory = min(threads, THREADS), sleep, log, session_factory
        self._pause_until = 0.0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._throttled_in_a_row = 0
        self.calls = 0
        self.failed: list[str] = []

    # -- one call ---------------------------------------------------------------------------
    def _session(self) -> Any:
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._session_factory()
            s.headers.update({"User-Agent": self.ua, "From": self.contact, "Accept-Encoding": "gzip"})
            self._local.s = s
        return s

    def _pause(self, seconds: float) -> None:
        with self._lock:
            self._pause_until = max(self._pause_until, time.monotonic() + seconds)

    def _wait_turn(self) -> None:
        while True:
            with self._lock:
                wait = self._pause_until - time.monotonic()
            if wait <= 0:
                return
            self._sleep(min(wait, 5.0))

    def _call(self, ids: list[str]) -> dict[str, Any]:
        return self.get_json(API, {"action": "wbgetentities", "ids": "|".join(ids), "props": "labels|descriptions|aliases|claims",
                                   "languages": "en", "format": "json", "formatversion": 2, "maxlag": 5})

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        """One MediaWiki Action API GET with the shared pause, Retry-After and maxlag handling (``maxlag`` is the caller's)."""
        for attempt in range(8):
            self._wait_turn()
            with self._lock:
                self.calls += 1
            try:
                resp = self._session().get(url, params=params, timeout=90)
            except requests.RequestException:
                self._sleep(min(2.0 ** attempt, 30.0))
                continue
            retry = _retry_after(resp)
            if resp.status_code == 429 or resp.status_code == 503:
                with self._lock:
                    self._throttled_in_a_row += 1
                    if self._throttled_in_a_row >= 4:
                        raise Throttled(f"wikidata answered {resp.status_code} four times in a row")
                wait = retry if retry is not None else min(5.0 * (attempt + 1), 60.0)
                if wait > MAX_PAUSE:
                    raise Throttled(f"wikidata asked to wait {wait:.0f}s")
                self._pause(wait)
                continue
            if resp.status_code != 200:
                self._sleep(min(2.0 ** attempt, 30.0))
                continue
            data = resp.json()
            err = data.get("error")
            if err:
                if err.get("code") == "maxlag":
                    self._pause(retry if retry is not None else 5.0)
                    continue
                raise RuntimeError(f"wikidata: {err.get('code')}: {err.get('info')}")
            with self._lock:
                self._throttled_in_a_row = 0
            return data
        raise RuntimeError("wikidata: too many failed attempts for one batch")

    # -- many ids ---------------------------------------------------------------------------
    def fetch(self, qids: Iterable[str]) -> int:
        """Read every id that is not fresh in the store; returns how many were fetched. Ids that fail stay out of the
        store and are listed in ``self.failed`` (the caller decides whether that is fatal)."""
        todo = [q for q in dict.fromkeys(qids) if self.store.get(q) is MISSING]
        if not todo:
            return 0
        chunks = [todo[n:n + BATCH] for n in range(0, len(todo), BATCH)]
        done = 0
        with ThreadPoolExecutor(self.threads) as pool:
            futures = {pool.submit(self._call, c): c for c in chunks}
            pending: dict[str, dict[str, Any] | None] = {}
            try:
                for fut in as_completed(futures):
                    chunk = futures[fut]
                    try:
                        data = fut.result()
                    except Throttled:
                        for f in futures:
                            f.cancel()
                        raise
                    except Exception as e:                      # one bad batch must not lose the others
                        self.failed.extend(chunk)
                        self.log(f"  batch failed ({len(chunk)} ids): {str(e)[:120]}")
                        continue
                    ents = data.get("entities") or {}
                    for q in chunk:
                        pending[q] = lean_entity(ents.get(q) or {})
                    done += len(chunk)
                    if len(pending) >= 1000:
                        self.store.put_many(pending)
                        pending = {}
                        self.log(f"  {done}/{len(todo)} entities read")
            finally:
                if pending:
                    self.store.put_many(pending)
        return done


def _retry_after(resp: Any) -> float | None:
    raw = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None

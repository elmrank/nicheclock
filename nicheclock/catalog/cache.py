"""A small SQLite key/value cache with per-read TTL, shared by the resolver, classifier and builders."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable


class Cache:
    """``Cache(path)`` (or ``Cache(":memory:")``). Values are JSON; ``None`` is a cacheable value."""

    def __init__(self, path: str | Path = ":memory:", clock: Callable[[], float] = time.time) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, at REAL, PRIMARY KEY (ns, k))")
        self._clock = clock
        self.hits = 0
        self.misses = 0

    def get(self, ns: str, key: str, ttl: float | None = None) -> tuple[bool, Any]:
        """(found, value). Entries older than ``ttl`` seconds count as not found."""
        row = self.db.execute("SELECT v, at FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
        if row is None or (ttl is not None and self._clock() - row[1] > ttl):
            self.misses += 1
            return False, None
        self.hits += 1
        return True, json.loads(row[0])

    def put(self, ns: str, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv (ns, k, v, at) VALUES (?, ?, ?, ?)",
                        (ns, key, json.dumps(value, ensure_ascii=False), self._clock()))

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()

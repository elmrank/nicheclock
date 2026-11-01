"""SQLite store at ``<data_dir>/nicheclock.db``: signals, topics, events, runs, gsc_rows, meta."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from nicheclock.models import Event, Signal, Topic, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, status TEXT NOT NULL DEFAULT 'running', stats TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, niche TEXT NOT NULL, source TEXT NOT NULL,
  term TEXT NOT NULL, url TEXT, rank INTEGER, score REAL, geo TEXT NOT NULL DEFAULT '',
  seen_at TEXT NOT NULL, day TEXT NOT NULL, extra TEXT NOT NULL DEFAULT '{}', run_id INTEGER,
  region TEXT NOT NULL DEFAULT 'global', region_precision TEXT NOT NULL DEFAULT 'global',
  UNIQUE(project, niche, source, term, geo, region, day));
CREATE INDEX IF NOT EXISTS signals_pn ON signals(project, niche, seen_at);
CREATE TABLE IF NOT EXISTS topics (
  id INTEGER PRIMARY KEY, run_id INTEGER, project TEXT NOT NULL, niche TEXT NOT NULL,
  key TEXT NOT NULL, label TEXT NOT NULL, score REAL NOT NULL, why TEXT NOT NULL DEFAULT '',
  url TEXT, signals TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT 'global', region_precision TEXT NOT NULL DEFAULT 'global',
  regions TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS topics_pn ON topics(project, niche, created_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, niche TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
  title TEXT NOT NULL, date TEXT NOT NULL, date_precision TEXT NOT NULL DEFAULT 'day',
  source TEXT NOT NULL DEFAULT '', url TEXT, publish_by TEXT, first_seen TEXT NOT NULL,
  region TEXT NOT NULL DEFAULT 'global', region_precision TEXT NOT NULL DEFAULT 'global',
  UNIQUE(project, niche, kind, title, date));
CREATE TABLE IF NOT EXISTS gsc_rows (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, date TEXT, query TEXT, page TEXT,
  clicks REAL, impressions REAL, ctr REAL, position REAL, window TEXT, fetched_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gsc_p ON gsc_rows(project);
CREATE TABLE IF NOT EXISTS meta (
  project TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(project, key));
CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY, day TEXT NOT NULL, project TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL,
  model TEXT NOT NULL, task TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0, cost_usd_est REAL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS llm_usage_day ON llm_usage(day);
CREATE TABLE IF NOT EXISTS llm_cache (
  project TEXT NOT NULL, task TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  created_at TEXT NOT NULL, PRIMARY KEY(project, task, key));
"""

# Columns added after the first release: (table, column, type). Applied to databases that predate them.
_ADDED_COLUMNS = (
    ("topics", "rule_score", "REAL"), ("topics", "llm_fit", "REAL"), ("topics", "llm_reason", "TEXT"),
    ("topics", "llm_status", "TEXT"), ("topics", "llm_note", "TEXT"),
    ("topics", "region", "TEXT NOT NULL DEFAULT 'global'"),
    ("topics", "region_precision", "TEXT NOT NULL DEFAULT 'global'"),
    ("topics", "regions", "TEXT NOT NULL DEFAULT '[]'"),
    ("events", "region", "TEXT NOT NULL DEFAULT 'global'"),
    ("events", "region_precision", "TEXT NOT NULL DEFAULT 'global'"),
)

_GSC_COLS = ("date", "query", "page", "clicks", "impressions", "ctr", "position", "window")


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d is not None else None


def _region_sql(q: str, a: list, region: str | None, has_regions: bool = False) -> tuple[str, list]:
    """Region filter: a country keeps its own rows plus the global ones. Rows of several countries
    ('multi', topics only) are kept when the country is among their ``regions``."""
    if region and region.lower() != "global":
        code = region.upper()
        if has_regions:
            q += " AND (region IN (?, 'global') OR (region = 'multi' AND regions LIKE ?))"
            a = a + [code, f'%"{code}"%']
        else:
            q += " AND region IN (?, 'global')"
            a = a + [code]
    return q, a


class Store:
    """``Store(data_dir)``; ``data_dir`` is created. Use as a context manager or call close()."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "nicheclock.db"
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Databases written before regions existed: add the columns (signals gets a new unique key)."""
        def cols(table: str) -> set[str]:
            return {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}

        if "region" not in cols("signals"):
            self.db.executescript("""
                ALTER TABLE signals RENAME TO signals_old;
                DROP INDEX IF EXISTS signals_pn;""")
            self.db.executescript(SCHEMA)       # recreates signals (+ index) with the region columns
            self.db.execute(
                "INSERT OR IGNORE INTO signals(id, project, niche, source, term, url, rank, score, geo,"
                " seen_at, day, extra, run_id) SELECT id, project, niche, source, term, url, rank, score,"
                " geo, seen_at, day, extra, run_id FROM signals_old")
            self.db.execute("DROP TABLE signals_old")
        for table, col, typ in _ADDED_COLUMNS:
            if col not in cols(table):
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()

    @classmethod
    def open(cls, cfg: Any) -> "Store":
        return cls(cfg.data_dir)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- runs ------------------------------------------------------------------------------
    def start_run(self, project: str) -> int:
        cur = self.db.execute("INSERT INTO runs(project, started_at) VALUES (?, ?)",
                              (project, utcnow().isoformat()))
        self.db.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, stats: dict[str, Any], status: str = "ok") -> None:
        self.db.execute("UPDATE runs SET finished_at=?, status=?, stats=? WHERE id=?",
                        (utcnow().isoformat(), status, json.dumps(stats), run_id))
        self.db.commit()

    def last_run(self, project: str) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM runs WHERE project=? ORDER BY id DESC LIMIT 1",
                            (project,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["stats"] = json.loads(d["stats"])
        return d

    # -- signals ---------------------------------------------------------------------------
    def add_signals(self, project: str, niche: str, signals: Iterable[Signal],
                    run_id: int | None = None) -> int:
        """Upsert by (project, niche, source, term, geo, region, day): a rerun the same day replaces."""
        n = 0
        for s in signals:
            self.db.execute(
                "INSERT OR REPLACE INTO signals(project, niche, source, term, url, rank, score, geo,"
                " seen_at, day, extra, run_id, region, region_precision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project, niche, s.source, s.term, s.url, s.rank, s.score, s.geo or "",
                 s.seen_at.isoformat(), s.seen_at.date().isoformat(), json.dumps(s.extra), run_id,
                 s.region, s.region_precision))
            n += 1
        self.db.commit()
        return n

    @staticmethod
    def _signal(r: sqlite3.Row) -> Signal:
        return Signal(source=r["source"], term=r["term"], url=r["url"], rank=r["rank"],
                      score=r["score"], geo=r["geo"] or None,
                      seen_at=datetime.fromisoformat(r["seen_at"]), extra=json.loads(r["extra"]),
                      region=r["region"], region_precision=r["region_precision"])

    def signals_since(self, project: str, niche: str | None = None,
                      since: datetime | None = None, region: str | None = None) -> list[Signal]:
        q, a = "SELECT * FROM signals WHERE project=?", [project]
        q, a = _region_sql(q, a, region)
        if niche is not None:
            q += " AND niche=?"
            a.append(niche)
        if since is not None:
            q += " AND seen_at>=?"
            a.append(since.isoformat())
        return [self._signal(r) for r in self.db.execute(q + " ORDER BY seen_at, id", a)]

    def recent_signals(self, project: str | None = None, niche: str | None = None,
                       days: int = 7, region: str | None = None) -> list[Signal]:
        since = utcnow() - timedelta(days=days)
        q, a = "SELECT * FROM signals WHERE seen_at>=?", [since.isoformat()]
        q, a = _region_sql(q, a, region)
        if project is not None:
            q += " AND project=?"
            a.append(project)
        if niche is not None:
            q += " AND niche=?"
            a.append(niche)
        return [self._signal(r) for r in self.db.execute(q + " ORDER BY seen_at, id", a)]

    # -- topics ----------------------------------------------------------------------------
    def save_topics(self, run_id: int | None, project: str, niche: str,
                    topics: Iterable[Topic]) -> None:
        now = utcnow().isoformat()
        for t in topics:
            url = next((s.url for s in t.signals if s.url), None)
            self.db.execute(
                "INSERT INTO topics(run_id, project, niche, key, label, score, why, url, signals,"
                " created_at, rule_score, llm_fit, llm_reason, llm_status, llm_note,"
                " region, region_precision, regions)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, project, niche, t.key, t.label, t.score, t.why, url,
                 json.dumps([s.to_dict() for s in t.signals]), now,
                 t.rule_score, t.llm_fit, t.llm_reason, t.llm_status, t.llm_note,
                 t.region, t.region_precision, json.dumps(t.regions)))
        self.db.commit()

    def topics(self, project: str, niche: str | None = None,
               since: datetime | None = None, include_hidden: bool = False,
               region: str | None = None) -> list[Topic]:
        """Topics since ``since`` (default: last 7 days), one per (niche, key): best score, newest
        run first; sorted by score descending. Topics the model hid (junk, merged into another)
        are left out unless ``include_hidden``. ``region`` keeps that country's topics plus global
        and multi-country ones."""
        since = since or (utcnow() - timedelta(days=7))
        q, a = "SELECT * FROM topics WHERE project=? AND created_at>=?", [project, since.isoformat()]
        if not include_hidden:
            q += " AND llm_status IS NULL"
        q, a = _region_sql(q, a, region, has_regions=True)
        if niche is not None:
            q += " AND niche=?"
            a.append(niche)
        best: dict[tuple[str, str, str], Topic] = {}
        for r in self.db.execute(q + " ORDER BY id DESC", a):
            k = (r["niche"], r["key"], r["region"])
            t = Topic(key=r["key"], label=r["label"], niche=r["niche"], score=r["score"],
                      why=r["why"], signals=[Signal.from_dict(s) for s in json.loads(r["signals"])],
                      rule_score=r["rule_score"], llm_fit=r["llm_fit"], llm_reason=r["llm_reason"],
                      llm_status=r["llm_status"], llm_note=r["llm_note"], region=r["region"],
                      region_precision=r["region_precision"], regions=json.loads(r["regions"] or "[]"))
            if k not in best or t.score > best[k].score:
                best[k] = t
        return sorted(best.values(), key=lambda t: -t.score)

    # -- events ----------------------------------------------------------------------------
    def save_events(self, project: str, events: Iterable[Event]) -> int:
        n = 0
        for e in events:
            self.db.execute(
                "INSERT INTO events(project, niche, kind, title, date, date_precision, source, url,"
                " publish_by, first_seen, region, region_precision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project, niche, kind, title, date) DO UPDATE SET url=excluded.url,"
                " publish_by=excluded.publish_by, date_precision=excluded.date_precision,"
                " source=excluded.source, region=excluded.region,"
                " region_precision=excluded.region_precision",
                (project, e.niche or "", e.kind, e.title, _iso(e.date), e.date_precision, e.source,
                 e.url, _iso(e.publish_by), utcnow().isoformat(), e.region, e.region_precision))
            n += 1
        self.db.commit()
        return n

    def events(self, project: str, since: date | None = None, until: date | None = None,
               niche: str | None = None, region: str | None = None) -> list[Event]:
        """Events with ``since <= date <= until`` (default since = today), soonest first."""
        since = since or date.today()
        q, a = "SELECT * FROM events WHERE project=? AND date>=?", [project, since.isoformat()]
        q, a = _region_sql(q, a, region)
        if until is not None:
            q += " AND date<=?"
            a.append(until.isoformat())
        if niche is not None:
            q += " AND niche=?"
            a.append(niche)
        out = []
        for r in self.db.execute(q + " ORDER BY date, title", a):
            out.append(Event.from_dict({**dict(r), "niche": r["niche"] or None}))
        return out

    # -- gsc -------------------------------------------------------------------------------
    def add_gsc_rows(self, project: str, rows: Iterable[dict[str, Any]]) -> int:
        """Rows are dicts with date, query, page, clicks, impressions, ctr, position,
        window ('cur'|'prev')."""
        now, n = utcnow().isoformat(), 0
        for r in rows:
            self.db.execute(
                "INSERT INTO gsc_rows(project, date, query, page, clicks, impressions, ctr, position,"
                " window, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, *[r.get(c) for c in _GSC_COLS], now))
            n += 1
        self.db.commit()
        return n

    def clear_gsc_rows(self, project: str) -> None:
        self.db.execute("DELETE FROM gsc_rows WHERE project=?", (project,))
        self.db.commit()

    def replace_gsc_rows(self, project: str, rows: Iterable[dict[str, Any]]) -> int:
        self.clear_gsc_rows(project)
        return self.add_gsc_rows(project, rows)

    def gsc_rows(self, project: str) -> list[dict[str, Any]]:
        return [{c: r[c] for c in _GSC_COLS}
                for r in self.db.execute("SELECT * FROM gsc_rows WHERE project=? ORDER BY id", (project,))]

    # -- meta ------------------------------------------------------------------------------
    def set_meta(self, project: str, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(project, key, value) VALUES (?,?,?)",
                        (project, key, json.dumps(value)))
        self.db.commit()

    def get_meta(self, project: str, key: str, default: Any = None) -> Any:
        r = self.db.execute("SELECT value FROM meta WHERE project=? AND key=?", (project, key)).fetchone()
        return default if r is None else json.loads(r["value"])

    # -- llm ledger and cache --------------------------------------------------------------
    def llm_record(self, day: str, provider: str, model: str, task: str, input_tokens: int,
                   output_tokens: int, cost_usd_est: float | None, project: str = "") -> None:
        self.db.execute(
            "INSERT INTO llm_usage(day, project, provider, model, task, input_tokens, output_tokens,"
            " cost_usd_est, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (day, project, provider, model, task, input_tokens, output_tokens, cost_usd_est,
             utcnow().isoformat()))
        self.db.commit()

    def llm_spent(self, day: str) -> float:
        """Estimated USD spent on ``day`` (calls with an unknown price count as 0)."""
        r = self.db.execute("SELECT COALESCE(SUM(cost_usd_est), 0) AS s FROM llm_usage WHERE day=?",
                            (day,)).fetchone()
        return float(r["s"])

    def llm_usage(self, since_day: str) -> list[dict[str, Any]]:
        """Per (day, provider, model, task): calls, tokens and cost since ``since_day`` (inclusive)."""
        rows = self.db.execute(
            "SELECT day, provider, model, task, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
            " SUM(output_tokens) AS output_tokens, SUM(cost_usd_est) AS cost_usd_est,"
            " SUM(cost_usd_est IS NULL) AS unpriced FROM llm_usage WHERE day>=?"
            " GROUP BY day, provider, model, task ORDER BY day DESC, provider, model, task", (since_day,))
        return [dict(r) for r in rows]

    def llm_cache_get(self, project: str, task: str, key: str, max_age_days: float) -> Any:
        r = self.db.execute("SELECT value, created_at FROM llm_cache WHERE project=? AND task=? AND key=?",
                            (project, task, key)).fetchone()
        if r is None:
            return None
        if utcnow() - datetime.fromisoformat(r["created_at"]) > timedelta(days=max_age_days):
            return None
        return json.loads(r["value"])

    def llm_cache_put(self, project: str, task: str, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO llm_cache(project, task, key, value, created_at)"
                        " VALUES (?,?,?,?,?)", (project, task, key, json.dumps(value), utcnow().isoformat()))
        self.db.commit()

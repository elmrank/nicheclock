"""Google Search Console: bring-your-own OAuth client, query gaps, index lag.

Flow
----
``nicheclock connect gsc --project NAME`` runs the installed-app OAuth flow with the
user's *own* ``client_secret.json`` (``projects[].gsc.client_secret``), caches the token
under ``~/.config/nicheclock/`` (``$NICHECLOCK_CONFIG_DIR`` / ``$XDG_CONFIG_HOME`` honoured,
mode 0600) and then syncs. ``sync`` pulls ``searchanalytics.query`` for the last 28 days
(``cur``) and the 28 days before (``prev``), stores the rows in the store's ``gsc_rows`` and
measures per-site index lag (days from sitemap ``lastmod`` to first impression), saved as
``set_meta(project, 'index_lag_days', n)`` for ``upcoming.py``.

``gaps(project, store)`` reads only stored rows: queries with enough impressions whose best
position is worse than 20, or that no known page's URL/title covers.

No secrets live in the repo or config: only the *path* of the client secret file. The token
and the API access are never logged.
"""

from __future__ import annotations

from nicheclock.i18n import t as _t

import argparse
import gzip
import json
import math
import os
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import quote, urlsplit

import requests

from nicheclock.match import tokens as _tokens

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
API_BASE = "https://www.googleapis.com/webmasters/v3"
TERMS_URL = "https://developers.google.com/webmaster-tools/limits"
TERMS_NOTE = "Search Console API: authorised read of the user's own property via their own OAuth client."

WINDOW_DAYS = 28
DATA_LAG_DAYS = 2            # GSC data is final ~2 days behind
LAG_LOOKBACK_DAYS = 90
ROW_LIMIT = 25000
MAX_ROWS = 200_000
MAX_SITEMAP_URLS = 5000
MAX_CHILD_SITEMAPS = 20
MIN_LAG_SAMPLES = 3
DEFAULT_MIN_IMPRESSIONS = 50
DEFAULT_MAX_POSITION = 20.0
HTTP_TIMEOUT = 30

_sleep = time.sleep  # patched in tests


class GscError(RuntimeError):
    """User-actionable failure (not connected, no access, missing extra...)."""


# ---- token cache -----------------------------------------------------------------------

def config_dir() -> Path:
    env = os.environ.get("NICHECLOCK_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".config") / "nicheclock"


def token_path(project: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in project) or "default"
    return config_dir() / f"gsc-token-{safe}.json"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _google_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:  # optional extra
        raise GscError(_t("gsc.libs_missing")) from e
    return Request, Credentials, InstalledAppFlow


def connect(project: Any, *, open_browser: bool = True, port: int = 0) -> Path:
    """Run the installed-app OAuth flow with the user's client secret; return the token path."""
    gsc = getattr(project, "gsc", None) or {}
    secret_file = gsc.get("client_secret")
    if not secret_file:
        raise GscError(_t("gsc.secret_unset", project=project.name))
    secret_path = Path(str(secret_file)).expanduser()
    if not secret_path.is_file():
        raise GscError(_t("gsc.secret_missing", path=secret_path))
    _, _, InstalledAppFlow = _google_libs()
    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser, access_type="offline",
                                  prompt="consent")
    path = token_path(project.name)
    _write_private(path, creds.to_json())
    return path


def access_token(project_name: str) -> str:
    """Valid access token from the cached credentials (refreshing and re-saving if needed)."""
    Request, Credentials, _ = _google_libs()
    path = token_path(project_name)
    if not path.is_file():
        raise GscError(_t("gsc.not_connected", project=project_name))
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise GscError(_t("gsc.token_expired", project=project_name))
        try:
            creds.refresh(Request())
        except Exception as e:  # google.auth.exceptions.RefreshError and transport errors
            raise GscError(_t("gsc.refresh_failed", error=type(e).__name__, project=project_name)) from None
        _write_private(path, creds.to_json())
    return creds.token


def is_connected(project_name: str) -> bool:
    return token_path(project_name).is_file()


# ---- Search Analytics API ----------------------------------------------------------------

def date_windows(today: date) -> dict[str, tuple[date, date]]:
    """``cur`` = last 28 finalised days, ``prev`` = the 28 days before that."""
    end = today - timedelta(days=DATA_LAG_DAYS)
    cur = (end - timedelta(days=WINDOW_DAYS - 1), end)
    prev_end = cur[0] - timedelta(days=1)
    return {"cur": cur, "prev": (prev_end - timedelta(days=WINDOW_DAYS - 1), prev_end)}


def _post(url: str, body: dict, token: str, ua: str | None, session: Any) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if ua:
        headers["User-Agent"] = ua
    for attempt in (0, 1):
        try:
            r = (session or requests).post(url, json=body, headers=headers, timeout=HTTP_TIMEOUT)
        except requests.RequestException as e:
            raise GscError(_t("gsc.request_failed", error=type(e).__name__)) from None
        if r.status_code in (429, 500, 503) and attempt == 0:
            try:
                wait = float(r.headers.get("Retry-After", 2))
            except ValueError:
                wait = 2.0
            if wait <= 30:
                _sleep(wait)
                continue
        if r.status_code == 401:
            raise GscError(_t("gsc.token_rejected"))
        if r.status_code == 403:
            raise GscError(_t("gsc.no_access"))
        if r.status_code >= 400:
            raise GscError(_t("gsc.http_error", status=r.status_code, body=r.text[:200]))
        return r.json()
    raise GscError("unreachable")  # pragma: no cover


def query_rows(prop: str, start: date, end: date, dimensions: list[str], *, token: str,
               ua: str | None = None, session: Any = None, max_rows: int = MAX_ROWS) -> Iterator[dict]:
    """Paged ``searchanalytics.query``; yields API rows ``{keys, clicks, impressions, ctr, position}``."""
    url = f"{API_BASE}/sites/{quote(prop, safe='')}/searchAnalytics/query"
    offset = 0
    while offset < max_rows:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dimensions,
                "rowLimit": ROW_LIMIT, "startRow": offset, "dataState": "final"}
        rows = _post(url, body, token, ua, session).get("rows") or []
        yield from rows
        if len(rows) < ROW_LIMIT:
            return
        offset += len(rows)


# ---- sitemap (for lastmod + known pages) -------------------------------------------------

def _norm_url(u: str) -> str:
    p = urlsplit(u.strip())
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


def parse_sitemap(xml_bytes: bytes) -> tuple[list[tuple[str, date | None]], list[str]]:
    """Return ``([(loc, lastmod_date|None)], [child_sitemap_urls])`` for a urlset or index."""
    if xml_bytes[:2] == b"\x1f\x8b":
        xml_bytes = gzip.decompress(xml_bytes)
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return [], []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    urls: list[tuple[str, date | None]] = []
    children: list[str] = []
    for el in root:
        kind = local(el.tag)
        if kind not in ("url", "sitemap"):
            continue
        loc = lastmod = None
        for c in el:
            name = local(c.tag)
            if name == "loc" and c.text:
                loc = c.text.strip()
            elif name == "lastmod" and c.text:
                try:
                    lastmod = date.fromisoformat(c.text.strip()[:10])
                except ValueError:
                    lastmod = None
        if loc:
            if kind == "url":
                urls.append((loc, lastmod))
            else:
                children.append(loc)
    return urls, children


def fetch_sitemap_lastmods(client: Any, site_urls: Iterable[str]) -> dict[str, date | None]:
    """``{normalised_url: lastmod|None}`` from each site's robots.txt/`sitemap.xml` (bounded)."""
    found: dict[str, date | None] = {}
    seen_maps: set[str] = set()
    for site in site_urls:
        p = urlsplit(site)
        origin = f"{p.scheme}://{p.netloc}"
        queue: list[str] = []
        try:
            robots = client.get(f"{origin}/robots.txt", robots=False)
            if robots.status_code == 200:
                queue += [ln.split(":", 1)[1].strip() for ln in robots.text.splitlines()
                          if ln.lower().startswith("sitemap:")]
        except Exception:  # noqa: BLE001 - discovery is best-effort
            pass
        queue = queue or [f"{origin}/sitemap.xml"]
        fetched = 0
        while queue and fetched < MAX_CHILD_SITEMAPS and len(found) < MAX_SITEMAP_URLS:
            sm = queue.pop(0)
            if sm in seen_maps:
                continue
            seen_maps.add(sm)
            fetched += 1
            try:
                resp = client.get(sm)
            except Exception:  # noqa: BLE001
                continue
            if resp.status_code != 200:
                continue
            urls, children = parse_sitemap(resp.content)
            queue += children
            for loc, lastmod in urls:
                if len(found) >= MAX_SITEMAP_URLS:
                    break
                found[_norm_url(loc)] = lastmod
    return found


# ---- index lag ---------------------------------------------------------------------------

def index_lag(first_impression: Mapping[str, date], lastmods: Mapping[str, date | None],
              window_start: date, *, min_samples: int = MIN_LAG_SAMPLES) -> dict[str, Any]:
    """Days from sitemap ``lastmod`` to first impression, over pages published inside the window.

    Pages whose lastmod is before ``window_start`` are skipped (their first impression is cut
    off by the window); so are pages that got impressions before lastmod (they were edited, not
    published). ``days`` is the median rounded up (be early, not late) or None when there are
    fewer than ``min_samples`` pages.
    """
    lags: list[int] = []
    for url, first in first_impression.items():
        lm = lastmods.get(_norm_url(url))
        if lm is None or lm < window_start:
            continue
        d = (first - lm).days
        if d >= 0:
            lags.append(d)
    days = math.ceil(statistics.median(lags)) if len(lags) >= min_samples else None
    return {"days": days, "samples": len(lags), "lags": sorted(lags)}


def first_impressions(rows: Iterable[dict]) -> dict[str, date]:
    """From ``dimensions=[page, date]`` API rows: earliest date with impressions per page."""
    first: dict[str, date] = {}
    for r in rows:
        page, day = r["keys"][0], date.fromisoformat(r["keys"][1])
        if r.get("impressions", 0) > 0 and (page not in first or day < first[page]):
            first[page] = day
    return first


# ---- sync --------------------------------------------------------------------------------

def sync(project: Any, store: Any, cfg: Any = None, *, client: Any = None,
         today: date | None = None, token: str | None = None, session: Any = None) -> dict[str, Any]:
    """Pull GSC data for ``project`` into ``store``; returns a summary dict.

    ``client`` is Core's rate-limited ``http.Client`` (used only for sitemaps); when omitted
    and ``cfg`` is given one is built, and sitemap/lag work is skipped if identity is unset.
    """
    gsc = getattr(project, "gsc", None) or {}
    prop = gsc.get("property")
    if not prop:
        raise GscError(_t("gsc.property_unset", project=project.name))
    today = today or date.today()
    token = token or access_token(project.name)
    ua = getattr(getattr(cfg, "identity", None), "user_agent", None) or None

    windows = date_windows(today)
    rows: list[dict] = []
    for window, (start, end) in windows.items():
        for r in query_rows(prop, start, end, ["query", "page"], token=token, ua=ua, session=session):
            q, page = r["keys"]
            rows.append({"date": end.isoformat(), "query": q, "page": page,
                         "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0),
                         "ctr": r.get("ctr", 0), "position": r.get("position"), "window": window})
    store.replace_gsc_rows(project.name, rows)

    summary: dict[str, Any] = {"rows": len(rows), "windows": {k: [v[0].isoformat(), v[1].isoformat()]
                                                              for k, v in windows.items()},
                               "index_lag_days": None, "lag_samples": 0, "sitemap_urls": 0}
    store.set_meta(project.name, "gsc_params", {
        "min_impressions": int(gsc.get("min_impressions", DEFAULT_MIN_IMPRESSIONS)),
        "max_position": float(gsc.get("max_position", DEFAULT_MAX_POSITION))})
    store.set_meta(project.name, "gsc_synced_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    if client is None and cfg is not None:
        try:
            from nicheclock.http import Client
            client = Client(cfg)
            if not cfg.identity.is_set():
                raise GscError(_t("gsc.identity_unset"))
        except Exception as e:  # noqa: BLE001
            summary["lag_note"] = _t("gsc.sitemap_skipped", error=e)
            client = None
    if client is not None:
        lastmods = fetch_sitemap_lastmods(client, getattr(project, "urls", []) or [])
        summary["sitemap_urls"] = len(lastmods)
        store.set_meta(project.name, "sitemap_urls", sorted(lastmods)[:MAX_SITEMAP_URLS])
        if any(lastmods.values()):
            lag_start = today - timedelta(days=LAG_LOOKBACK_DAYS)
            lag_end = today - timedelta(days=DATA_LAG_DAYS)
            first = first_impressions(
                query_rows(prop, lag_start, lag_end, ["page", "date"], token=token, ua=ua, session=session))
            lag = index_lag(first, lastmods, lag_start)
            summary["lag_samples"] = lag["samples"]
            if lag["days"] is not None:
                summary["index_lag_days"] = lag["days"]
                store.set_meta(project.name, "index_lag_days", lag["days"])
                store.set_meta(project.name, "index_lag_samples", lag["samples"])
    return summary


DEFAULT_SYNC_HOURS = 20


def refresh(project: Any, store: Any, cfg: Any = None, *, now: datetime | None = None,
            **sync_kw: Any) -> dict[str, Any] | None:
    """For ``run``: re-sync a connected project at most every ``gsc.sync_hours`` (default 20).

    Returns the sync summary, ``None`` when GSC is not configured/connected or was synced
    recently, or ``{"error": msg}`` (never raises) so a GSC outage cannot break a run.
    """
    gsc = getattr(project, "gsc", None)
    if not gsc or not is_connected(project.name):
        return None
    now = now or datetime.now(timezone.utc)
    last = store.get_meta(project.name, "gsc_synced_at")
    if last:
        age = now - datetime.fromisoformat(last)
        if age < timedelta(hours=float(gsc.get("sync_hours", DEFAULT_SYNC_HOURS))):
            return None
    try:
        return sync(project, store, cfg, today=now.date(), **sync_kw)
    except GscError as e:
        return {"error": str(e)}


# ---- gaps --------------------------------------------------------------------------------

_STOP = frozenset("a an and are as at be by for from how i in is it of on or the to vs what when "
                  "where which who why with".split())


def _qtokens(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOP and len(t) > 1]


def compute_gaps(rows: Iterable[Mapping[str, Any]], *, min_impressions: float = DEFAULT_MIN_IMPRESSIONS,
                 max_position: float = DEFAULT_MAX_POSITION, known_pages: Iterable[str] = (),
                 titles: Mapping[str, str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Queries worth a page. ``rows`` are gsc_rows dicts (window ``cur``/``prev``).

    A query is a gap when its current-window impressions ≥ ``min_impressions`` and either its
    best (lowest) position is worse than ``max_position`` or no known page covers it, where a
    page *covers* a query when every query token appears in the page URL path or title.
    ``known_pages`` adds URLs (e.g. from the sitemap) beyond the pages seen in ``rows``.
    Sorted by ``score`` = impressions × growth boost × (1.5 if uncovered).
    """
    titles = titles or {}
    cur: dict[str, dict[str, Any]] = {}
    prev_impr: dict[str, float] = defaultdict(float)
    pages: set[str] = set(known_pages)
    for r in rows:
        q = (r.get("query") or "").strip().lower()
        if not q:
            continue
        if r.get("page"):
            pages.add(r["page"])
        impr = float(r.get("impressions") or 0)
        if r.get("window") == "prev":
            prev_impr[q] += impr
            continue
        a = cur.setdefault(q, {"impressions": 0.0, "clicks": 0.0, "position": None, "page": None})
        a["impressions"] += impr
        a["clicks"] += float(r.get("clicks") or 0)
        pos = r.get("position")
        if impr > 0 and pos is not None and (a["position"] is None or pos < a["position"]):
            a["position"], a["page"] = float(pos), r.get("page")

    index: dict[str, set[int]] = defaultdict(set)   # token -> page ids
    page_list = sorted(pages)
    for i, p in enumerate(page_list):
        for t in set(_tokens(urlsplit(p).path.replace("/", " ").replace("_", " ")) + _tokens(titles.get(p, ""))):
            index[t].add(i)

    def covered(qtoks: list[str]) -> bool:
        if not qtoks:
            return True
        sets = [index.get(t) for t in qtoks]
        if any(s is None for s in sets):
            return False
        return bool(set.intersection(*sorted(sets, key=len)))

    out: list[dict[str, Any]] = []
    for q, a in cur.items():
        if a["impressions"] < min_impressions:
            continue
        pos = a["position"]
        is_covered = covered(_qtokens(q))
        if not ((pos is not None and pos > max_position) or not is_covered):
            continue
        prev = prev_impr.get(q, 0.0)
        growth = (a["impressions"] - prev) / max(prev, 10.0)
        boost = 1.0 + min(max(growth, 0.0), 3.0)
        score = a["impressions"] * boost * (1.0 if is_covered else 1.5)
        bits = [_t("gsc.impressions", count=f"{int(a['impressions']):,}")]
        if prev:
            bits[0] += _t("gsc.vs_prior", change=f"{(a['impressions'] - prev) / prev:+.0%}")
        else:
            bits[0] += _t("gsc.new_vs_prior")
        if pos is not None:
            bits.append(_t("gsc.best_position", position=pos))
        reasons = []
        if pos is not None and pos > max_position:
            reasons.append(_t("gsc.ranks_beyond", position=max_position))
        if not is_covered:
            reasons.append(_t("gsc.no_page"))
        out.append({
            "query": q, "page": a["page"], "impressions": int(a["impressions"]),
            "clicks": int(a["clicks"]), "position": round(pos, 1) if pos is not None else None,
            "ctr": round(a["clicks"] / a["impressions"], 4) if a["impressions"] else 0.0,
            "prev_impressions": int(prev), "covered": is_covered,
            "reason": "no_page" if not is_covered else "position",
            "why": ", ".join(bits) + ": " + _t("gsc.why_join").join(reasons),
            "score": round(score, 1),
        })
    out.sort(key=lambda g: (-g["score"], g["query"]))
    return out[:limit]


def gaps(project: Any, store: Any, limit: int = 50, *, min_impressions: float | None = None,
         max_position: float | None = None, titles: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Gaps from the rows already synced into ``store`` (``project`` = name or Project)."""
    name = project if isinstance(project, str) else project.name
    rows = store.gsc_rows(name)
    if not rows:
        return []
    params = store.get_meta(name, "gsc_params", {}) or {}
    return compute_gaps(
        rows,
        min_impressions=min_impressions if min_impressions is not None
        else params.get("min_impressions", DEFAULT_MIN_IMPRESSIONS),
        max_position=max_position if max_position is not None
        else params.get("max_position", DEFAULT_MAX_POSITION),
        known_pages=store.get_meta(name, "sitemap_urls", []) or [], titles=titles, limit=limit)


# ---- CLI ---------------------------------------------------------------------------------

def _cmd_connect_gsc(args: argparse.Namespace) -> int:
    from nicheclock.cli import load_for
    from nicheclock.store import Store

    cfg = load_for(args)
    project = cfg.project(getattr(args, "project", None))
    try:
        path = connect(project, open_browser=not args.no_browser, port=args.port)
        print(_t("gsc.connected", path=path))
        if args.no_sync:
            return 0
        with Store.open(cfg) as store:
            s = sync(project, store, cfg)
            found = gaps(project, store, limit=args.limit)
    except GscError as e:
        print(_t("gsc.error", message=e), file=sys.stderr)
        return 1
    print(_t("gsc.synced", rows=s["rows"], prev_start=s["windows"]["prev"][0], cur_end=s["windows"]["cur"][1],
             sitemap=s["sitemap_urls"],
             lag=s["index_lag_days"] if s["index_lag_days"] is not None else _t("gsc.lag_na"))
          + (_t("gsc.lag_pages", count=s["lag_samples"]) if s["lag_samples"] else ""))
    if s.get("lag_note"):
        print(_t("gsc.note", note=s["lag_note"]))
    for g in found:
        print(f"  {g['score']:>8.1f}  {g['query']}  - {g['why']}")
    if not found:
        print(_t("gsc.no_gaps"))
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("connect", help=_t("gsc.help.connect"))
    cs = p.add_subparsers(dest="connect_what", metavar="<service>")
    p.set_defaults(func=lambda a: (p.print_help(), 2)[1])
    g = cs.add_parser("gsc", help=_t("gsc.help.gsc"))
    g.add_argument("--project", default=None, help=_t("gsc.help.project"))
    g.add_argument("--no-browser", action="store_true", help=_t("gsc.help.no_browser"))
    g.add_argument("--port", type=int, default=0, help=_t("gsc.help.port"))
    g.add_argument("--no-sync", action="store_true", help=_t("gsc.help.no_sync"))
    g.add_argument("--limit", type=int, default=20, help=_t("gsc.help.limit"))
    g.set_defaults(func=_cmd_connect_gsc)

"""The one shared HTTP client: identity UA, <=1 req/s/host, robots.txt cache, 429/Retry-After."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib import robotparser
from urllib.parse import urlsplit

import requests

from nicheclock.i18n import t

MIN_INTERVAL = 1.0           # seconds between requests to one host
MAX_RETRY_AFTER = 120.0      # longer waits are not honoured: the call fails instead

# The only endpoints fetched without a robots.txt check (besides robots.txt itself): the MediaWiki
# Action API, which Wikimedia documents for bots (https://www.mediawiki.org/wiki/API:Etiquette)
# while en.wikipedia.org/robots.txt disallows /w/ for crawlers; the same Action API on wikidata.org
# (catalog resolver); and the Wikidata Query Service endpoint, documented for programmatic use with an
# identifying User-Agent (https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/queries); and the
# YouTube Data API v3 videos endpoint, an API with a key rather than a crawlable page.
# Nothing else is exempt.
ROBOTS_EXEMPT: tuple[tuple[str, str], ...] = (
    ("en.wikipedia.org", "/w/api.php"),
    ("www.wikidata.org", "/w/api.php"),
    ("query.wikidata.org", "/sparql"),
    ("www.googleapis.com", "/youtube/v3/videos"),     # the official YouTube Data API (needs a key)
)


class HttpError(Exception):
    pass


class IdentityError(HttpError):
    """identity.user_agent / contact_email not set: a run refuses to fetch."""


class RobotsDisallowed(HttpError):
    pass


class FetchError(HttpError):
    pass


class Client:
    """``Client(cfg).get(url, params=None, ...) -> requests.Response``.

    ``cfg`` is anything with ``.identity`` (a Config). Per-host spacing, robots.txt (cached per
    host, RFC 9309: 4xx = allow all, 5xx/unreachable = disallow all) and 429/5xx retries live here
    and nowhere else. Successful GETs are memoised for the client's lifetime (one run).
    """

    def __init__(self, cfg: Any, *, session: Any = None, cache_dir: str | Path | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 20.0,
                 max_retries: int = 2, min_interval: float = MIN_INTERVAL) -> None:
        self.identity = cfg.identity
        self.session = session or requests.Session()
        self._sleep, self._clock = sleep, clock
        self.timeout, self.max_retries, self.min_interval = timeout, max_retries, min_interval
        self._last: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._cache: dict[tuple, requests.Response] = {}
        self._robots_note: dict[str, str] = {}
        self._tripped: dict[str, str] = {}   # host -> why it is skipped for the rest of this run
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._wall = time.time
        self.requests_made = 0
        self.failures: list[str] = []     # rate limits, server errors, robots blocks, network errors

    # -- identity ------------------------------------------------------------------------
    def _require_identity(self) -> None:
        if not self.identity.is_set():
            raise IdentityError(t("http.identity_required"))

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        h = {"User-Agent": self.identity.user_agent, "From": self.identity.contact_email,
             "Accept": "*/*"}
        h.update(extra or {})
        return h

    @property
    def _agent_token(self) -> str:
        return self.identity.user_agent.split("/")[0].split()[0] if self.identity.user_agent else "*"

    # -- rate limit ----------------------------------------------------------------------
    def _throttle(self, host: str, interval: float) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = last + interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last[host] = self._clock()

    def _send(self, url: str, params: dict | None, headers: dict | None, interval: float,
              timeout: float | None) -> requests.Response:
        """Rate-limited GET with retries; returns the last response (callers check status)."""
        host = urlsplit(url).netloc.lower()
        if host in self._tripped:
            raise FetchError(self._tripped[host])
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host, interval)
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, headers=self._headers(headers),
                                        timeout=timeout or self.timeout, allow_redirects=True)
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.max_retries:
                    self._sleep(2.0 ** attempt)
                    continue
                raise FetchError(f"{url}: {e}") from e
            if resp.status_code == 429 or resp.status_code in (500, 502, 503, 504):
                wait = _retry_after(resp)
                if wait is None:
                    wait = 2.0 ** (attempt + 1)          # exponential backoff: 2s, 4s
                if attempt >= self.max_retries or wait > MAX_RETRY_AFTER:
                    if resp.status_code == 429:          # give up on this host for the whole run
                        self._tripped[host] = t("http.rate_limited", host=host)
                    return resp
                self._sleep(wait)
                self._last[host] = self._clock()
                continue
            return resp
        raise FetchError(f"{url}: {last_exc}")

    # -- robots ---------------------------------------------------------------------------
    def robots_allowed(self, url: str) -> bool:
        self._require_identity()
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc.lower()}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                resp = self._send(origin + "/robots.txt", None, None, self.min_interval, None)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                elif 400 <= resp.status_code < 500:
                    rp.parse([])               # unavailable: allow all
                else:
                    self._robots_note[origin] = t("http.robots_unavailable", status=resp.status_code)
                    rp.parse(["User-agent: *", "Disallow: /"])
            except FetchError as e:
                self._robots_note[origin] = t("http.robots_unreachable", error=e)
                rp.parse(["User-agent: *", "Disallow: /"])
            self._robots[origin] = rp
        path = parts.path or "/"
        target = path + ("?" + parts.query if parts.query else "")
        return rp.can_fetch(self._agent_token, target) or rp.can_fetch("*", target)

    # -- public ---------------------------------------------------------------------------
    def tripped(self, host: str) -> bool:
        """True when ``host`` answered 429 and is skipped for the rest of this run."""
        return host.lower() in self._tripped

    def forget(self) -> None:
        """Drop the in-memory response memo (large downloads would otherwise stay in memory all run)."""
        self._cache.clear()

    def note(self, message: str) -> None:
        """Report something the run should show (a region a source cannot serve, a skipped query)."""
        self.failures.append(message)

    def drain_failures(self) -> list[str]:
        """Failures since the last call (unique, in order); sources swallow them, the run reports them."""
        out, self.failures = list(dict.fromkeys(self.failures)), []
        return out

    def get(self, url: str, params: dict | None = None, *, headers: dict | None = None,
            robots: bool = True, min_interval: float | None = None,
            timeout: float | None = None, cache_ttl: float | None = None) -> requests.Response:
        """GET. ``cache_ttl`` (seconds) also keeps 200 responses on disk when the client has a
        ``cache_dir``. ``robots=False`` is refused except for robots.txt and ROBOTS_EXEMPT."""
        self._require_identity()
        try:
            return self._get(url, params, headers, robots, min_interval, timeout, cache_ttl)
        except (RobotsDisallowed, FetchError) as e:
            self.failures.append(str(e))
            raise

    def _get(self, url: str, params: dict | None, headers: dict | None, robots: bool,
             min_interval: float | None, timeout: float | None,
             cache_ttl: float | None) -> requests.Response:
        full = requests.Request("GET", url, params=params).prepare().url or url
        key = (full, tuple(sorted((headers or {}).items())))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if cache_ttl:
            disk = self._disk_get(full, cache_ttl)
            if disk is not None:
                self._cache[key] = disk
                return disk
        parts = urlsplit(url)
        exempt = (parts.netloc.lower(), parts.path) in ROBOTS_EXEMPT or parts.path == "/robots.txt"
        if not robots and not exempt:
            raise ValueError(t("http.robots_false", url=url))
        if robots and not exempt and not self.robots_allowed(full):
            note = self._robots_note.get(f"{parts.scheme}://{parts.netloc.lower()}")
            raise RobotsDisallowed(t("http.robots_note", note=note, url=full) if note
                                   else t("http.robots_disallows", url=full))
        interval = max(self.min_interval, min_interval or 0.0)
        resp = self._send(url, params, headers, interval, timeout)
        if resp.status_code == 200:
            self._cache[key] = resp
            if cache_ttl:
                self._disk_put(full, resp)
        elif resp.status_code == 429:
            self.failures.append(self._tripped.get(parts.netloc.lower(), t("http.rate_limited_short", host=parts.netloc)))
        elif resp.status_code in (401, 403) or resp.status_code >= 500:
            self.failures.append(f"{parts.netloc}: HTTP {resp.status_code}")
        return resp

    # -- disk cache (opt-in per call with cache_ttl seconds) -------------------------------
    def _disk_path(self, full: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(full.encode()).hexdigest()[:32] + ".json")

    def _disk_get(self, full: str, ttl: float) -> requests.Response | None:
        p = self._disk_path(full)
        if p is None or not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if self._wall() - d.get("fetched_at", 0) > ttl:
            return None
        resp = requests.Response()
        resp.status_code = 200
        resp._content = d["body"].encode("utf-8")
        resp.url = d.get("url", full)
        resp.encoding = "utf-8"
        return resp

    def _disk_put(self, full: str, resp: requests.Response) -> None:
        p = self._disk_path(full)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"fetched_at": self._wall(), "url": full, "body": resp.text}),
                     encoding="utf-8")

    def get_json(self, url: str, params: dict | None = None, **kw: Any) -> Any:
        resp = self.get(url, params, **kw)
        if resp.status_code != 200:
            raise FetchError(f"{resp.url}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as e:
            raise FetchError(f"{resp.url}: not JSON") from e

    def get_text(self, url: str, params: dict | None = None, **kw: Any) -> str:
        resp = self.get(url, params, **kw)
        if resp.status_code != 200:
            raise FetchError(f"{resp.url}: HTTP {resp.status_code}")
        return resp.text


def _retry_after(resp: requests.Response) -> float | None:
    v = resp.headers.get("Retry-After")
    if v is None:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        try:
            return max(0.0, (parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None

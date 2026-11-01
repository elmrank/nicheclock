from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from nicheclock.config import (Config, ConfigError, load_config, save_config, validate_config,
                               validate_dict)
from nicheclock.http import Client, FetchError, IdentityError, RobotsDisallowed
from nicheclock.models import Event, FeedItem, Signal, Topic
from nicheclock.store import Store

YAML = """
identity: {user_agent: "t/1 (+mailto:a@b.co)", contact_email: a@b.co}
data_dir: ./data
out_dir: ./out
projects:
  - name: p
    urls: [https://example.com]
    niches:
      - name: birding
        keywords: [bird watching, bird feeder]
        entities: [Merlin Bird ID]
        exclude: [tips]
        sources: [google_trends, hn]
"""


def test_config_roundtrip(tmp_path):
    f = tmp_path / "nicheclock.yaml"
    f.write_text(YAML)
    cfg = load_config(f)
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.identity.is_set()
    assert validate_config(cfg) == []
    save_config(cfg, tmp_path / "b.yaml")
    again = load_config(tmp_path / "b.yaml")
    assert again.projects[0].niches[0].keywords == ["bird watching", "bird feeder"]
    assert again.data_dir == cfg.data_dir


def test_validate_catches_problems(tmp_path):
    raw = {"projects": [{"name": "p", "niches": [{"name": "n", "sources": ["reddit"], "typo": 1}]}]}
    errs = validate_dict(raw)
    text = "\n".join(errs)
    assert "unknown source 'reddit'" in text and "typo" in text and "keyword" in text
    assert any("identity" in e for e in validate_dict(raw, require_identity=True))
    with pytest.raises(ConfigError):
        Config().project("x")


class Resp(requests.Response):
    def __init__(self, status=200, body="", headers=None):
        super().__init__()
        self.status_code = status
        self._content = body.encode()
        self.headers.update(headers or {})


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append((url, headers))
        r = self.routes[url]
        if isinstance(r, list):
            r = r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.t, self.slept = 100.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def mk(routes, identity=True):
    cfg = Config.__new__(Config)
    from nicheclock.config import Identity
    cfg.identity = Identity("nc-test/1 (+mailto:x@y.z)", "x@y.z") if identity else Identity()
    ck, ses = Clock(), FakeSession(routes)
    return Client(cfg, session=ses, sleep=ck.sleep, clock=ck.now), ses, ck


def test_client_refuses_without_identity():
    c, _, _ = mk({}, identity=False)
    with pytest.raises(IdentityError):
        c.get("https://a.test/x")


def test_client_rate_limit_robots_cache():
    c, ses, ck = mk({
        "https://a.test/robots.txt": Resp(200, "User-agent: *\nDisallow: /private\n"),
        "https://a.test/ok1": Resp(200, "1"), "https://a.test/ok2": Resp(200, "2"),
        "https://a.test/private": Resp(200, "no"),
    })
    c.get("https://a.test/ok1")
    c.get("https://a.test/ok2")
    with pytest.raises(RobotsDisallowed):
        c.get("https://a.test/private")
    urls = [u for u, _ in ses.calls]
    assert urls.count("https://a.test/robots.txt") == 1       # cached per host
    assert all(h["User-Agent"].startswith("nc-test/1") for _, h in ses.calls)
    # three requests (robots, ok1, ok2) at >= 1s spacing
    assert sum(ck.slept) >= 2.0
    c.get("https://a.test/ok1")                               # memoised: no new call
    assert [u for u, _ in ses.calls].count("https://a.test/ok1") == 1


def test_client_429_retry_after_and_5xx_robots():
    c, ses, ck = mk({
        "https://b.test/robots.txt": Resp(404),
        "https://b.test/x": [Resp(429, "", {"Retry-After": "7"}), Resp(200, "fine")],
        "https://down.test/robots.txt": Resp(503),
        "https://down.test/x": Resp(200),
    })
    assert c.get("https://b.test/x").text == "fine"
    assert any(abs(s - 7) < 0.01 or s >= 7 for s in ck.slept)
    with pytest.raises(RobotsDisallowed):
        c.get("https://down.test/x")


def test_client_network_error():
    c, _, _ = mk({"https://c.test/robots.txt": Resp(404),
                  "https://c.test/x": requests.ConnectionError("boom")})
    with pytest.raises(FetchError):
        c.get("https://c.test/x")


def test_store_roundtrip(tmp_path):
    now = datetime.now(timezone.utc)
    with Store(tmp_path) as st:
        st.add_signals("p", "n", [Signal("hn", "A", "u", rank=1, score=2.0, seen_at=now, extra={"k": 1})])
        st.add_signals("p", "n", [Signal("hn", "A", "u", rank=2, seen_at=now)])   # same day: replaced
        sigs = st.signals_since("p", "n", now - timedelta(hours=1))
        assert len(sigs) == 1 and sigs[0].rank == 2
        rid = st.start_run("p")
        st.save_topics(rid, "p", "n", [Topic("a", "A", [sigs[0]], "n", 5.0, why="w")])
        st.finish_run(rid, {"x": 1})
        assert st.topics("p", "n")[0].label == "A" and st.last_run("p")["stats"] == {"x": 1}
        ev = Event("release", "T", date.today() + timedelta(days=5), source="ics", niche="n",
                   publish_by=date.today() + timedelta(days=2))
        st.save_events("p", [ev])
        st.save_events("p", [ev])
        got = st.events("p")
        assert len(got) == 1 and got[0].publish_by == ev.publish_by and got[0].niche == "n"
        st.add_gsc_rows("p", [{"query": "q", "clicks": 1, "impressions": 9, "window": "cur"}])
        assert st.gsc_rows("p")[0]["query"] == "q"
        st.set_meta("p", "index_lag_days", 4)
        assert st.get_meta("p", "index_lag_days") == 4 and st.get_meta("p", "zz", 9) == 9


def test_models_dict_roundtrip():
    it = FeedItem("p", "n", "topic", "t", None, "why", 1.5)
    assert FeedItem.from_dict(it.to_dict()) == it
    ev = Event("k", "t", date(2026, 12, 1), "month", "s", None, date(2026, 11, 28), "n")
    assert Event.from_dict(ev.to_dict()) == ev


def test_client_rate_limited_host_is_skipped_for_the_run():
    c, ses, ck = mk({"https://r.test/robots.txt": Resp(404),
                     "https://r.test/x": Resp(429, "", {}), "https://r.test/y": Resp(200, "y"),
                     "https://ok.test/robots.txt": Resp(404), "https://ok.test/y": Resp(404)})
    assert c.get("https://r.test/x").status_code == 429
    assert [u for u, _ in ses.calls].count("https://r.test/x") == 3      # 3 tries, backoff 2s then 4s
    assert ck.slept[-2:] == [2.0, 4.0] or sum(ck.slept) >= 6
    with pytest.raises(FetchError, match="rate-limited"):
        c.get("https://r.test/y")                                          # no further waiting
    assert c.get("https://ok.test/y").status_code == 404                  # a plain 404 is no failure
    assert c.drain_failures() == ["r.test: rate-limited (HTTP 429), skipped for this run"]
    assert c.drain_failures() == []


def test_robots_false_only_for_exempt_endpoints():
    c, _, _ = mk({"https://en.wikipedia.org/w/api.php": Resp(200, "{}"),
                  "https://x.test/robots.txt": Resp(404)})
    assert c.get("https://en.wikipedia.org/w/api.php", robots=False).status_code == 200
    assert c.get("https://x.test/robots.txt", robots=False).status_code == 404
    with pytest.raises(ValueError):
        c.get("https://x.test/page", robots=False)


def test_disk_cache_ttl(tmp_path):
    cfg = Config.__new__(Config)
    from nicheclock.config import Identity
    cfg.identity = Identity("nc-test/1 (+mailto:x@y.z)", "x@y.z")
    ses = FakeSession({"https://g.test/robots.txt": Resp(404), "https://g.test/q": Resp(200, '{"a": 1}')})
    now = [1000.0]

    def mkc():
        c = Client(cfg, session=ses, sleep=lambda s: None, cache_dir=tmp_path)
        c._wall = lambda: now[0]
        return c

    assert mkc().get("https://g.test/q", {"query": "x"}, cache_ttl=86400).json() == {"a": 1}
    n = len(ses.calls)
    assert mkc().get("https://g.test/q", {"query": "x"}, cache_ttl=86400).json() == {"a": 1}
    assert len(ses.calls) == n                      # served from disk by a fresh client
    now[0] += 86401
    mkc().get("https://g.test/q", {"query": "x"}, cache_ttl=86400)
    assert len(ses.calls) == n + 2                  # expired after 24h: robots.txt + the query again


def test_context_and_watch_roundtrip(tmp_path):
    f = tmp_path / "nicheclock.yaml"
    f.write_text(YAML.replace("        exclude: [tips]", "        context: [California]\n        watch: [https://w.test/feed]\n        exclude: [tips]")
                 .replace("sources: [google_trends, hn]", "sources: [google_trends, hn, rss]"))
    cfg = load_config(f)
    n = cfg.projects[0].niches[0]
    assert n.context == ["California"] and n.watch == ["https://w.test/feed"]
    assert validate_config(cfg) == []
    save_config(cfg, tmp_path / "b.yaml")
    n2 = load_config(tmp_path / "b.yaml").projects[0].niches[0]
    assert n2.context == n.context and n2.watch == n.watch
    n.sources = ["hn"]
    assert any("watch" in e for e in validate_config(cfg))

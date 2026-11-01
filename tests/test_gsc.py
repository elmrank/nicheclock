from __future__ import annotations

import json
import os
import stat
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from integ_helpers import FakeServer
from nicheclock import gsc
from nicheclock.config import Project
from nicheclock.store import Store

FIX = Path(__file__).parent / "fixtures" / "integrations"
TODAY = date(2026, 10, 31)


def R(query, page, impr, pos, window="cur", clicks=0):
    return {"date": "2026-10-29", "query": query, "page": page, "clicks": clicks,
            "impressions": impr, "ctr": (clicks / impr) if impr else 0, "position": pos, "window": window}


ROWS = [
    # long-tail with no covering page and bad position, growing fast
    R("Bird treaty act compliance checklist", "https://example.com/blog/bird-treaty-act-overview", 1000, 34.2),
    R("bird treaty act compliance checklist", "https://example.com/blog/bird-treaty-act-overview", 240, 41.0),
    R("bird treaty act compliance checklist", "https://example.com/blog/bird-treaty-act-overview", 400, 30.0, "prev"),
    # ranks well on a page that covers it: not a gap
    R("feeder cleaning schedule", "https://example.com/blog/feeder-cleaning-schedule", 900, 4.2, clicks=120),
    R("feeder cleaning schedule", "https://example.com/blog/feeder-cleaning-schedule", 900, 6.0, "prev"),
    # covered by URL but ranks beyond 20: a "position" gap; best position is the min over pages
    R("ebird hotspot guide", "https://example.com/blog/ebird-hotspot-guide", 300, 27.5),
    R("ebird hotspot guide", "https://example.com/other/page-a", 20, 22.0),
    # good position but nothing covers the tokens: a "no_page" gap
    R("migration count committee", "https://example.com/blog/feeder-cleaning-schedule", 150, 8.0),
    # below the impression threshold
    R("tiny query", "https://example.com/blog/feeder-cleaning-schedule", 10, 50.0),
    # only in the previous window: ignored
    R("vanished query", "https://example.com/blog/feeder-cleaning-schedule", 500, 40.0, "prev"),
]


# ---- pure logic ---------------------------------------------------------------------

def test_date_windows():
    w = gsc.date_windows(TODAY)
    assert w["cur"] == (date(2026, 10, 2), date(2026, 10, 29))
    assert w["prev"] == (date(2026, 9, 4), date(2026, 10, 1))
    assert (w["cur"][1] - w["cur"][0]).days == 27 and (w["prev"][1] - w["prev"][0]).days == 27


def test_compute_gaps_on_fixture_rows():
    gaps = gsc.compute_gaps(ROWS, min_impressions=50, max_position=20)
    by_q = {g["query"]: g for g in gaps}
    assert set(by_q) == {"bird treaty act compliance checklist", "ebird hotspot guide", "migration count committee"}

    g = by_q["bird treaty act compliance checklist"]
    assert g["impressions"] == 1240 and g["prev_impressions"] == 400
    assert g["position"] == 34.2 and g["reason"] == "no_page" and g["covered"] is False
    assert "1,240 impressions (+210% vs prior 28d)" in g["why"]
    assert "best position 34.2" in g["why"] and "no page URL/title covers it" in g["why"]
    assert g["page"] == "https://example.com/blog/bird-treaty-act-overview"

    n = by_q["ebird hotspot guide"]
    assert n["reason"] == "position" and n["covered"] is True and n["position"] == 22.0
    assert n["page"] == "https://example.com/other/page-a" and n["impressions"] == 320

    m = by_q["migration count committee"]
    assert m["reason"] == "no_page" and m["position"] == 8.0

    assert [x["query"] for x in gaps][0] == "bird treaty act compliance checklist"  # highest score first
    assert "feeder cleaning schedule" not in by_q and "tiny query" not in by_q and "vanished query" not in by_q


def test_known_pages_and_titles_cover_queries():
    base = {g["query"] for g in gsc.compute_gaps(ROWS)}
    assert "migration count committee" in base
    covered = gsc.compute_gaps(
        ROWS, known_pages=["https://example.com/guides/bird-treaty-act-compliance-checklist"],
        titles={"https://example.com/blog/feeder-cleaning-schedule": "Migration Count Committees: a primer"})
    by_q = {g["query"]: g for g in covered}
    assert "migration count committee" not in by_q                    # title covers it (plural stemmed)
    assert by_q["bird treaty act compliance checklist"]["reason"] == "position"  # sitemap page covers, rank still >20


def test_thresholds_and_limit():
    assert gsc.compute_gaps(ROWS, min_impressions=2000) == []
    assert len(gsc.compute_gaps(ROWS, limit=1)) == 1
    # a huge position threshold leaves only queries nothing covers
    only_uncovered = gsc.compute_gaps(ROWS, max_position=100)
    assert only_uncovered and all(g["covered"] is False for g in only_uncovered)


def test_index_lag_median_rounds_up_and_ignores_old_and_edited():
    first = {
        "https://example.com/blog/bird-treaty-act-overview/": date(2026, 9, 24),     # lastmod 09-20 -> 4
        "https://example.com/blog/feeder-cleaning-schedule": date(2026, 10, 3),      # 2
        "https://example.com/blog/ebird-hotspot-guide": date(2026, 10, 10),      # 5
        "https://example.com/blog/model-cards-explained": date(2026, 10, 13),  # 1
        "https://example.com/blog/old-evergreen-post": date(2026, 8, 3),       # lastmod too old: skip
        "https://example.com/guides/bird-treaty-act-compliance-checklist": date(2026, 10, 20),  # no lastmod
        "https://example.com/blog/edited": date(2026, 10, 1),                  # impressions before lastmod
    }
    lastmods = {gsc._norm_url(k): v for k, v in {
        "https://example.com/blog/bird-treaty-act-overview": date(2026, 9, 20),
        "https://www.example.com/blog/feeder-cleaning-schedule": date(2026, 10, 1),
        "https://example.com/blog/ebird-hotspot-guide": date(2026, 10, 5),
        "https://example.com/blog/model-cards-explained": date(2026, 10, 12),
        "https://example.com/blog/old-evergreen-post": date(2025, 1, 2),
        "https://example.com/guides/bird-treaty-act-compliance-checklist": None,
        "https://example.com/blog/edited": date(2026, 10, 9),
    }.items()}
    lag = gsc.index_lag(first, lastmods, window_start=date(2026, 8, 2))
    assert lag["lags"] == [1, 2, 4, 5] and lag["samples"] == 4 and lag["days"] == 3  # median 3
    lag2 = gsc.index_lag({"https://example.com/blog/ebird-hotspot-guide": date(2026, 10, 10)}, lastmods, date(2026, 8, 2))
    assert lag2["days"] is None and lag2["samples"] == 1
    assert gsc.index_lag({k: v for k, v in list(first.items())[:2]}, lastmods, date(2026, 8, 2), min_samples=1)["days"] == 3


def test_parse_sitemap_index_and_urlset():
    urls, kids = gsc.parse_sitemap((FIX / "sitemap_index.xml").read_bytes())
    assert urls == [] and kids == ["https://example.com/sitemap-posts.xml"]
    urls, kids = gsc.parse_sitemap((FIX / "sitemap_posts.xml").read_bytes())
    assert kids == [] and len(urls) == 6
    assert urls[0] == ("https://example.com/blog/bird-treaty-act-overview/", date(2026, 9, 20))
    assert urls[-1][1] is None
    assert gsc.parse_sitemap(b"<not xml") == ([], [])


# ---- sync against a fake Search Console API ------------------------------------------

class FakeClient:
    """Stands in for http.Client: serves fixtures for robots.txt and sitemaps."""

    def __init__(self):
        self.calls = []
        self.files = {
            "https://example.com/robots.txt": "robots.txt",
            "https://example.com/sitemap-index.xml": "sitemap_index.xml",
            "https://example.com/sitemap-posts.xml": "sitemap_posts.xml",
        }

    def get(self, url, **kw):
        self.calls.append(url)
        name = self.files.get(url)
        if not name:
            return SimpleNamespace(status_code=404, text="", content=b"")
        data = (FIX / name).read_bytes()
        return SimpleNamespace(status_code=200, text=data.decode(), content=data)


def _api_rows(window_start: str):
    cur = window_start == "2026-10-02"
    return [{"keys": [r["query"], r["page"]], "clicks": r["clicks"], "impressions": r["impressions"],
             "ctr": r["ctr"], "position": r["position"]}
            for r in ROWS if (r["window"] == "cur") == cur]


LAG_ROWS = [
    {"keys": ["https://example.com/blog/bird-treaty-act-overview/", "2026-09-24"], "impressions": 3},
    {"keys": ["https://example.com/blog/bird-treaty-act-overview/", "2026-09-22"], "impressions": 0},   # zero: ignored
    {"keys": ["https://example.com/blog/bird-treaty-act-overview/", "2026-09-30"], "impressions": 9},
    {"keys": ["https://example.com/blog/feeder-cleaning-schedule", "2026-10-03"], "impressions": 5},
    {"keys": ["https://example.com/blog/ebird-hotspot-guide", "2026-10-10"], "impressions": 1},
    {"keys": ["https://example.com/blog/model-cards-explained", "2026-10-13"], "impressions": 2},
]


def _responder(log):
    def respond(path, body):
        log.append((path, body))
        assert path == "/sites/sc-domain%3Aexample.com/searchAnalytics/query"
        if body["dimensions"] == ["page", "date"]:
            rows = LAG_ROWS
        else:
            rows = _api_rows(body["startDate"])
        page = rows[body["startRow"]: body["startRow"] + body["rowLimit"]]
        return 200, {"Content-Type": "application/json"}, json.dumps({"rows": page}).encode()
    return respond


def _project():
    return Project(name="mysite", urls=["https://example.com"],
                   gsc={"property": "sc-domain:example.com", "client_secret": "~/x.json",
                        "min_impressions": 50})


def test_sync_stores_rows_lag_and_gaps(tmp_path, monkeypatch):
    log = []
    ident = SimpleNamespace(user_agent="nicheclock-test/0 (+mailto:t@example.com)", is_set=lambda: True)
    with FakeServer() as srv, Store(tmp_path / "data") as store:
        srv.responder = _responder(log)
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        monkeypatch.setattr(gsc, "ROW_LIMIT", 4)  # force pagination
        fc = FakeClient()
        summary = gsc.sync(_project(), store, SimpleNamespace(identity=ident), client=fc,
                           today=TODAY, token="tok-123")

        assert summary["rows"] == len(ROWS) and summary["sitemap_urls"] == 6
        assert summary["index_lag_days"] == 3 and summary["lag_samples"] == 4
        assert store.get_meta("mysite", "index_lag_days") == 3
        assert len(store.gsc_rows("mysite")) == len(ROWS)
        assert {r["window"] for r in store.gsc_rows("mysite")} == {"cur", "prev"}
        assert store.gsc_rows("mysite")[0]["date"] == "2026-10-29"

        # requests: Bearer token, UA, windows, pagination
        bodies = [b for _, b in log]
        assert {b["startDate"] for b in bodies if b["dimensions"] == ["query", "page"]} == {"2026-10-02", "2026-09-04"}
        assert any(b["startRow"] > 0 for b in bodies)
        lag_body = next(b for b in bodies if b["dimensions"] == ["page", "date"])
        assert (lag_body["startDate"], lag_body["endDate"]) == ("2026-08-02", "2026-10-29")
        assert srv.requests[0]["headers"]["Authorization"] == "Bearer tok-123"
        assert srv.requests[0]["headers"]["User-Agent"].startswith("nicheclock-test/0")

        # robots.txt Sitemap line -> index -> child sitemap
        assert fc.calls[:3] == ["https://example.com/robots.txt", "https://example.com/sitemap-index.xml",
                                "https://example.com/sitemap-posts.xml"]

        found = gsc.gaps("mysite", store, limit=10)
        by_q = {g["query"]: g for g in found}
        # the sitemap's /guides/bird-treaty-act-compliance-checklist now covers the long-tail query
        assert by_q["bird treaty act compliance checklist"]["reason"] == "position"
        assert set(by_q) == {"bird treaty act compliance checklist", "ebird hotspot guide", "migration count committee"}
        assert {"query", "page", "impressions", "clicks", "position", "ctr", "why"} <= set(found[0])
        # a second sync replaces rather than duplicates
        gsc.sync(_project(), store, SimpleNamespace(identity=ident), client=FakeClient(), today=TODAY, token="t")
        assert len(store.gsc_rows("mysite")) == len(ROWS)


def test_sync_without_sitemap_client_keeps_previous_lag(tmp_path, monkeypatch):
    with FakeServer() as srv, Store(tmp_path / "data") as store:
        srv.responder = _responder([])
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        store.set_meta("mysite", "index_lag_days", 5)
        cfg = SimpleNamespace(identity=SimpleNamespace(user_agent="", is_set=lambda: False))
        s = gsc.sync(_project(), store, cfg, today=TODAY, token="t")
        assert s["index_lag_days"] is None and "sitemap skipped" in s["lag_note"]
        assert store.get_meta("mysite", "index_lag_days") == 5


@pytest.mark.parametrize("status,needle", [(401, "connect gsc"), (403, "no access")])
def test_api_errors_are_actionable(tmp_path, monkeypatch, status, needle):
    with FakeServer() as srv, Store(tmp_path / "data") as store:
        srv.responder = lambda p, b: (status, {}, b"{}")
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        with pytest.raises(gsc.GscError, match=needle):
            gsc.sync(_project(), store, None, today=TODAY, token="t")


def test_429_retried_once(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(gsc, "_sleep", slept.append)
    with FakeServer() as srv:
        srv.responses.append((429, {"Retry-After": "3"}, b"{}"))
        srv.responses.append((200, {}, b'{"rows": []}'))
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        rows = list(gsc.query_rows("sc-domain:x.com", TODAY, TODAY, ["query"], token="t"))
    assert rows == [] and slept == [3.0]


def test_gaps_empty_store(tmp_path):
    with Store(tmp_path / "data") as store:
        assert gsc.gaps("mysite", store) == []


def test_gaps_uses_params_saved_by_sync(tmp_path):
    with Store(tmp_path / "data") as store:
        store.add_gsc_rows("mysite", ROWS)
        assert len(gsc.gaps("mysite", store)) == 3
        store.set_meta("mysite", "gsc_params", {"min_impressions": 1000, "max_position": 20})
        assert [g["query"] for g in gsc.gaps("mysite", store)] == ["bird treaty act compliance checklist"]
        assert len(gsc.gaps(_project(), store, min_impressions=50)) == 3


# ---- OAuth token cache / connect ----------------------------------------------------

@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    d = tmp_path / "cfgdir"
    monkeypatch.setenv("NICHECLOCK_CONFIG_DIR", str(d))
    return d


def test_token_path_is_per_project_and_safe(cfgdir):
    assert gsc.token_path("my site/../x") == cfgdir / "gsc-token-my_site_.._x.json"


def test_connect_requires_own_client_secret(cfgdir, tmp_path):
    p = Project(name="mysite", gsc={"property": "sc-domain:example.com"})
    with pytest.raises(gsc.GscError, match="client_secret"):
        gsc.connect(p)
    p.gsc["client_secret"] = str(tmp_path / "missing.json")
    with pytest.raises(gsc.GscError, match="not found"):
        gsc.connect(p)


def test_connect_saves_token_0600(cfgdir, tmp_path, monkeypatch):
    secret = tmp_path / "client_secret.json"
    secret.write_text("{}")
    seen = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["secret"], seen["scopes"] = path, scopes
            return cls()

        def run_local_server(self, **kw):
            seen["kw"] = kw
            return SimpleNamespace(to_json=lambda: '{"refresh_token": "r"}')

    monkeypatch.setattr(gsc, "_google_libs", lambda: (None, None, Flow))
    p = Project(name="mysite", gsc={"property": "sc-domain:example.com", "client_secret": str(secret)})
    path = gsc.connect(p, open_browser=False)
    assert seen["secret"] == str(secret) and seen["scopes"] == gsc.SCOPES
    assert seen["kw"]["open_browser"] is False and seen["kw"]["access_type"] == "offline"
    assert path == cfgdir / "gsc-token-mysite.json" and path.read_text() == '{"refresh_token": "r"}'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert gsc.is_connected("mysite")


def test_access_token_refreshes_and_resaves(cfgdir, monkeypatch):
    with pytest.raises(gsc.GscError, match="not connected"):
        monkeypatch.setattr(gsc, "_google_libs", lambda: (None, None, None))
        gsc.access_token("mysite")
    gsc._write_private(gsc.token_path("mysite"), "{}")

    class Creds:
        valid, refresh_token, token = False, "r", "old"

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            return cls()

        def refresh(self, request):
            self.token = "fresh"

        def to_json(self):
            return '{"token": "fresh"}'

    monkeypatch.setattr(gsc, "_google_libs", lambda: (object, Creds, None))
    assert gsc.access_token("mysite") == "fresh"
    assert gsc.token_path("mysite").read_text() == '{"token": "fresh"}'


def test_real_google_libs_import():
    pytest.importorskip("google_auth_oauthlib")
    Request, Credentials, Flow = gsc._google_libs()
    assert Flow.__name__ == "InstalledAppFlow"


@pytest.fixture
def fixed_clock(monkeypatch):
    """The fake API recognises the current window by its start date, so tests that read the clock must run on TODAY."""
    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=tz or timezone.utc)

    monkeypatch.setattr(gsc, "date", FixedDate)
    monkeypatch.setattr(gsc, "datetime", FixedDateTime)



# ---- CLI wiring ----------------------------------------------------------------------

def test_cli_connect_gsc_end_to_end_with_fakes(tmp_path, monkeypatch, capsys, cfgdir, fixed_clock):
    from nicheclock import cli

    (tmp_path / "nicheclock.yaml").write_text(
        "identity: {user_agent: 'nc-test/0', contact_email: 't@example.com'}\n"
        "data_dir: ./data\nout_dir: ./out\n"
        "projects:\n  - name: mysite\n    urls: [https://example.com]\n    niches: [{name: bird photography, keywords: [bird photography]}]\n"
        "    gsc: {property: 'sc-domain:example.com', client_secret: ./cs.json}\n")
    (tmp_path / "cs.json").write_text("{}")

    def fake_connect(project, **kw):
        assert kw == {"open_browser": False, "port": 0}
        return gsc.token_path(project.name)

    monkeypatch.setattr(gsc, "connect", fake_connect)
    monkeypatch.setattr(gsc, "access_token", lambda name: "tok")
    monkeypatch.setattr(gsc, "fetch_sitemap_lastmods", lambda client, urls: {})
    with FakeServer() as srv:
        srv.responder = _responder([])
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        rc = cli.main(["-c", str(tmp_path / "nicheclock.yaml"), "connect", "gsc", "--no-browser"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"synced {len(ROWS)} rows" in out
    assert "bird treaty act compliance checklist" in out and "no page URL/title covers it" in out


def test_cli_connect_gsc_reports_missing_client_secret(tmp_path, capsys, cfgdir):
    from nicheclock import cli

    (tmp_path / "nicheclock.yaml").write_text(
        "identity: {user_agent: 'nc-test/0', contact_email: 't@example.com'}\n"
        "projects:\n  - name: mysite\n    urls: [https://example.com]\n    niches: [{name: bird photography, keywords: [bird photography]}]\n"
        "    gsc: {property: 'sc-domain:example.com'}\n")
    rc = cli.main(["-c", str(tmp_path / "nicheclock.yaml"), "connect", "gsc", "--no-browser"])
    assert rc == 1 and "client_secret" in capsys.readouterr().err


# ---- refresh (called by `run`) -------------------------------------------------------

def test_refresh_skips_unconnected_then_syncs_once(tmp_path, monkeypatch, cfgdir, fixed_clock):
    cfg = SimpleNamespace(identity=SimpleNamespace(user_agent="u", is_set=lambda: True))
    with FakeServer() as srv, Store(tmp_path / "data") as store:
        srv.responder = _responder([])
        monkeypatch.setattr(gsc, "API_BASE", srv.url)
        monkeypatch.setattr(gsc, "access_token", lambda name: "tok")
        p = _project()
        assert gsc.refresh(p, store, cfg) is None                       # no token cached yet
        gsc._write_private(gsc.token_path("mysite"), "{}")
        s = gsc.refresh(p, store, cfg, client=FakeClient())
        assert s["rows"] == len(ROWS) and s["index_lag_days"] == 3
        n = len(srv.requests)
        assert gsc.refresh(p, store, cfg, client=FakeClient()) is None  # synced <20h ago
        assert len(srv.requests) == n
        assert gsc.refresh(Project(name="mysite"), store, cfg) is None  # no gsc block


def test_refresh_reports_error_instead_of_raising(tmp_path, monkeypatch, cfgdir):
    def boom(name):
        raise gsc.GscError("token expired")

    monkeypatch.setattr(gsc, "access_token", boom)
    gsc._write_private(gsc.token_path("mysite"), "{}")
    with Store(tmp_path / "data") as store:
        assert gsc.refresh(_project(), store, None) == {"error": "token expired"}

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="install the mcp extra to run MCP server tests")

from nicheclock import mcp_server
from nicheclock.config import Config, Niche, Project
from nicheclock.models import Event, Signal, Topic
from nicheclock.store import Store

from test_gsc import ROWS

NOW = datetime.now(timezone.utc)
TODAY = date.today()


@pytest.fixture
def cfg(tmp_path):
    proj = Project(name="mysite", urls=["https://example.com"],
                   niches=[Niche(name="birdwatching", keywords=["bird watching"]), Niche(name="bird photography")])
    c = Config(projects=[proj], data_dir=tmp_path / "data", out_dir=tmp_path / "out", base_dir=tmp_path)
    with Store.open(c) as s:
        sig = lambda term, url: Signal(source="google_trends", term=term, url=url, rank=3, score=None,  # noqa: E731
                                       geo="US", seen_at=NOW, extra={})
        s.save_topics(None, "mysite", "birdwatching", [
            Topic(key="snow goose", label="Snow Goose", niche="birdwatching", score=9.1,
                  why="Google Trends rank 3 (US)", signals=[sig("snow goose", "https://trends.example/sf")]),
            Topic(key="merlin bird id", label="Merlin Bird ID", niche="birdwatching", score=4.0,
                  why="Wikipedia pageviews x2", signals=[sig("merlin bird id", None)]),
        ])
        s.save_topics(None, "mysite", "bird photography", [
            Topic(key="bird treaty act", label="Bird treaty act", niche="bird photography", score=8.0, why="HN 120 pts", signals=[])])
        s.save_events("mysite", [
            Event(kind="release", title="Spring Bird Festival", date=TODAY + timedelta(days=20), source="ics",
                  publish_by=TODAY + timedelta(days=17), niche="birdwatching"),
            Event(kind="release", title="Too far away", date=TODAY + timedelta(days=200), source="ics"),
            Event(kind="deadline", title="Imminent deadline", date=TODAY + timedelta(days=2), source="ics",
                  publish_by=TODAY - timedelta(days=1), niche="bird photography"),
        ])
        s.add_gsc_rows("mysite", ROWS)
    return c


# ---- tool functions -----------------------------------------------------------------

def test_trends_for_niche(cfg):
    out = mcp_server.trends_for_niche(cfg, "mysite", "Birdwatching", days=7)
    assert [t["title"] for t in out] == ["Snow Goose", "Merlin Bird ID"]
    assert out[0] == {"niche": "birdwatching", "title": "Snow Goose", "score": 9.1,
                      "why": "Google Trends rank 3 (US)", "url": "https://trends.example/sf",
                      "sources": ["google_trends"], "region": "global", "region_precision": "global",
                      "regions": []}


def test_trends_unknown_niche_lists_valid_ones(cfg):
    with pytest.raises(mcp_server.ToolError, match=r"known niches: \['birdwatching', 'bird photography'\]"):
        mcp_server.trends_for_niche(cfg, "mysite", "knitting")
    with pytest.raises(mcp_server.ToolError, match="unknown project"):
        mcp_server.trends_for_niche(cfg, "nope", "bird photography")
    with pytest.raises(mcp_server.ToolError, match="days"):
        mcp_server.trends_for_niche(cfg, "mysite", "bird photography", days=0)


def test_upcoming_window_and_publish_by_flags(cfg):
    out = mcp_server.upcoming(cfg, "mysite", days=30)
    assert [e["title"] for e in out] == ["Imminent deadline", "Spring Bird Festival"]
    imminent, sgf = out
    assert imminent["days_until"] == 2 and imminent["publish_by_passed"] is True
    assert sgf["days_until"] == 20 and sgf["publish_by"] == (TODAY + timedelta(days=17)).isoformat()
    assert sgf["publish_by_passed"] is False
    assert len(mcp_server.upcoming(cfg, "mysite", days=365)) == 3


def test_gsc_gaps_from_store(cfg):
    out = mcp_server.gsc_gaps(cfg, "mysite", limit=2)
    assert len(out) == 2 and out[0]["query"] == "bird treaty act compliance checklist"
    assert out[0]["impressions"] == 1240 and "why" in out[0]


def test_fallback_to_feed_json_when_no_store(tmp_path):
    proj = Project(name="mysite", niches=[Niche(name="bird photography")])
    c = Config(projects=[proj], data_dir=tmp_path / "data", out_dir=tmp_path / "out", base_dir=tmp_path)
    (tmp_path / "out/mysite").mkdir(parents=True)
    (tmp_path / "out/mysite/feed.json").write_text(json.dumps({
        "project": "mysite",
        "items": [{"kind": "topic", "niche": "bird photography", "title": "Bird treaty act", "score": 3.0, "why": "w", "url": "u"},
                  {"kind": "topic", "niche": "bird photography", "title": "Audubon", "score": 5.0, "why": "w2", "url": None},
                  {"kind": "event", "niche": "bird photography", "title": "ignored", "score": 9}],
        "upcoming": [{"kind": "deadline", "title": "Deadline", "date": (TODAY + timedelta(days=5)).isoformat(),
                      "publish_by": None}],
        "gaps": [{"kind": "gap", "title": "ai audit", "url": None, "why": "500 impressions", "score": 7.0}],
    }))
    assert [t["title"] for t in mcp_server.trends_for_niche(c, "mysite", "bird photography")] == ["Audubon", "Bird treaty act"]
    assert mcp_server.upcoming(c, "mysite")[0]["days_until"] == 5
    assert mcp_server.gsc_gaps(c, "mysite") == [{"query": "ai audit", "page": None, "why": "500 impressions", "score": 7.0}]
    assert not (tmp_path / "data").exists()  # the read-only server never creates a store


# ---- through the MCP protocol -------------------------------------------------------

def _call(server, name, args):
    from mcp import Client

    async def go():
        async with Client(server) as c:
            tools = sorted(t.name for t in (await c.list_tools()).tools)
            return tools, await c.call_tool(name, args)

    return asyncio.run(go())


def test_tools_over_mcp_in_memory_client(cfg):
    pytest.importorskip("mcp.client")
    tools, res = _call(mcp_server.build_server(cfg), "trends_for_niche",
                       {"project": "mysite", "niche": "birdwatching", "days": 7})
    assert tools == ["explain_topic", "gsc_gaps", "trends_for_niche", "upcoming"]
    assert not res.is_error
    data = res.structured_content["result"] if res.structured_content else json.loads(res.content[0].text)
    assert data[0]["title"] == "Snow Goose" and data[0]["why"] == "Google Trends rank 3 (US)"

    _, res = _call(mcp_server.build_server(cfg), "trends_for_niche", {"project": "mysite", "niche": "knitting"})
    assert res.is_error and "known niches" in res.content[0].text


def test_stdio_server_subprocess(cfg, tmp_path):
    """The real `nicheclock mcp` command over stdio, driven by the SDK's stdio client."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (tmp_path / "nicheclock.yaml").write_text(
        "identity: {user_agent: 'nc-test/0', contact_email: 't@example.com'}\n"
        f"data_dir: {cfg.data_dir}\nout_dir: {cfg.out_dir}\n"
        "projects:\n  - name: mysite\n    niches:\n      - {name: birdwatching, keywords: [bird watching]}\n")
    params = StdioServerParameters(command=sys.executable, args=["-m", "nicheclock", "-c", str(tmp_path / "nicheclock.yaml"), "mcp"],
                                   cwd=str(Path(__file__).parent.parent))

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await s.call_tool("upcoming", {"project": "mysite", "days": 30})

    res = asyncio.run(go())
    data = res.structured_content["result"] if res.structured_content else json.loads(res.content[0].text)
    assert [e["title"] for e in data] == ["Imminent deadline", "Spring Bird Festival"]

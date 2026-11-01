"""What the report page decides from its data (nicheclock/output/app/app.js): which niche opens first, which trends
are in the table, what Upcoming lists. The decisions are pure functions the script exports under Node."""

import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nicheclock.models import Event
from nicheclock.output import Feed, NicheMeta, appdata

APP_JS = Path(__file__).resolve().parents[1] / "nicheclock" / "output" / "app" / "app.js"
NODE = shutil.which("node")

DATA = {
    "niches": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
    "topics": [
        {"niche": "a", "title": "a1", "score": 30},
        {"niche": "a", "title": "a2", "score": 29},
        {"niche": "b", "title": "b1", "score": 54},
        {"niche": "b", "title": "b2", "score": 36},
        {"niche": "b", "title": "b3", "score": 35},
        {"niche": "b", "title": "b4", "score": 34.9},
        {"niche": "c", "title": "c1", "score": 54},
    ],
    "upcoming": [
        {"niche": "b", "title": "r", "kind": "rising"},
        {"niche": "b", "title": "e", "kind": "event"},
        {"niche": "b", "title": "d", "kind": "deadline"},
        {"niche": "c", "title": "other niche", "kind": "event"},
    ],
}


def page_js(expr: str):
    script = f"const p = require({json.dumps(str(APP_JS))}); const d = {json.dumps(DATA)}; console.log(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(NODE is None, reason="node is needed to run the page script")
def test_trends_split_at_the_threshold_without_inflating():
    assert page_js("p.STRONG") == 35
    mine = page_js("p.splitTrends(d.topics.filter(t => t.niche === 'b'))")
    assert [t["title"] for t in mine["strong"]] == ["b1", "b2", "b3"]          # 35 counts; best first
    assert [t["title"] for t in mine["watching"]] == ["b4"]
    none = page_js("p.splitTrends(d.topics.filter(t => t.niche === 'a'))")
    assert none["strong"] == [] and len(none["watching"]) == 2                 # a weak niche has no rows above the line


@pytest.mark.skipif(NODE is None, reason="node is needed to run the page script")
def test_first_tab_is_the_strongest_niche_from_the_data_not_the_first_listed():
    assert page_js("p.pickNiche(d).id") == "b"          # a is listed first but b has the top score and more strong items than c
    assert page_js("p.pickNiche({niches: [{id: 'x'}, {id: 'y'}], topics: [{niche: 'y', score: 5}]}).id") == "y"
    assert page_js("p.pickNiche({niches: [{id: 'x'}, {id: 'y'}], topics: []}).id") == "x"   # nothing to compare: data order


@pytest.mark.skipif(NODE is None, reason="node is needed to run the page script")
def test_upcoming_lists_events_and_deadlines_only():
    assert [e["title"] for e in page_js("p.visibleUpcoming(d, 'b')")] == ["e", "d"]


def test_self_hosted_event_kinds_map_onto_the_schemas_three():
    when = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    feed = Feed(project="p", generated_at=when, niches=[NicheMeta("n")])
    for kind in ("early riser", "calendar", "scheduled", "comment deadline", "effective date"):
        feed.upcoming.append(Event(kind, kind, date(2026, 11, 20), niche="n", source="ics", publish_by=date(2026, 11, 17)))
    kinds = {e["title"]: e["kind"] for e in appdata.build(feed)["upcoming"]}
    assert kinds == {"early riser": "rising", "calendar": "event", "scheduled": "event",
                     "comment deadline": "deadline", "effective date": "deadline"}

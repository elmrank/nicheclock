"""Build the sample report of the docs (docs/img/report.png) from invented, neutral data: a "birdwatching" niche.

    python tests/docs_report_birdwatching.py OUTDIR      # writes OUTDIR/nicheclock.yaml, runs it, report in OUTDIR/out/example/index.html

Nothing leaves the machine: every source answers from the fixtures below (or tests/fixtures for the numeric
series). The names and numbers are made up for the screenshot; they are not live trends.
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicheclock import cli                                # noqa: E402
from nicheclock.http import Client                        # noqa: E402
from tests.conftest import Resp, RouteSession, fixture_text   # noqa: E402

GOOGLE_TRENDS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>
<item><title>Hockey final</title><ht:approx_traffic>500K+</ht:approx_traffic><pubDate>Fri, 30 Oct 2026 20:00:00 -0700</pubDate>
<ht:news_item><ht:news_item_title>Game recap</ht:news_item_title><ht:news_item_url>https://news.example/a</ht:news_item_url><ht:news_item_source>Example</ht:news_item_source></ht:news_item></item>
<item><title>Snowy owl irruption</title><ht:approx_traffic>20K+</ht:approx_traffic><pubDate>Fri, 30 Oct 2026 18:00:00 -0700</pubDate>
<ht:news_item><ht:news_item_title>Snowy owls seen far south of their usual range</ht:news_item_title><ht:news_item_url>https://news.example/owls</ht:news_item_url><ht:news_item_source>Example</ht:news_item_source></ht:news_item></item>
</channel></rss>"""

HN = {"hits": [
    {"objectID": "111", "title": "Show HN: An offline birdwatching log", "url": "https://ex.test/log", "points": 312, "num_comments": 120, "created_at": "2026-10-30T10:00:00Z"},
    {"objectID": "222", "title": "Ask HN: best birdwatching tips?", "url": None, "points": 15, "num_comments": 3, "created_at": "2026-10-30T11:00:00Z"},
    {"objectID": "333", "title": None, "points": 1}]}

GITHUB = {"total_count": 2, "items": [
    {"full_name": "foo/birdsong-lite", "html_url": "https://github.com/foo/birdsong-lite", "stargazers_count": 340,
     "description": "bird sound identification for birdwatching", "language": "Python", "created_at": "2026-10-10T00:00:00Z"},
    {"full_name": "bar/feeder-cam", "html_url": "https://github.com/bar/feeder-cam", "stargazers_count": 12, "description": None,
     "language": None, "created_at": "2026-10-20T00:00:00Z"}]}

WIKI_TOP = {"items": [{"project": "en.wikipedia", "access": "all-access", "year": "2026", "month": "10", "day": "29", "articles": [
    {"article": "Main_Page", "views": 9000000, "rank": 1}, {"article": "Special:Search", "views": 800000, "rank": 2},
    {"article": "Snowy_owl", "views": 12000, "rank": 3}, {"article": "Hockey", "views": 9000, "rank": 4},
    {"article": "Birdwatching", "views": 2000, "rank": 5}, {"article": "Ornithology", "views": 40, "rank": 6}]}]}

MEDIAWIKI = {"batchcomplete": True, "query": {"pages": [
    {"pageid": 1, "ns": 0, "title": "Snowy owl", "description": "species of owl"},
    {"pageid": 2, "ns": 0, "title": "Common kingfisher", "description": "species of bird"},
    {"pageid": 3, "ns": 0, "title": "Mercury", "pageprops": {"disambiguation": ""}},
    {"ns": 0, "title": "Nope Nope", "missing": True}]}}

BLOG = """<?xml version="1.0"?><rss version="2.0"><channel><title>Blog</title>
<item><title>Birdwatching roadmap 2027</title><link>https://blog.test/roadmap</link><pubDate>Thu, 29 Oct 2026 10:00:00 GMT</pubDate><description>IGNORED BODY</description></item>
<item><title>Old post</title><link>https://blog.test/old</link><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>Launch event: Birdwatching Fair</title><link>https://blog.test/fair</link><pubDate>Wed, 18 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""

CAL = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
SUMMARY:Autumn Migration Count\\, main weekend
DTSTART;VALUE=DATE:20261120
URL:https://count.test/
END:VEVENT
BEGIN:VEVENT
SUMMARY:Weekly guided walk with a very long title
DTSTART:20261104T180000Z
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
BEGIN:VEVENT
SUMMARY:Past
DTSTART:20200101
END:VEVENT
END:VCALENDAR
"""

ROUTES = [
    ("trending/rss", GOOGLE_TRENDS),
    ("/top/en.wikipedia/all-access/", json.dumps(WIKI_TOP)),
    ("w/api.php", json.dumps(MEDIAWIKI)),
    ("per-article", fixture_text("wiki_series_rising.json")),
    ("search_by_date", json.dumps(HN)),
    ("api.github.com", json.dumps(GITHUB)),
    ("gdeltproject.org", fixture_text("gdelt_timeline.json")),
    ("blog.test", BLOG),
    ("cal.test", CAL),
]

CONFIG = {
    "identity": {"user_agent": "nicheclock-docs/0.1 (+mailto:you@example.com)", "contact_email": "you@example.com"},
    "data_dir": "./data", "out_dir": "./out", "default_lag_days": 3,
    "projects": [{
        "name": "example", "urls": ["https://example.com"],
        "niches": [{"name": "birdwatching", "keywords": ["birdwatching", "bird watching", "birding"],
                    "entities": ["Snowy owl"], "exclude": ["tips"], "formats": ["field guide"],
                    "sources": ["google_trends", "wikipedia", "hn", "github", "gdelt", "rss"],
                    "rss": ["https://blog.test/feed.xml"], "ics": ["https://cal.test/a.ics"], "upcoming": True}],
    }],
}


class StringSession(RouteSession):
    """RouteSession whose routes carry the body itself instead of a fixture file name."""

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        from urllib.parse import urlencode
        full = url + ("?" + urlencode(params, doseq=True) if params else "")
        self.calls.append(full)
        if url.endswith("/robots.txt"):
            return Resp(404)
        for sub, body in self.routes:
            if sub in full:
                return Resp(200, body)
        return Resp(404)


def main(outdir: str) -> int:
    base = Path(outdir)
    base.mkdir(parents=True, exist_ok=True)
    cfg_path = base / "nicheclock.yaml"
    cfg_path.write_text(yaml.safe_dump(CONFIG, sort_keys=False), encoding="utf-8")
    orig = Client.__init__

    def fixture_client(self, cfg, **kw):
        orig(self, cfg, session=StringSession(ROUTES), sleep=lambda s: None, **kw)

    Client.__init__ = fixture_client
    return cli.main(["--config", str(cfg_path), "run", "--no-notify"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "docs-birdwatching"))

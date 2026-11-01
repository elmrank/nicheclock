from collections import Counter
from datetime import datetime, timezone

import pytest
from onboard_fakes import FIX, FakeClient, Resp

from nicheclock.onboard import crawl as C
from nicheclock.onboard import feeds as F

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
RSS = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
       '<item><title>Fresh post</title><link>https://x.test/1</link><pubDate>Thu, 29 Oct 2026 10:00:00 GMT</pubDate></item>'
       '</channel></rss>').encode()
STALE = RSS.replace(b"29 Oct 2026", b"29 Oct 2022")
HTML = (FIX / "home_with_feeds.html").read_bytes()


def home(*hrefs):
    links = "".join(f'<link rel="alternate" type="application/rss+xml" href="{h}">' for h in hrefs)
    return f"<html><head>{links}</head></html>".encode()


def test_alternate_feeds_reads_feed_links_only():
    got = F.alternate_feeds(HTML.decode(), "https://blog.test/")
    assert got == ["https://blog.test/feed.xml", "https://feeds.blog.test/atom.xml"]   # no comments feed, css, hreflang, mailto


def test_denylist_covers_never_ship_list():
    for host in ["reddit.com", "www.reddit.com", "old.reddit.com", "redd.it", "producthunt.com", "www.amazon.com",
                 "amazon.co.uk", "news.google.com", "trends.google.com", "weibo.com", "zhihu.com", "arcticshift.com"]:
        assert F.never_ship(host), host
        assert F.denied(host), host
    for host in ["twitter.com", "x.com", "youtube.com", "bit.ly", "en.wikipedia.org"]:
        assert F.denied(host) and not F.never_ship(host), host
    for host in ["blog.test", "eurogamer.net", "amazonaws-fan.net", "notreddit.com", "googleblog.example"]:
        assert not F.denied(host), host


def test_never_ship_extra_domains_from_the_environment(monkeypatch):
    assert not F.never_ship("forbidden.example") and not F.denied("forbidden.example")
    monkeypatch.setenv("NICHECLOCK_NEVER_SHIP", " forbidden.example , other.example,")
    for host in ["forbidden.example", "www.forbidden.example", "api.other.example"]:
        assert F.never_ship(host) and F.denied(host), host
    assert not F.never_ship("notforbidden.example")


def test_external_links_and_counts():
    links = C.external_links(HTML.decode(), "https://mysite.test/a/b", "mysite.test")
    assert links == ["https://blog.test/post-1", "https://www.reddit.com/r/birds/", "https://news.example.org/story?id=1&x=2"]
    crawl = C.SiteCrawl(base="https://mysite.test", host="mysite.test", pages=[
        C.Page("https://mysite.test/a", links=links),
        C.Page("https://mysite.test/b", links=["https://blog.test/x", "https://www.blog.test/y"])])
    assert crawl.link_counts() == Counter({"blog.test": 3, "reddit.com": 1, "news.example.org": 1})
    assert crawl.link_counts({"https://mysite.test/b"}) == Counter({"blog.test": 2})


def test_top_domains_skips_denied_and_own_site():
    counts = Counter({"blog.test": 5, "reddit.com": 99, "mysite.test": 40, "news.example.org": 5, "x.com": 50, "a.test": 1})
    assert F.top_domains(counts, 3, exclude=["www.mysite.test"]) == [("blog.test", 5), ("news.example.org", 5), ("a.test", 1)]
    assert F.top_domains(counts, 1) == [("mysite.test", 40)]


def test_verify_feed_parse_stale_and_errors():
    files = {"https://a.test/ok.xml": RSS, "https://a.test/stale.xml": STALE, "https://a.test/junk.xml": b"<html>no",
             "https://a.test/empty.xml": b'<rss version="2.0"><channel><title>t</title></channel></rss>',
             "https://a.test/redir.xml": Resp(200, RSS, {}, "https://www.reddit.com/feed.xml")}
    c = FakeClient(files, disallow=("https://a.test/blocked",))
    f = F.verify_feed(c, "https://a.test/ok.xml", NOW)
    assert f and f.domain == "a.test" and f.items == 1 and f.sample == ["Fresh post"]
    for name in ("stale", "junk", "empty", "redir", "missing", "blocked"):
        assert F.verify_feed(c, f"https://a.test/{name}.xml", NOW) is None, name


def test_discover_feeds_end_to_end_respects_denylist_and_robots():
    files = {
        "https://good.test/": home("/feed.xml", "/second.xml", "/third.xml"),
        "https://good.test/feed.xml": RSS, "https://good.test/second.xml": RSS, "https://good.test/third.xml": RSS,
        "https://nofeed.test/": b"<html>nothing</html>",
        "https://dead.test/": home("/feed.xml"),
        "https://other.test/": home("https://cdn.other.test/rss"),
        "https://cdn.other.test/rss": RSS,
        "https://reddit.com/": home("/r.rss"), "https://reddit.com/r.rss": RSS,
    }
    client = FakeClient(files, disallow=("https://blocked.test",))
    out = F.discover_feeds(client, ["good.test", "nofeed.test", "dead.test", "other.test", "reddit.com", "blocked.test"],
                           now=NOW)
    assert sorted(out) == ["good.test", "other.test"]
    assert [f.url for f in out["good.test"]] == ["https://good.test/feed.xml", "https://good.test/second.xml"]   # per_domain=2
    assert out["other.test"][0].domain == "other.test"
    assert not any("reddit.com" in r for r in client.requests)          # never even requested
    assert not any("blocked.test" in r and r.endswith("/feed.xml") for r in client.requests)


def test_feeds_for_niche_overlap_floor_ranking_and_domain_spread():
    def mk(d, n, on):
        titles = [f"snowy owl tip {i}" if i < on else f"unrelated {i}" for i in range(10)]
        return [F.Feed(f"https://{d}/{i}.xml", d, titles=titles) for i in range(n)]
    by = {"a.test": mk("a.test", 3, 3), "b.test": mk("b.test", 1, 9), "c.test": mk("c.test", 1, 5), "off.test": mk("off.test", 2, 0),
          "edge.test": mk("edge.test", 1, 1)}
    got = F.feeds_for_niche(by, Counter({"c.test": 4}), Counter({"a.test": 9}), ["snowy owl"], [], limit=5)
    assert [f.url for f in got] == ["https://b.test/0.xml", "https://c.test/0.xml", "https://a.test/0.xml",
                                    "https://edge.test/0.xml", "https://a.test/1.xml"]
    assert [round(f.overlap, 2) for f in got] == [0.9, 0.5, 0.3, 0.1, 0.3]
    assert not any("off.test" in f.url for f in got)                    # 0% on topic: dropped
    assert F.feeds_for_niche({}, Counter(), Counter(), ["x"]) == []
    assert F.feeds_for_niche(by, Counter(), Counter(), []) == []


def test_overlap_uses_keywords_and_entities_with_normalised_matching():
    f = F.Feed("u", "d", titles=["The best bird-watching spots", "Snowy Owl DLC out", "Weather today", "Bird Feeder picks"])
    assert F.overlap(f, ["bird watching", "bird feeder"], ["Snowy Owl"]) == 0.75
    assert F.overlap(f, [], []) == 0.0 and F.overlap(F.Feed("u", "d"), ["x"]) == 0.0


def test_deal_feeds_are_detected_by_url_channel_and_titles():
    assert F.is_deal_feed("https://x.test/rss/giveaways", "", [])
    assert F.is_deal_feed("https://x.test/feed", "Best Deals Daily", [])
    assert F.is_deal_feed("https://x.test/feed", "News", ["Coupon: 20% off", "Free games deal", "Review of X"])
    assert not F.is_deal_feed("https://x.test/feed", "News", ["Review of X", "Patch notes", "Free speech ruling"])


def _rss(*titles):
    from datetime import datetime, timezone
    from email.utils import format_datetime
    d = format_datetime(datetime.now(timezone.utc))
    items = "".join(f"<item><title>{t}</title><link>https://x.test/{i}</link><pubDate>{d}</pubDate></item>" for i, t in enumerate(titles))
    return f'<rss version="2.0"><channel><title>Chan</title>{items}</channel></rss>'.encode()


def test_probe_common_feed_paths_when_no_alternate_link():
    files = {"https://site.test/": b"<html>no links</html>", "https://site.test/atom.xml": _rss("A", "B")}
    client = FakeClient(files)
    out = F.discover_site(client, "https://site.test/")
    assert [f.url for f in out] == ["https://site.test/atom.xml"]
    assert [r for r in client.requests if "site.test" in r and "robots" not in r] == [
        "https://site.test/", "https://site.test/feed", "https://site.test/rss", "https://site.test/feed.xml",
        "https://site.test/rss.xml", "https://site.test/atom.xml"]                  # stops at the first valid path
    # robots-disallowed probes are skipped, an unreachable homepage is not probed at all, advertised feeds win
    assert F.discover_site(FakeClient(files, disallow=("https://site.test/atom",)), "https://site.test/") == []
    c2 = FakeClient({"https://down.test/feed": _rss("A")})
    assert F.discover_site(c2, "https://down.test/") == [] and "https://down.test/feed" not in c2.requests
    files["https://adv.test/"] = home("/f.xml")
    files["https://adv.test/f.xml"] = _rss("A")
    files["https://adv.test/feed"] = _rss("Z")
    c3 = FakeClient(files)
    assert [f.url for f in F.discover_site(c3, "adv.test")] == ["https://adv.test/f.xml"]
    assert "https://adv.test/feed" not in c3.requests


def test_own_site_feeds_are_never_added():
    files = {"https://blog.mine.test/": home("https://blog.mine.test/feed.xml"),
             "https://blog.mine.test/feed.xml": _rss("x"), "https://other.test/": home("https://feeds.mine.test/o.xml"),
             "https://feeds.mine.test/o.xml": _rss("x")}
    c = FakeClient(files)
    assert F.discover_site(c, "https://blog.mine.test/", own={"mine.test"}) == []
    assert F.discover_site(c, "https://other.test/", own={"www.mine.test"}) == []          # advertised feed lives on own site
    assert F.verify_feed(c, "https://feeds.mine.test/o.xml", own=["mine.test"]) is None
    assert F.verify_feed(c, "https://feeds.mine.test/o.xml", own=["else.test"]) is not None


def test_discover_skips_deal_feeds_but_watch_keeps_them_and_refuses_never_ship():
    files = {"https://deals.test/": home("/rss/giveaways"), "https://deals.test/rss/giveaways": _rss("Free game", "Coupon"),
             "https://news.test/": home("/feed.xml"), "https://news.test/feed.xml": _rss("Patch notes", "Review"),
             "https://news.test/sub/page": b"<html></html>", "https://feed.test/rss.xml": _rss("A")}
    c = FakeClient(files)
    assert F.discover_feeds(c, ["deals.test", "news.test"]).keys() == {"news.test"}
    feeds, problems = F.discover_watch(c, ["https://deals.test", "https://news.test/sub/page", "https://www.reddit.com/r/x",
                                           "https://mine.test", "https://feed.test/rss.xml", "https://nothing.test"],
                                       own={"mine.test"})
    assert [f.url for f in feeds] == ["https://deals.test/rss/giveaways", "https://news.test/feed.xml", "https://feed.test/rss.xml"]
    assert all(f.watch for f in feeds)
    text = "\n".join(problems)
    assert "never-ship" in text and "your own site" in text and "nothing.test" in text

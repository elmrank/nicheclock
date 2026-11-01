"""Regions, per-country pageviews, YouTube chart, region-aware assembly (no network)."""
import gzip
import json
from datetime import date, datetime, timedelta, timezone

from nicheclock.catalog import countrypv, regions as rg, youtube as yt
from nicheclock.catalog.categories import Classification
from nicheclock.catalog.scoring import score_topic
from nicheclock.catalog.trending import assemble_topic

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
CLS = Classification("entertainment", "occupation", "Q33999", "actor", 0)


def test_regions_are_worldwide_plus_the_ten_most_populous_by_un_wpp_2026():
    rs = rg.regions()
    assert [r["id"] for r in rs] == ["global", "IN", "CN", "US", "ID", "PK", "NG", "BR", "BD", "RU", "ET"]
    pops = [r["population_2026"] for r in rs[1:]]
    assert pops == sorted(pops, reverse=True) and all(p > 100_000_000 for p in pops)
    src = rg.load()["population_source"]
    assert "Prospects 2024" in src["name"] and src["retrieved"] == "2026-11-01" and "Mexico" in src["decision"]
    for r in rs[1:]:
        assert r["timezone"] and r["languages"] and r["wikipedia"] and r["fips"] and len(r["iso2"]) == 2
    assert rg.trending_file("global") == "trending.json" and rg.trending_file("IN") == "trending-IN.json"


def test_ledger_records_terms_for_every_used_source_and_the_exclusions():
    by = {e["id"]: e for e in rg.ledger()}
    for e in by.values():
        assert e["status"] in ("used", "excluded", "planned", "pending_key", "experimental")
        if e["status"] in ("used", "excluded", "experimental") and e["id"] not in ("tmdb", "spotify_charts", "google_play", "google_trends_rpc", "baidu", "weibo"):
            assert e["terms_url"].startswith("http") and len(e["terms_quote"]) > 30, e["id"]
    assert by["nager"]["status"] == by["apple"]["status"] == "excluded"
    assert by["wikimedia_country"]["status"] == "used" and by["gdelt"]["status"] == "used"


def row(cc, project, qid, views, title="Page", pid="1", country="X"):
    return "\t".join([country, cc, project, pid, title, qid, str(views)])


def test_parse_day_sums_projects_keeps_wikipedia_only_and_records_the_floor():
    raw = "\n".join([row("IN", "hi.wikipedia", "Q1", 400, "हिन्दी"), row("IN", "en.wikipedia", "Q1", 900, "Hindi"),
                     row("IN", "en.wikipedia", "Q2", 120, "Two"), row("IN", "commons.wikimedia", "Q3", 5000),
                     row("IN", "en.wikipedia", "", 300, "No_QID"), row("FR", "fr.wikipedia", "Q9", 9999)]).encode()
    d = countrypv.parse_day(raw, ["IN"])
    assert set(d) == {"IN"} and d["IN"]["rows"] == 4 and d["IN"]["floor"] == 120
    assert d["IN"]["pages"]["Q1"] == [1300.0, "Hindi", "en.wikipedia"] and "Q3" not in d["IN"]["pages"]


def days(values_by_day, floor=100):
    """samples {date: {"IN": {...}}} from {offset: {qid: views}} (offset 0 = END)."""
    out = {}
    for off, pages in values_by_day.items():
        out[END - timedelta(days=off)] = {"IN": {"rows": 5000, "floor": floor, "pages": {q: [v, f"T{q}", "en.wikipedia"] for q, v in pages.items()}}}
    return out


def test_country_series_substitutes_the_floor_for_days_below_the_release_threshold_and_says_so():
    s = days({0: {"Q1": 1000}, 1: {"Q1": 300}, 2: {}, 3: {"Q1": 280}})
    vals, censored = countrypv.series(s, END, "IN", "Q1", days=4)
    assert vals == [280.0, 100.0, 300.0, 1000.0] and censored is True
    full = days({0: {"Q1": 1000}, 1: {"Q1": 300}})
    assert countrypv.series(full, END, "IN", "Q1", days=2) == ([300.0, 1000.0], False)
    assert countrypv.series(s, END, "IN", "QX", days=4) is None                 # not published on the last day
    assert countrypv.series(s, END, "FR", "Q1", days=4) is None                 # no data for that country


def test_a_missing_day_file_breaks_the_series_instead_of_becoming_zero():
    s = days({0: {"Q1": 500}, 1: {"Q1": 400}, 3: {"Q1": 300}})                  # no file for day 2
    vals, _c = countrypv.series(s, END, "IN", "Q1", days=4)
    assert vals == [400.0, 500.0]


def test_country_candidates_need_a_history_and_a_rise():
    hist = {off: {"Q1": 300, "Q2": 300} for off in range(1, 16)}
    hist[0] = {"Q1": 1500, "Q2": 310, "Q3": 5000}
    s = days(hist)
    assert countrypv.candidates(s, END, "IN") == ["Q1", "Q3"] or countrypv.candidates(s, END, "IN") == ["Q3", "Q1"]
    assert countrypv.candidates(days({0: {"Q1": 1500}}), END, "IN") == []       # no history: no honest ratio


def test_country_day_files_are_cached_and_the_directory_index_gives_the_latest_day(tmp_path):
    class R:
        status_code = 200
        content = (row("IN", "hi.wikipedia", "Q1", 400) + "\n").encode()

    class C:
        calls = 0

        def get(self, url, **kw):
            C.calls += 1
            return R()

        def forget(self):
            pass

        def get_text(self, url, **kw):
            return '<a href="2026-10-30.tsv">x</a><a href="2026-10-31.tsv">y</a><a href="00_README.html">r</a>'

    c = C()
    d = countrypv.fetch_day(c, tmp_path, END, ["IN"])
    assert d["IN"]["pages"]["Q1"][0] == 400.0 and countrypv.fetch_day(c, tmp_path, END, ["IN"]) == d and C.calls == 1
    assert countrypv.latest_day(c) == date(2026, 10, 31)


def test_youtube_chart_parse_key_handling_and_no_key_leak(tmp_path, monkeypatch):
    data = {"items": [{"id": "abc", "snippet": {"title": " Trailer ", "channelTitle": "Studio"}, "statistics": {"viewCount": "1234"}},
                      {"id": "def", "snippet": {"title": "Hidden views"}, "statistics": {}}, {"id": "", "snippet": {"title": "x"}}]}
    vids = yt.parse_chart(data)
    assert vids == [{"rank": 1, "id": "abc", "title": "Trailer", "channel": "Studio", "views": 1234, "url": "https://www.youtube.com/watch?v=abc"},
                    {"rank": 2, "id": "def", "title": "Hidden views", "channel": "", "views": None, "url": "https://www.youtube.com/watch?v=def"}]
    f = tmp_path / "k.env"
    f.write_text("YOUTUBE_API_KEY=secret123\n")
    assert yt.load_key({"YOUTUBE_KEY_FILE": str(f)}) == "secret123" and yt.load_key({"YOUTUBE_API_KEY": "fromenv"}) == "fromenv"
    assert yt.load_key({"YOUTUBE_KEY_FILE": str(tmp_path / "missing")}) is None

    from nicheclock.http import FetchError

    class Bad:
        def get_json(self, url, params=None, **kw):
            raise FetchError(f"{url}?key={params['key']}: HTTP 403")

    out, detail = yt.fetch_chart(Bad(), "secret123", "IN")
    assert out is None and "secret123" not in detail and "***" in detail

    class Err:
        def get_json(self, url, params=None, **kw):
            return {"error": {"message": "quota secret123"}}

    assert "secret123" not in yt.fetch_chart(Err(), "secret123", "IN")[1]


def surge(base=1000, last=4000):
    return [base] * 27 + [last]


def test_assemble_topic_for_a_country_with_native_label_and_censored_series():
    rec = {"id": "Q1", "label": "Hindi cinema", "labels": {"hi": "हिन्दी सिनेमा"}, "description": "d", "aliases": [], "titles": {}, "claims": {}, "sitelinks": 9}
    region = next(r for r in rg.regions() if r["id"] == "IN")
    vals = [300.0] * 21 + [1500.0]
    t = assemble_topic({"qid": "Q1", "rec": rec, "country_series": (vals, True), "country_title": "हिन्दी सिनेमा", "country_project": "hi.wikipedia"},
                       CLS, END, NOW, None, region=region)
    assert t["region"] == "IN" and t["native_label"] == "हिन्दी सिनेमा" and t["label"] == "Hindi cinema"
    w = t["channels"]["wikipedia"]
    assert w["scope"] == "country" and w["censored"] is True and w["unit"].startswith("pageviews/day") and w["ratio"] == 5.0
    assert w["source"] == "wikimedia_country" and w["source_url"].startswith("https://hi.wikipedia.org/")
    assert t["breakdown"] == ["हिन्दी सिनेमा"] and "hn" not in t["channels"]


def test_youtube_chart_presence_counts_as_a_family_but_adds_no_score_and_no_ratio():
    chart = [{"rank": 3, "id": "a", "title": "T", "views": 1000, "url": "https://www.youtube.com/watch?v=a"},
             {"rank": 9, "id": "b", "title": "U", "views": None, "url": "https://www.youtube.com/watch?v=b"}]
    rec = {"id": "Q1", "label": "X", "labels": {}, "description": "", "aliases": [], "titles": {}, "claims": {}, "sitelinks": 9}
    region = next(r for r in rg.regions() if r["id"] == "BR")
    both = assemble_topic({"qid": "Q1", "rec": rec, "titles": {"pt": "X"}, "wiki_series": {"pt": surge()}, "wiki_scope": "language",
                           "youtube": chart}, CLS, END, NOW, None, region=region)
    yt_only = assemble_topic({"qid": "Q1", "rec": rec, "titles": {}, "wiki_series": {}, "youtube": chart}, CLS, END, NOW, None, region=region)
    y = both["channels"]["youtube"]
    assert y["chart"] and y["rank"] == 3 and y["value"] == 1000 and "ratio" not in y and "windows" not in y
    assert both["multi_source"] and both["source_families"] == ["video", "wiki"] and both["channels"]["wikipedia"]["scope"] == "language"
    assert yt_only["score"] == 0 and yt_only["status"] == "active" and yt_only["rising_channels"] == ["youtube"]
    assert yt_only["evidence"][0]["rank"] == 3 and score_topic({"youtube": y})["score"] == 0


def test_gdelt_country_counters_use_fips_locations():
    from nicheclock.catalog import news as newsmod

    def r(url, persons, locs):
        cols = [""] * 27
        cols[4], cols[10], cols[12] = url, locs, persons
        return "\t".join(cols)

    raw = "\n".join([r("https://a/1", "Narendra Modi,1", "1#India#IN#IN00#1#2#-1#5;4#Delhi, India#IN#IN07#1#2#-2#9"),
                     r("https://a/2", "Narendra Modi,1;Joe Biden,2", "1#United States#US#US#1#2#3#4"),
                     r("https://a/3", "Narendra Modi,1", "")]).encode()
    counts, example, n = newsmod.parse_gkg(raw, {"IN": "IN", "US": "US"})
    assert n == 3 and counts["WW"]["Narendra Modi"] == 3
    assert counts["IN"]["Narendra Modi"] == 1 and "Joe Biden" not in counts["IN"]       # an article counts once per country
    assert counts["US"]["Joe Biden"] == 1 and counts["US"]["Narendra Modi"] == 1
    assert example["IN"]["Narendra Modi"][1] == "https://a/1"


def test_split_youtube_strips_every_trace_and_drops_chart_only_rows():
    from nicheclock.catalog.trending import split_youtube

    rec = {"id": "Q1", "label": "X", "labels": {}, "description": "", "aliases": [], "titles": {}, "claims": {}, "sitelinks": 9}
    region = next(r for r in rg.regions() if r["id"] == "BR")
    chart = [{"rank": 3, "id": "a", "title": "T", "views": 1000, "url": "https://www.youtube.com/watch?v=a"}]
    both = assemble_topic({"qid": "Q1", "rec": rec, "titles": {"pt": "X"}, "wiki_series": {"pt": [1000] * 27 + [4000]}, "wiki_scope": "language",
                           "youtube": chart}, CLS, END, NOW, None, region=region)
    only = assemble_topic({"qid": "Q2", "rec": dict(rec, id="Q2"), "titles": {}, "wiki_series": {}, "youtube": chart}, CLS, END, NOW, None, region=region)
    doc = {"topics": [both, only], "coverage": {}}
    pub, side, extra = split_youtube(doc)
    text = json.dumps(pub)
    assert "youtube" not in text.lower() and "video" not in pub["topics"][0]["source_families"]
    assert [t["qid"] for t in pub["topics"]] == ["Q1"] and set(side) == {"Q1", "Q2"}
    assert [t["qid"] for t in extra] == ["Q2"] and "youtube" in extra[0]["channels"]           # the chart-only row survives in the sidecar
    assert side["Q1"]["channel"]["rank"] == 3 and side["Q1"]["evidence"][0]["source"] == "youtube"
    assert pub["topics"][0]["multi_source"] is False and "coverage" in pub and doc["topics"][0]["channels"].get("youtube")   # input untouched


def test_time_budget_stops_new_requests_but_still_serves_cached_series():
    import time

    from nicheclock.catalog.cache import Cache
    from nicheclock.catalog.trending import Ctx

    class RT:
        cache = Cache(":memory:")
        client = None                 # any request would raise: the budget must prevent it

    ctx = Ctx(RT, None, None, None, END, NOW, print, [], deadline=time.monotonic() - 1)
    assert ctx.out_of_time() and ctx.wiki_series("en", "Nope") is None
    RT.cache.put("pvday", f"en|Cached|{END.isoformat()}", [1, 2, 3])
    assert ctx.wiki_series("en", "Cached") == [1, 2, 3]
    assert Ctx(RT, None, None, None, END, NOW, print, []).out_of_time() is False


def test_main_page_entity_is_not_a_topic_in_any_edition():
    from nicheclock.catalog.trending import is_main_page

    assert is_main_page({"id": "Q5296", "description": "main page of a Wikimedia project"})
    assert is_main_page({"id": "Q999", "description": "Main page of a Wikimedia project in another language"})
    assert not is_main_page({"id": "Q42", "description": "English writer"})


def test_video_rows_are_sidecar_only_steady_and_carry_only_raw_chart_data():
    from nicheclock.catalog.trending import split_youtube

    region = next(r for r in rg.regions() if r["id"] == "IN")
    v = {"rank": 4, "id": "abc", "title": "Puppy training", "channel": "Dog TV", "views": 123456, "url": "https://www.youtube.com/watch?v=abc"}
    row = yt.video_row(v, "pets-animals", "Pets and Animals", region, "2026-10-31", "15")
    assert row["trend"] == "steady" and row["rank_score"] == 0 and row["ratio"] is None and row["windows"] == {} and row["sidecar_only"]
    assert row["channels"]["youtube"]["rank"] == 4 and row["channels"]["youtube"]["value"] == 123456 and row["category"] == "pets-animals"
    pub, side, extra = split_youtube({"topics": [row], "coverage": {}})
    assert pub["topics"] == [] and [e["id"] for e in extra] == ["yt:abc"] and "yt:abc" not in json.dumps(pub)
    assert set(yt.CATEGORY_MAP.values()) <= set(rg_cat_ids())


def rg_cat_ids():
    from nicheclock.catalog.categories import category_ids

    return category_ids()


def test_fill_youtube_asks_only_for_short_categories_and_counts_quota():
    from nicheclock.catalog.trending import Ctx, fill_youtube

    region = next(r for r in rg.regions() if r["id"] == "IN")
    calls = []

    class C:
        def get_json(self, url, params=None, **kw):
            calls.append(params["videoCategoryId"])
            return {"items": [{"id": f"v{i}", "snippet": {"title": f"T{i}", "channelTitle": "C"}, "statistics": {"viewCount": str(1000 + i)}} for i in range(50)]}

    class RT:
        client = C()

    ctx = Ctx(RT, None, None, None, END, NOW, lambda *_: None, [])
    ctx.yt_key = "k"
    full = [{"category": c, "qid": f"Q{n}"} for c in rg_cat_ids() if c != "other" for n in range(10)]
    topics = [t for t in full if t["category"] != "pets-animals"] + [{"category": "pets-animals", "qid": "Q1"}] * 7
    rows = fill_youtube(ctx, region, topics, END)
    assert calls == ["15"] and ctx.yt_units == 1                                  # one call: only pets-animals is short
    assert len(rows) == 3 and all(r["category"] == "pets-animals" and r["sidecar_only"] for r in rows)
    ctx.yt_key = None
    assert fill_youtube(ctx, region, topics, END) == []

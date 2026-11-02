"""Catalog scoring, sources and assembly (no network)."""
import io
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from nicheclock.catalog import hn as hnmod
from nicheclock.catalog import news as newsmod
from nicheclock.catalog.categories import Classification
from nicheclock.catalog.scoring import (channel_windows, family_rising, score_topic, seasonality, status_of,
                                        window_stats)
from nicheclock.catalog.trending import assemble_topic

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


def flat(n=28, v=1000):
    return [v] * n


def surge(base=1000, last=4000, days=1):
    return flat(28 - days, base) + [last] * days


# -- ratio against a 14-day median -------------------------------------------------------------

def test_window_stats_uses_the_median_of_the_14_days_before_the_window():
    s = [100] * 13 + [10_000] + [100] * 13 + [400]        # one old spike must not lift the baseline
    st = window_stats(s, 1)
    assert st == {"value": 400.0, "baseline": 100.0, "ratio": 4.0}
    s2 = flat(28, 200)[:-2] + [600, 800]
    assert window_stats(s2, 2)["ratio"] == 3.5 and window_stats(s2, 2)["baseline"] == 200.0


def test_window_stats_needs_window_plus_14_days():
    assert window_stats(flat(14), 1) is None and window_stats(flat(15), 1) is not None
    assert window_stats(flat(20), 7) is None and window_stats(flat(21), 7) is not None


def test_channel_windows_flags_active_at_1_5x():
    w = channel_windows(surge(1000, 1600))
    assert w["24h"] == {"ratio": 1.6, "active": True}
    assert channel_windows(flat())["24h"]["active"] is False


def ch(series, unit="pageviews/day"):
    return {"value": series[-1], "series": series[-14:], "windows": channel_windows(series), "unit": unit}


def test_family_rising_applies_floors_and_news_needs_2x():
    assert family_rising("wikipedia", ch(surge(1000, 2000)))
    assert not family_rising("wikipedia", ch(surge(10, 40)))                 # below 100 views/day: noise
    assert not family_rising("news", ch(surge(100, 180)))                    # 1.8x < 2x
    assert family_rising("news", ch(surge(100, 250)))
    assert not family_rising("news", ch(surge(10, 40)))                      # below 50 articles/day
    assert family_rising("hn", {"value": 120}) and not family_rising("hn", {"value": 30})


def test_two_rising_families_make_a_multi_source_topic():
    both = score_topic({"wikipedia": ch(surge(1000, 5000)), "news": ch(surge(100, 400)), "hn": {"value": 10}})
    assert both["multi_source"] and both["families"] == ["news", "wiki"]
    one = score_topic({"wikipedia": ch(surge(1000, 5000)), "news": ch(flat(28, 100))})
    assert not one["multi_source"] and one["families"] == ["wiki"]
    assert both["score"] > one["score"] > 0 and both["ratio"] == 5.0


def test_two_channels_in_the_same_family_are_not_multi_source():
    # two wiki channels cannot exist, but wiki + hn is two families; a lone hn is one
    s = score_topic({"hn": {"value": 400}})
    assert s["families"] == ["community"] and not s["multi_source"]
    assert score_topic({"wikipedia": ch(surge(1000, 4000)), "hn": {"value": 400}})["multi_source"]


def test_status_active_ended_or_unlisted():
    assert status_of({"wikipedia": ch(surge(1000, 3000))}) == "active"
    ended = flat(28, 1000)
    ended[-8:-2] = [3000] * 6                        # rose 3-8 days ago, back to normal for 2 days
    assert status_of({"wikipedia": ch(ended)}) == "ended"
    assert status_of({"wikipedia": ch(flat())}) is None


# -- seasonal vs new ---------------------------------------------------------------------------

def months(years, peak_month=None, peak=1000, base=100, partial_to=None):
    out = {}
    for y in years:
        for m in range(1, 13):
            if y == 2026 and m > 10:
                continue
            out[(y, m)] = base * 31
            if peak_month and abs(m - peak_month) < 1 and y < 2026:
                out[(y, m)] = peak * 31
    return out


def test_recurring_october_peak_is_seasonal():
    m = months(range(2021, 2027), peak_month=10)
    m[(2026, 10)] = 1200 * 31
    s = seasonality(m, date(2026, 10, 31))
    assert s["verdict"] == "seasonal" and 10 in s["peak_months"] and s["years_checked"] == 5


def test_first_time_rise_without_earlier_peaks_is_new():
    m = months(range(2021, 2027))
    m[(2026, 10)] = 900 * 31
    assert seasonality(m, date(2026, 10, 31))["verdict"] == "new"


def test_short_history_is_unknown_not_a_guess():
    m = {(2026, mo): 1000 * 30 for mo in range(5, 11)}
    assert seasonality(m, date(2026, 10, 31))["verdict"] == "unknown"
    assert seasonality({}, date(2026, 10, 31))["verdict"] == "unknown"


def test_flat_page_is_unknown():
    assert seasonality(months(range(2021, 2027)), date(2026, 10, 31))["verdict"] == "unknown"


# -- GDELT files -------------------------------------------------------------------------------

def gkg_row(url, persons="", orgs="", title="T"):
    cols = [""] * 27
    cols[4], cols[12], cols[14] = url, persons, orgs
    cols[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(cols)


def test_parse_gkg_counts_a_name_once_per_article_and_keeps_an_example():
    raw = "\n".join([
        gkg_row("https://a.example/1", "Lewis Hamilton,10;Lewis Hamilton,50;Max Verstappen,5", "Ferrari,3", "Race report"),
        gkg_row("https://b.example/2", "Lewis Hamilton,7;Al,2;Room 101,4", "", "Other"),
    ]).encode()
    counts, example, n = newsmod.parse_gkg(raw)
    ww = counts["WW"]
    assert n == 2 and ww["Lewis Hamilton"] == 2 and ww["Ferrari"] == 1
    assert "Al" not in ww and "Room 101" not in ww      # short tokens and names with digits are dropped
    assert example["WW"]["Lewis Hamilton"] == ["Race report", "https://a.example/1"]


def sample(day, files, **counts):
    return {"day": day.isoformat(), "files": files, "scale": 96 / files, "counts": {"WW": counts}, "examples": {}}


def test_gdelt_counts_scale_to_a_full_day_and_series_stay_contiguous():
    d0 = date(2026, 10, 31)
    samples = {d0: sample(d0, 6, X=10)}
    assert newsmod.estimated(samples[d0], "X") == 160.0
    from datetime import timedelta
    for i in (1, 2, 4):                                          # day 3 is missing
        samples[d0 - timedelta(days=i)] = sample(d0 - timedelta(days=i), 6, X=5)
    assert newsmod.series_for(["X"], samples, d0, 10) == [80.0, 80.0, 160.0]
    assert newsmod.series_for(["X"], samples, d0 - timedelta(days=3), 10) is None


def test_candidate_names_include_the_biggest_risers():
    from datetime import timedelta
    end = date(2026, 10, 31)
    samples = {end - timedelta(days=i): sample(end - timedelta(days=i), 6, Big=40, Riser=1) for i in range(1, 15)}
    samples[end] = sample(end, 6, Big=40, Riser=20, Quiet=1)
    names = newsmod.candidate_names(samples, end)
    assert names[0] == "Big" and "Riser" in names and "Quiet" not in names


def test_fetch_day_samples_files_and_caches(tmp_path):
    raw = gkg_row("https://a.example/1", "Lewis Hamilton,1", "", "t") + "\n" + gkg_row("https://a.example/2", "Lewis Hamilton,1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", raw)

    class R:
        status_code, content = 200, buf.getvalue()

    class C:
        urls = []

        def get(self, url):
            self.urls.append(url)
            return R()

        def forget(self):
            pass

    c = C()
    d = newsmod.fetch_day(c, tmp_path, date(2026, 10, 30))
    assert len(c.urls) == 6 and d["files"] == 6 and d["counts"] == {"WW": {"Lewis Hamilton": 12}} and d["scale"] == 16
    assert c.urls[0].endswith("20261030000000.gkg.csv.zip")
    newsmod.fetch_day(c, tmp_path, date(2026, 10, 30))
    assert len(c.urls) == 6                                      # second call read the state file


# -- Hacker News -------------------------------------------------------------------------------

def test_candidate_phrases_pick_capitalised_runs():
    assert hnmod.candidate_phrases("Show HN: Formula One telemetry viewer") == ["Formula One"]
    assert hnmod.candidate_phrases("OpenAI releases a model for Bank of America") == ["OpenAI", "Bank of America"]
    assert hnmod.candidate_phrases("why is everything so slow") == []
    assert hnmod.candidate_phrases("Ask HN: How do you learn?") == []


def test_title_mentions_is_whole_word():
    assert hnmod.title_mentions("Why I switched to Espresso machines", "espresso")
    assert not hnmod.title_mentions("Rusty tools", "Rust")
    assert not hnmod.title_mentions("Going places", "Go")
    assert hnmod.title_mentions("Learn Rust today", "Rust")


# -- one assembled topic -----------------------------------------------------------------------

def candidate(**kw):
    rec = {"id": "Q1968", "label": "Formula One", "description": "motorsport championship", "titles": {"enwiki": "Formula One"},
           "claims": {}, "sitelinks": 146, "aliases": []}
    c = {"qid": "Q1968", "rec": rec, "titles": {"en": "Formula One"}, "wiki_series": {"en": surge(2000, 9000)}}
    c.update(kw)
    return c


CLS = Classification("sports", "property", "P641", "sport", 0)


def test_assemble_topic_carries_units_provenance_and_no_search_volume():
    news = surge(100, 450, 1)
    t = assemble_topic(candidate(news_names=["Formula One"], news_series=news,
                                 news_examples=[["Race report", "https://news.example/r"]],
                                 hn=[{"id": "1", "title": "Formula One telemetry", "hn_url": "https://news.ycombinator.com/item?id=1",
                                      "url": "https://x.example", "points": 300, "comments": 80}]),
                       CLS, END, NOW, None)
    assert t["qid"] == "Q1968" and t["category"] == "sports" and t["category_label"] == "Sports"
    assert t["multi_source"] and t["source_families"] == ["community", "news", "wiki"] and t["status"] == "active"
    ch = t["channels"]
    assert ch["wikipedia"]["unit"] == "pageviews/day" and ch["wikipedia"]["value"] == 9000
    assert ch["news"]["unit"].startswith("articles/day") and ch["hn"]["unit"].startswith("HN points")
    assert t["windows"]["24h"]["ratio"] == 4.5 and t["first_seen"] == "2026-10-31"
    for e in t["evidence"] + [t]:
        assert e["source"] and e["source_url"].startswith("http") and e["license"]
    assert {e["source"] for e in t["evidence"]} == {"wikimedia_pageviews", "gdelt", "hn"}
    assert not any("search_volume" in k for k in t)


def test_flat_entities_above_the_floor_are_steady_and_below_it_are_dropped():
    t = assemble_topic(candidate(wiki_series={"en": flat(28, 5000)}), CLS, END, NOW, None)
    assert (t["trend"], t["rise"], t["status"]) == ("steady", None, "steady")
    assert t["ratio"] == 1.0 and t["rising_channels"] == [] and t["rank_score"] == 0 and t["volume"] == 5000
    assert set(t["windows"]) == {"24h", "48h", "7d"} and not t["windows"]["24h"]["active"]
    assert assemble_topic(candidate(wiki_series={"en": flat(28, 400)}), CLS, END, NOW, None) is None


def test_assemble_topic_uses_the_wiki_where_the_entity_is_biggest():
    c = candidate(titles={"en": "Formula One", "ja": "F1"},
                  wiki_series={"en": surge(2000, 9000), "ja": surge(500, 700)})
    t = assemble_topic(c, CLS, END, NOW, "2026-10-01", seasonal={"verdict": "seasonal"})
    assert t["channels"]["wikipedia"]["wiki"] == "en" and set(t["channels"]["wikipedia"]["by_wiki"]) == {"en", "ja"}
    assert t["seasonal"] == "seasonal" and t["first_seen"] == "2026-10-01"


# -- meta.json -------------------------------------------------------------------------------

def test_meta_counts_regions_sources_and_symmetry():
    from nicheclock.catalog.build import SOURCE_FILTER, meta_doc

    def topic(cat, multi=False, **ch):
        return {"category": cat, "multi_source": multi, "channels": ch}

    docs = {"global": {"date": "2026-10-30", "topics": [topic("sports", True, wikipedia={}), topic("sports"), topic("science")]},
            "IN": {"date": "2026-10-30", "topics": [topic("sports", wikipedia={}, news={})]},
            "CN": {"date": "2026-10-30", "topics": []}}
    rstat = {"global": {"wikipedia": {"status": "ok", "detail": "editions en", "topics": 3}, "hn": {"status": "ok", "detail": "x", "topics": 1}},
             "IN": {"wikipedia": {"status": "ok", "detail": "Wikimedia per-country data, 17000 published pages", "topics": 1},
                    "news": {"status": "ok", "detail": "n", "topics": 1}, "youtube": {"status": "pending_key", "detail": "no key", "topics": 0}},
             "CN": {"wikipedia": {"status": "limited", "detail": "zh edition, read worldwide. Wikipedia is blocked", "topics": 0},
                    "news": {"status": "no_data", "detail": "none", "topics": 0}}}
    ustat = {"wikidata_events": {"ok": True, "detail": "177 events"}, "holidays": {"ok": True, "detail": "h"}}
    m = meta_doc(docs, {"today": "2026-11-01", "events": [{"category": "other"}]}, rstat, ustat, NOW)
    assert m["counts"]["topics"] == 3 and m["counts"]["multi_source"] == 1 and m["counts"]["topics_all_regions"] == 4
    assert m["counts"]["regions"] == 11 and len(m["regions"]) == 11
    reg = {r["id"]: r for r in m["regions"]}
    assert reg["global"]["file"] == "trending.json" and reg["IN"]["file"] == "trending-IN.json"
    assert reg["IN"]["sources"] == ["wikipedia", "news"] and reg["IN"]["limited"] is False
    assert reg["CN"]["limited"] is True and "blocked" in reg["CN"]["limited_note"] and reg["CN"]["sources"] == []
    assert reg["IN"]["native_label"] == "भारत" and reg["IN"]["timezone"] == "Asia/Kolkata"
    assert reg["ET"]["source_status"] == {}                     # not built in this test: no invented status
    src = {s["id"]: s for s in m["sources"]}
    assert src["youtube"]["status"] == "pending_key" and src["youtube"]["ok"] is False
    assert src["nager"]["status"] == "excluded" and "sponsorship" in src["nager"]["terms_quote"]
    assert src["wikimedia_country"]["ok"] is True and src["hn"]["ok"] is True and src["wikidata"]["ok"] is True
    assert src["github"]["status"] == "used" and src["mastodon"]["status"] == "planned"
    assert [f["key"] for f in m["source_filter"]] == [f["key"] for f in SOURCE_FILTER if not f.get("experimental")] == ["wikipedia", "news", "hn", "youtube", "github", "gdacs", "tvmaze", "stackexchange", "openlibrary"]
    assert m["symmetry"] == {"ok": True, "problems": []} and "never" in m["units"]["note"]
    assert m["population_source"]["decision"].startswith("Ethiopia")


# -- pageviews: a day that is not published yet is not a zero day ------------------------------

def test_daily_series_treats_an_unpublished_last_day_as_missing_not_zero():
    from nicheclock.catalog import pageviews as pv

    def items(days):
        return {"items": [{"timestamp": f"202610{d:02d}00", "views": 100 + d} for d in days]}

    class Resp:
        def __init__(self, status, body=None):
            self.status_code, self._b = status, body

        def json(self):
            return self._b

    class C:
        def __init__(self, resp):
            self.resp = resp

        def get(self, url):
            return self.resp

    end = date(2026, 10, 31)
    full = pv.daily_series(C(Resp(200, items(range(4, 32)))), "en", "X", end, 28)
    assert len(full) == 28 and full[-1] == 131 and 0 not in full
    assert pv.daily_series(C(Resp(200, items(range(3, 31)))), "en", "X", end, 28) is None      # 31st not there yet
    gap = pv.daily_series(C(Resp(200, items([d for d in range(4, 32) if d != 10]))), "en", "X", end, 28)
    assert gap[6] == 0 and gap[-1] == 131                                                      # an inner gap is a zero day
    assert pv.daily_series(C(Resp(404)), "en", "X", end, 3) == [0, 0, 0]                       # no views at all
    assert pv.daily_series(C(Resp(500)), "en", "X", end, 3) is None


# -- quality: floors, Main Page, volume-aware ranking --------------------------------------------

def test_rank_score_prefers_a_modest_rise_on_a_big_audience_to_a_huge_rise_on_a_tiny_one():
    from nicheclock.catalog.scoring import RATIO_CAP, rank_score

    big = rank_score(450_000, 3.0, 1)
    tiny = rank_score(300, 1500.0, 1)
    assert big > tiny and big > rank_score(6_000, 20.0, 1)                          # 3x on 450k beats 20x on 6k
    assert rank_score(450_000, 1500.0, 1) == rank_score(450_000, RATIO_CAP, 1)      # capped
    assert rank_score(50_000, 4.0, 2) == rank_score(50_000, 4.0, 1) * 1.25           # a second family adds 25%
    assert rank_score(None, 5.0, 1) == 0 and rank_score(1000, None, 1) == 0 and rank_score(1000, 1.0, 1) == 0


def test_edition_floors_scale_with_audience_size():
    # 40x on 300 views/day is noise in English but 4,000/day in a small edition can be a trend
    assert not family_rising("wikipedia", dict(ch(surge(100, 4000)), floor=5000.0))
    assert family_rising("wikipedia", dict(ch(surge(100, 4000)), floor=150.0))
    from nicheclock.catalog.scoring import WIKI_FLOORS
    assert WIKI_FLOORS["en"] == 5000 and WIKI_FLOORS["am"] < WIKI_FLOORS["hi"] < WIKI_FLOORS["en"]


def test_a_tiny_baseline_blowup_is_not_listed_and_the_big_topic_leads():
    tiny = assemble_topic(candidate(wiki_series={"en": surge(120, 3000)}), CLS, END, NOW, None)       # 25x, 3,000/day < 5,000
    assert tiny is None
    small = candidate(wiki_series={"en": surge(2000, 9000)})
    big = candidate(wiki_series={"en": surge(150_000, 450_000)})
    a = assemble_topic(small, CLS, END, NOW, None)
    b = assemble_topic(big, CLS, END, NOW, None)
    assert b["rank_score"] > a["rank_score"] and b["volume"] == 450_000 and b["volume_unit"] == "pageviews/day"
    assert b["channels"]["wikipedia"]["floor"] == 5000.0 and a["score"] <= 100


def test_on_this_day_pages_must_clear_three_times_the_floor():
    c = candidate(wiki_series={"en": surge(2000, 9000)}, otd_langs=["en"])           # 9,000 < 3 x 5,000
    assert assemble_topic(c, CLS, END, NOW, None) is None
    assert assemble_topic(candidate(wiki_series={"en": surge(2000, 20000)}, otd_langs=["en"]), CLS, END, NOW, None) is not None


def test_featured_feed_titles_are_read_from_tfa_and_on_this_day():
    from nicheclock.catalog import pageviews as pv

    class R:
        status_code = 200

        def json(self):
            return {"tfa": {"titles": {"normalized": "Zombie Nightmare"}},
                    "onthisday": [{"pages": [{"titles": {"normalized": "Ninety-five Theses"}}, {"titles": {}}]}, {"pages": None}],
                    "image": {"title": "File:x.jpg"}}

    class C:
        def get(self, url):
            assert url.endswith("/wikipedia/en/featured/2026/10/31")
            return R()

    assert pv.featured(C(), "en", date(2026, 10, 31)) == {"tfa": {"Zombie Nightmare"}, "otd": {"Ninety-five Theses"}}

    class Down:
        def get(self, url):
            r = R()
            r.status_code = 404
            return r

    assert pv.featured(Down(), "ha", date(2026, 10, 31)) == {"tfa": set(), "otd": set()}


# -- categories: at least 10 rows when the data has them, rising first, steady fill ---------------------

def row(cat, trend, rise, rank=0.0, volume=1000):
    return {"category": cat, "trend": trend, "rise": rise, "rank_score": rank, "volume": volume,
            "windows": {"24h": {"active": trend == "rising"}, "48h": {"active": trend == "rising"}, "7d": {"active": False}}}


def test_trend_of_distinguishes_strong_mild_steady_and_nothing():
    from nicheclock.catalog.scoring import trend_of

    assert trend_of({"wikipedia": dict(ch(surge(1000, 4000)), floor=500.0)}) == ("rising", "strong", "active")
    assert trend_of({"wikipedia": dict(ch(surge(1000, 1300)), floor=500.0)}) == ("rising", "mild", "active")      # 1.3x
    assert trend_of({"wikipedia": dict(ch(flat(28, 1000)), floor=500.0)}) == ("steady", None, "steady")
    assert trend_of({"wikipedia": dict(ch(flat(28, 300)), floor=500.0)}) is None
    assert trend_of({"wikipedia": dict(ch(surge(100, 1300)), floor=2000.0)}) is None                              # rise on a tiny level


def test_select_per_category_fills_with_mild_then_steady_and_never_invents():
    from nicheclock.catalog.trending import select_per_category

    rows = [row("sports", "rising", "strong", rank=r) for r in range(1, 4)]
    rows += [row("sports", "rising", "mild", rank=0.5)] * 2
    rows += [row("sports", "steady", None, volume=v) for v in range(100, 130)]
    rows += [row("food-drink", "steady", None, volume=v) for v in (5, 4, 3)]
    out = select_per_category(rows)
    sports = [t for t in out if t["category"] == "sports"]
    assert len(sports) == 10 and [t["rise"] for t in sports[:5]] == ["strong"] * 3 + ["mild"] * 2
    assert [t["volume"] for t in sports[5:]] == [129, 128, 127, 126, 125]                        # biggest steady first
    assert len([t for t in out if t["category"] == "food-drink"]) == 3                           # short: not padded
    assert [t["trend"] for t in out] == sorted((t["trend"] for t in out), key=lambda x: x != "rising")


def test_strong_risers_are_all_kept_up_to_the_cap_even_beyond_ten():
    from nicheclock.catalog.trending import RISING_CAP, select_per_category

    rows = [row("politics", "rising", "strong", rank=r) for r in range(1, 40)] + [row("politics", "steady", None)]
    out = select_per_category(rows)
    assert len(out) == RISING_CAP and all(t["rise"] == "strong" for t in out) and out[0]["rank_score"] == 39


def test_coverage_counts_rows_per_window_and_flags_limited_categories():
    from nicheclock.catalog.build import coverage_markdown
    from nicheclock.catalog.trending import coverage

    rows = [row("sports", "rising", "strong")] * 6 + [row("sports", "steady", None)] * 4 + [row("games", "steady", None)] * 3
    cov = coverage(rows)
    assert cov["sports"] == {"rising": 6, "steady": 4, "total": 10, "limited": False,
                             "by_window": {"24h": {"rising": 6, "total": 10}, "48h": {"rising": 6, "total": 10}, "7d": {"rising": 0, "total": 4}}}
    assert cov["games"]["limited"] is True and cov["shopping"] == {**cov["shopping"], "total": 0, "limited": True}
    meta = {"generated_at": "2026-11-01T05:00Z", "date": "2026-10-31", "coverage": {"global": cov},
            "categories": [{"id": "sports", "label": "Sports"}, {"id": "games", "label": "Games"}],
            "regions": [{"id": "global", "label": "Worldwide"}]}
    md = coverage_markdown(meta)
    assert "| Sports | 6+4 |" in md and "| Games | 0+3* |" in md and "Worldwide: 1/2 categories have 10 or more rows" in md


def test_coverage_counts_only_rows_the_default_7d_window_lists():
    from nicheclock.catalog.trending import coverage

    no7d = dict(row("games", "rising", "strong"), windows={"24h": {"active": True}, "48h": {"active": True}})
    cov = coverage([row("games", "steady", None)] * 9 + [no7d])
    assert cov["games"]["total"] == 9 and cov["games"]["limited"] is True       # the row without a 7d window is not listed by default
    assert cov["games"]["by_window"]["24h"]["rising"] == 1                      # but it is still counted in its own window


def test_thin_baseline_spikes_are_excluded_not_just_clamped():
    from nicheclock.catalog.scoring import baseline_ok, trend_of

    # 5,000/day today against 300/day before (a Main Page slot): floor 5,000 -> baseline must be >= 500
    spike = dict(ch([300] * 27 + [5000]), floor=5000.0, baseline=300.0)
    assert not baseline_ok(spike) and trend_of({"wikipedia": spike}) is None
    real = dict(ch([800] * 27 + [5000]), floor=5000.0, baseline=800.0)
    assert baseline_ok(real) and trend_of({"wikipedia": real})[:2] == ("rising", "strong")
    assert baseline_ok({"value": 300})                                                    # HN / chart: not judged


# -- audit P0s: past-peak rows, news without baseline, ratio provenance, local news signals ----------

def test_peak_info_and_fading_rows_are_not_rising():
    from nicheclock.catalog.scoring import peak_info, trend_of

    s = [1000] * 10 + [2360000, 900000, 450000, 450000]               # peaked 3 days ago, now 19% of the peak
    pk = peak_info(s)
    assert pk["peak"] == 2360000 and pk["peak_days_ago"] == 3 and pk["from_peak"] == 0.191
    c = dict(ch([1000] * 14 + s[-14:]), floor=5000.0, baseline=1000.0, **pk)
    assert trend_of({"wikipedia": c}) == ("fading", None, "fading")
    live = dict(ch([1000] * 27 + [5000]), floor=5000.0, baseline=1000.0, **peak_info([1000] * 27 + [5000]))
    assert trend_of({"wikipedia": live})[:2] == ("rising", "strong")
    # a Hacker News snapshot cannot keep a faded topic rising; with no history channel at all HN alone still lists it
    assert trend_of({"wikipedia": c, "hn": {"value": 300}}) == ("fading", None, "fading")
    assert trend_of({"hn": {"value": 300}})[:2] == ("rising", "strong")
    still = dict(ch([1000] * 27 + [4000]), floor=500.0, baseline=1000.0, **peak_info([1000] * 27 + [4000]))
    assert trend_of({"wikipedia": c, "news": still})[:2] == ("rising", "strong")             # news still climbing


def test_fading_topic_row_is_listed_with_peak_info_and_never_ranked_as_a_riser():
    s = [1000] * 10 + [2360000, 900000, 450000, 450000]
    t = assemble_topic(candidate(wiki_series={"en": [1000] * 14 + s}), CLS, END, NOW, None)
    assert (t["trend"], t["rise"], t["status"], t["rank_score"], t["score"]) == ("fading", None, "fading", 0.0, 0)
    assert t["peak"]["value"] == 2360000 and t["peak"]["days_ago"] == 3 and t["peak"]["from_peak"] < 0.5 and t["volume"] == 450000
    assert t["ratio"] > 100 and t["ratio_window"] in ("24h", "48h") and t["ratio_channel"] == "wikipedia"       # the real ratio stays visible


def test_row_ratio_says_which_window_and_channel_it_came_from():
    t = assemble_topic(candidate(wiki_series={"en": surge(2000, 9000)}), CLS, END, NOW, None)
    assert t["ratio_window"] == "24h" and t["ratio_channel"] == "wikipedia" and t["ratio"] == t["windows"]["24h"]["ratio"] == 4.5
    assert t["windows"]["24h"]["channel"] == "wikipedia" and t["peak"]["from_peak"] == 1.0


def test_news_channel_without_a_baseline_has_no_ratio():
    from nicheclock.catalog.trending import _news_channel

    c = _news_channel([0.0] * 27 + [400.0], [["t", "https://x.example/a"]], "WW")
    assert c["ratio"] is None and c["windows"] == {} and "no baseline" in c["ratio_note"] and c["value"] == 400.0
    ok = _news_channel([100.0] * 27 + [400.0], [], "WW")
    assert ok["ratio"] == 4.0 and ok["windows"]["24h"]["active"]


def test_country_news_names_must_be_local_signals_not_world_news_that_mentions_a_place():
    from datetime import timedelta

    end = date(2026, 10, 31)
    ww = {"Wire Agency": 400, "US Senator": 300, "Local Minister": 30}
    et = {"Wire Agency": 20, "US Senator": 15, "Local Minister": 25}            # Local Minister: 83% of its articles mention Ethiopia
    day = {"day": "x", "files": 6, "scale": 16.0, "counts": {"WW": ww, "ET": et}, "examples": {}}
    prev = {end - timedelta(days=i): {"day": "x", "files": 6, "scale": 16.0, "counts": {"WW": {}, "ET": {}}, "examples": {}} for i in range(1, 10)}
    prev[end] = day
    assert newsmod.candidate_names(prev, end, region="ET") == ["Local Minister"]
    assert "Wire Agency" in newsmod.candidate_names(prev, end, region="WW")


def test_experimental_mechanism_hides_sources_unless_enabled(monkeypatch):
    import nicheclock.catalog.build as build

    monkeypatch.setattr(build, "EXPERIMENTAL_IDS", ("github",))
    monkeypatch.setattr(build, "SOURCE_FILTER", [dict(f, experimental=f["key"] == "github") for f in build.SOURCE_FILTER])
    docs = {"global": {"date": "2026-10-30", "topics": []}}
    rstat = {"global": {"wikipedia": {"status": "ok", "detail": "x", "topics": 0}}}
    off = build.meta_doc(docs, None, rstat, {}, NOW)
    src = {s["id"]: s for s in off["sources"]}
    assert off["experimental"] is False and src["github"]["status"] == "experimental_off" and src["github"]["ok"] is None
    assert "github" not in {f["key"] for f in off["source_filter"]}
    on = build.meta_doc(docs, None, {"global": {"github": {"status": "ok", "detail": "174 repos", "topics": 3}}}, {}, NOW, experimental=True)
    assert "github" in {f["key"] for f in on["source_filter"]} and {s["id"]: s for s in on["sources"]}["github"]["status"] == "used"


def test_github_runs_on_the_rest_api_and_gh_archive_is_excluded_in_the_ledger():
    from nicheclock.catalog import regions as rg
    from nicheclock.catalog.feeds import EXPERIMENTAL

    by = {e["id"]: e for e in rg.ledger()}
    assert by["github"]["status"] == "used" and "REST" in by["github"]["label"] and by["gharchive"]["status"] == "excluded"
    assert EXPERIMENTAL == ()


def test_a_source_waiting_for_its_key_is_not_offered_as_a_filter():
    from nicheclock.catalog.build import meta_doc

    docs = {"global": {"date": "2026-10-30", "topics": []}}
    m = meta_doc(docs, None, {"global": {"stackexchange": {"status": "pending_key", "detail": "no key", "topics": 0}}}, {}, NOW)
    assert "stackexchange" not in {f["key"] for f in m["source_filter"]}
    assert {s["id"]: s for s in m["sources"]}["stackexchange"]["status"] == "pending_key"

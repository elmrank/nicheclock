"""Junk and safety filter: adult, utility pages, bot-like traffic (no network)."""
from datetime import date, datetime, timezone

from nicheclock.catalog import junk
from nicheclock.catalog.categories import Classification
from nicheclock.catalog.trending import Ctx, class_ids, drop_bot_rows, drop_junk_candidates

END = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


def test_adult_by_label_class_and_description_but_not_ordinary_topics():
    assert junk.adult_reason(".xxx", "sponsored top-level Internet domain intended for pornographic websites", ["Q29380"]) == "label"
    assert junk.adult_reason("Some Studio", "", ["Q30941437"]) == "class"
    assert junk.adult_reason("Mia Khalifa", "Lebanese-American former pornographic film actress (born 1993)", []) == "text"
    assert junk.adult_reason("XVideos", "pornographic video sharing and viewing website", ["Q11074464"]) == "label"
    assert junk.adult_reason("Another Site", "", ["Q11074464"]) == "class"
    for label, desc in (("Sex education", "teaching about human sexuality"), (".com", "commercial top-level domain"),
                        ("Essex", "county in England"), ("Formula One", "motorsport championship")):
        assert junk.adult_reason(label, desc, []) is None, label
    assert junk.adult_reason("Erotica", "1992 studio album by Madonna", []) == "text"        # conservative: the word alone is enough


def test_utility_pages_are_recognised_by_title_and_real_titles_stay():
    for t in ("Deaths in October 2026", "Deaths in 2026", "deaths in march 2025", "List of The Simpsons episodes", "Main Page", "Special:Search",
              "Wikipedia:Featured pictures", "Portal:Current events", "Lists of earthquakes"):
        assert junk.utility_reason("x", [t]) == "title", t
    for t in ("Formula One", "Listing", "Index (publishing)", "Deaths in Venice", "Deaths in the Family", "Wikipedia", "Timeline of the Big Bang",
              "Outline of science", "Glossary of golf"):
        assert junk.utility_reason(t, [t]) is None, t


def test_access_skew_and_week_peak_share_heuristics():
    assert junk.access_skew([5] * 14, [995] * 14) == 0.995
    assert junk.access_skew([500] * 14, [500] * 14) == 0.5
    assert junk.access_skew([1] * 14, [2] * 14) is None                                     # too few views to judge
    assert junk.week_peak_share([100] * 7) == 1 / 7 and junk.week_peak_share([0] * 7) == 0.0
    assert junk.week_peak_share([100] * 6 + [90000]) > 0.99
    assert junk.single_day_spike([100] * 6 + [90000] + [120] * 7) == 6                       # helper kept, no longer a rule by itself


def test_bot_rule_is_strict_a_mobile_heavy_celebrity_is_not_a_bot():
    flat = [1000] * 14
    assert junk.bot_reason([5] * 14, [995] * 14, flat) == "skew 100%"                                  # 99.5%: skew alone is enough
    assert junk.bot_reason([20] * 14, [980] * 14, flat) == "skew 98%"                                  # exactly 98%
    assert junk.bot_reason([90] * 14, [910] * 14, flat) is None                                         # 91% skew, flat traffic: a celebrity
    assert junk.bot_reason([60] * 14, [940] * 14, flat) is None                                         # 94%: still fine
    spike = [100] * 6 + [90000]
    assert junk.bot_reason([90] * 14, [910] * 14, spike).startswith("single-day spike")                # one day, and 91% skew
    assert junk.bot_reason([200] * 14, [800] * 14, spike) is None                                       # one day but only 80% skew: fine
    assert junk.bot_reason([500] * 14, [500] * 14, spike) is None
    assert junk.bot_reason(None, None, spike) is None and junk.bot_reason([1] * 14, [1] * 14, spike) is None


def test_tally_counts_and_keeps_five_examples():
    t = junk.Tally()
    for i in range(8):
        t.add("adult", f"X{i}", "text")
    t.add("bot", "Y", "skew 99%")
    d = t.to_dict()
    assert d["removed"] == {"adult": 8, "utility": 0, "bot": 1} and d["total"] == 9 and len(d["examples"]["adult"]) == 5 and "utility" not in d["examples"]


class FakeClassifier:
    def __init__(self, ladder):
        self.ladder = ladder

    def _prefetch(self, seeds, prop, depth):
        pass

    def _closure(self, seeds, prop, depth):
        out = {s: 0 for s in seeds}
        for s in seeds:
            for up in self.ladder.get(s, []):
                out[up] = 1
        return out


def rec(qid, label, description="", p31=(), p106=()):
    return {"id": qid, "label": label, "description": description, "aliases": [], "titles": {}, "sitelinks": 9,
            "claims": {"P31": list(p31), "P106": list(p106)}}


def test_drop_junk_candidates_removes_adult_and_utility_and_reports_them():
    ctx = Ctx(None, None, None, FakeClassifier({"QSUB": ["Q185529"]}), END, NOW, lambda *_: None, [])
    recs = {"Q1": rec("Q1", ".xxx", "sponsored top-level domain"), "Q2": rec("Q2", "Some Film", "film", p31=["QSUB"]),
            "Q3": rec("Q3", "Deaths in October 2026", "list"), "Q4": rec("Q4", "Formula One", "motorsport championship"),
            "Q5": rec("Q5", "Fine Person", "actor", p106=["Q33999"])}
    cands = {q: {"wiki": {"en": {"title": r["label"]}}} for q, r in recs.items()}
    drop_junk_candidates(ctx, cands, recs)
    assert sorted(cands) == ["Q4", "Q5"]
    d = ctx.tally.to_dict()
    assert d["removed"] == {"adult": 2, "utility": 1, "bot": 0} and ".xxx (label)" in d["examples"]["adult"]
    assert class_ids(ctx, recs["Q2"]) == ["QSUB", "Q185529"]


def row(label, series, rank=5.0, trend="rising", wiki="en"):
    return {"label": label, "trend": trend, "rank_score": rank, "channels": {"wikipedia": {"wiki": wiki, "title": label, "series": series}}}


def test_drop_bot_rows_uses_the_access_split_and_caches_it():
    from nicheclock.catalog.cache import Cache

    calls = []

    class C:
        def get(self, url):
            calls.append(url)
            raise AssertionError("pageviews.access_split is patched in this test")

    import nicheclock.catalog.pageviews as pv

    def fake(client, lang, title, end, days=14):
        calls.append(title)
        return ([5] * 14, [995] * 14) if title == "Botty" else ([90] * 14, [910] * 14) if title == "Celeb" else ([400] * 14, [600] * 14) if title != "Spiky" else ([90] * 14, [910] * 14)

    pv.access_split, orig = fake, pv.access_split
    try:
        class RT:
            cache = Cache(":memory:")
            client = C()

        ctx = Ctx(RT, None, None, None, END, NOW, lambda *_: None, [])
        rows = [row("Botty", [1000] * 14), row("Human", [1000] * 13 + [4000]), row("Spiky", [100] * 13 + [90000]),
                row("Celeb", [1000] * 14), row("Quiet", [100] * 14, trend="steady")]
        kept = drop_bot_rows(ctx, rows, END)
        assert [t["label"] for t in kept] == ["Human", "Celeb", "Quiet"]
        d = ctx.tally.to_dict()
        assert d["removed"]["bot"] == 2 and any("Botty" in e for e in d["examples"]["bot"]) and any("Spiky" in e for e in d["examples"]["bot"])
        assert sorted(calls) == ["Botty", "Celeb", "Human", "Spiky"]             # steady rows are not checked
        drop_bot_rows(ctx, rows, END)
        assert len(calls) == 4                                                   # second run reads the cache
    finally:
        pv.access_split = orig


def test_access_split_reads_desktop_and_mobile_and_refuses_unpublished_days():
    from nicheclock.catalog import pageviews as pv

    def payload(v, days):
        return {"items": [{"timestamp": f"202610{d:02d}00", "views": v} for d in days]}

    class R:
        def __init__(self, status, body=None):
            self.status_code, self._b = status, body

        def json(self):
            return self._b

    seen = []

    class C:
        def get(self, url):
            seen.append(url)
            return R(200, payload(10 if "/desktop/" in url else 990, range(18, 32)))

    d, m = pv.access_split(C(), "en", "Some Page", END)
    assert d == [10] * 14 and m == [990] * 14 and "/desktop/user/" in seen[0] and "/mobile-web/user/" in seen[1]

    class Late:
        def get(self, url):
            return R(200, payload(5, range(17, 31)))

    assert pv.access_split(Late(), "en", "Some Page", END) is None

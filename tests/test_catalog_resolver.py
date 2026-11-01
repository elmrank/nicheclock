"""Catalog resolver: text -> QID with popularity-based disambiguation and caching (no network)."""
from nicheclock.catalog.cache import Cache
from nicheclock.catalog.resolver import Resolver, norm


def ent(qid, label, desc="", sitelinks=10, p31=(), titles=None):
    return {"id": qid, "label": label, "description": desc, "aliases": [], "sitelinks": sitelinks,
            "titles": titles or {}, "claims": {"P31": list(p31)} if p31 else {}}


class FakeWD:
    """Just the three methods the resolver calls, counting search calls."""

    def __init__(self, hits, ents, titles=None):
        self.cache = Cache(":memory:")
        self.hits, self.ents, self.titles = hits, {e["id"]: e for e in ents}, titles or {}
        self.searches = 0

    def search(self, text, lang="en", limit=7):
        self.searches += 1
        return self.hits.get(text, [])

    def entities(self, ids):
        return {i: self.ents.get(i) for i in ids}

    def qids_for_titles(self, site, titles):
        return {t: self.titles.get((site, t)) for t in titles}


def hit(qid, label, match="label", text=None):
    return {"id": qid, "label": label, "description": "", "match": match, "match_text": text or label}


def test_popular_entity_beats_obscure_namesake():
    wd = FakeWD({"Mercury": [hit("Q1", "Mercury"), hit("Q2", "Mercury"), hit("Q3", "Mercury")]},
                [ent("Q1", "Mercury", "chemical element", 150), ent("Q2", "Mercury", "village", 3),
                 ent("Q3", "Mercury", "Roman god", 80)])
    r = Resolver(wd).resolve("Mercury")
    assert r.qid == "Q1" and r.match == "label" and r.confidence > 0.4
    assert [a["qid"] for a in r.alternatives] == ["Q3", "Q2"]


def test_close_runner_up_is_flagged_ambiguous_with_alternatives():
    wd = FakeWD({"Python": [hit("Q1", "Python"), hit("Q2", "Python")]},
                [ent("Q1", "Python", "programming language", 100), ent("Q2", "Python", "snake genus", 90)])
    r = Resolver(wd).resolve("Python")
    assert r.qid == "Q1" and r.ambiguous and r.alternatives[0]["qid"] == "Q2"


def test_context_words_pick_the_matching_description():
    wd = FakeWD({"Python": [hit("Q1", "Python"), hit("Q2", "Python")]},
                [ent("Q1", "Python", "programming language", 100), ent("Q2", "Python", "genus of snakes", 90)])
    assert Resolver(wd).resolve("Python", context="snakes").qid == "Q2"


def test_exact_label_beats_alias_and_fuzzy():
    wd = FakeWD({"Jordan": [hit("Q1", "Michael Jordan", "alias", "Jordan"), hit("Q2", "Jordan")]},
                [ent("Q1", "Michael Jordan", "", 140), ent("Q2", "Jordan", "country", 120)])
    r = Resolver(wd).resolve("Jordan")
    assert r.qid == "Q2" and r.match == "label"


def test_hint_person_prefers_humans():
    wd = FakeWD({"Jordan": [hit("Q1", "Jordan"), hit("Q2", "Jordan")]},
                [ent("Q1", "Jordan", "country", 120), ent("Q2", "Jordan", "basketball player", 100, p31=["Q5"])])
    assert Resolver(wd).resolve("Jordan", hint="person").qid == "Q2"


def test_disambiguation_and_list_pages_are_skipped():
    wd = FakeWD({"Mars": [hit("Q1", "Mars"), hit("Q2", "Mars")]},
                [ent("Q1", "Mars", "Wikimedia disambiguation page", 200, p31=["Q4167410"]),
                 ent("Q2", "Mars", "planet", 100)])
    assert Resolver(wd).resolve("Mars").qid == "Q2"
    only_dis = FakeWD({"X": [hit("Q1", "X")]}, [ent("Q1", "X", "", 50, p31=["Q4167410"])])
    r = Resolver(only_dis).resolve("X")
    assert r.qid is None and "non-topic" in r.reason


def test_min_sitelinks_rejects_unknown_entities():
    wd = FakeWD({"Obscure": [hit("Q1", "Obscure")]}, [ent("Q1", "Obscure", "", 2)])
    r = Resolver(wd).resolve("Obscure", min_sitelinks=10)
    assert r.qid is None and "sitelinks" in r.reason


def test_no_hits_and_empty_text():
    r = Resolver(FakeWD({}, []))
    assert r.resolve("zzzz").qid is None and r.resolve("  ").reason == "empty text"


def test_qid_and_wikipedia_url_resolve_without_search():
    wd = FakeWD({}, [ent("Q42", "Douglas Adams", "writer", 200)], titles={("enwiki", "Douglas Adams"): "Q42"})
    r = Resolver(wd)
    assert r.resolve("q42").qid == "Q42" and r.resolve("Q42").match == "qid"
    u = r.resolve("https://en.wikipedia.org/wiki/Douglas_Adams")
    assert u.qid == "Q42" and u.match == "url"
    assert r.resolve("Q999").reason == "unknown QID"
    assert wd.searches == 0


def test_resolution_is_cached():
    wd = FakeWD({"Formula One": [hit("Q1", "Formula One")]}, [ent("Q1", "Formula One", "motorsport", 146)])
    r = Resolver(wd)
    a, b = r.resolve("Formula One"), r.resolve("Formula One")
    assert a.qid == b.qid == "Q1" and wd.searches == 1


def test_norm_strips_accents_case_and_punctuation():
    assert norm("  Barça — F.C.  ") == "barca f c"

import pytest
from onboard_fakes import FakeClient, FakeWiki

from nicheclock.onboard import extract as X


def test_slug_and_url_helpers():
    assert X.slug_of("https://a.test/birds/snowy-owl-best-builds.html") == "snowy-owl-best-builds"
    assert X.slug_of("https://a.test/posts/12345/") == "posts"          # id-only segment skipped
    assert X.slug_of("https://a.test/") == ""
    assert X.section_of("https://a.test/birds/x") == "birds"
    assert X.section_of("https://a.test/flat-slug") == "/"
    assert not X.is_content_url("https://a.test/tag/rpg")
    assert not X.is_content_url("https://a.test/wp-content/uploads/a.jpg")
    assert X.is_content_url("https://a.test/guides/x")


def test_slug_tokenization_accents_and_junk():
    assert X.tokenize("Café-Menu_Bird-watch 2025") == ["cafe", "menu", "bird", "watch", "2025"]
    phrases, formats, excludes = X.analyze(X.tokenize("how-to-beat-snowy-owl-boss-2025-a1b2c3d4e5f6"))
    assert "snowy owl boss" in phrases and "snowy owl" in phrases
    assert "how to" in formats
    # digits, hex ids and stop words never become phrases
    assert not any("2025" in p or "a1b2c3" in p for p in phrases)
    assert not any(p.startswith("to ") or p.endswith(" to") for p in phrases)


def test_ngrams_do_not_cross_format_or_stop_words():
    phrases, formats, excludes = X.analyze(X.tokenize("snowy-owl-tier-list-for-beginners-and-tips"))
    assert phrases == {"snowy", "owl", "snowy owl"}
    assert formats == {"tier list", "tips"}
    assert excludes == {"beginners", "tips"}


def test_format_split_canonicalises_and_prefers_longest():
    _, f, _ = X.analyze(X.tokenize("patch-notes-vs-patch-note"))
    assert f == {"patch notes", "vs"}
    _, f, _ = X.analyze(X.tokenize("case-study-and-checklist-cheatsheet"))
    assert f == {"case study", "checklist", "cheat sheet"}
    _, f, _ = X.analyze(X.tokenize("the-best-tier-lists-in-2025"))
    assert f == {"best", "tier list"}


def test_brand_tokens_are_dropped():
    brand = X.brand_tokens(["https://www.birdnotes.org"])
    assert brand == {"birdnotes"}
    phrases, _, _ = X.analyze(X.tokenize("birdnotes-snowy-owl"), brand)
    assert phrases == {"snowy", "owl", "snowy owl"}


def test_clean_titles_strips_shared_brand_segment():
    titles = ["Snowy Owl Builds | Birdsite", "Kestrel Guide | Birdsite", "Birdsite - Home", "No separator"]
    clean, brands = X.clean_titles(titles)
    assert clean[0] == "Snowy Owl Builds" and clean[1] == "Kestrel Guide"
    assert "birdsite" in brands
    assert clean[3] == "No separator"
    clean, _ = X.clean_titles(["A: B"], site_names=["Whatever"])
    assert clean == ["A: B"]


def _docs(urls_titles):
    return X.build_docs([u for u, _ in urls_titles], {u: (t, "") for u, t in urls_titles if t})


SITE = [(f"https://a.test/{s}", None) for s in (
    "peregrine-falcon-tier-list", "peregrine-falcon-best-spots", "peregrine-falcon-patch-notes", "peregrine-falcon-map",
    "kestrel-patch-notes", "kestrel-best-builds", "kestrel-review",
    "compliance-checklist", "compliance-guide", "how-to-compliance", "free-compliance-tips",
    "guide-for-beginners",
)]
BG = X.Background(n_docs=1000, df={"compliance": 300, "guide": 400, "map": 200, "review": 250})


def test_tfidf_ranking_prefers_site_specific_phrases():
    ext = X.extract(_docs(SITE), BG)
    top = ext.ranked
    assert top[0] == "peregrine falcon"
    assert top.index("peregrine falcon") < top.index("compliance")
    assert "kestrel" in top[:3]
    # unigram "arc" is subsumed by "peregrine falcon" (covers all its pages)
    assert "arc" not in top and "raiders" not in top


def test_ranking_is_deterministic_and_bg_changes_order():
    a = X.extract(_docs(SITE), BG).ranked
    assert a == X.extract(list(reversed(_docs(SITE))), BG).ranked
    flat = X.extract(_docs(SITE), X.Background(n_docs=1, df={})).ranked
    assert flat.index("compliance") <= a.index("compliance")


def test_formats_and_excludes_are_counted_per_doc():
    ext = X.extract(_docs(SITE), BG)
    f = dict(ext.formats)
    assert f["patch notes"] == 2 and f["tier list"] == 1 and f["guide"] == 2 and f["how to"] == 1
    assert dict(ext.excludes) == {"free": 1, "tips": 1, "beginners": 1}


def test_min_df_filters_singletons():
    df, score = X.score_phrases([{"a b"}, {"a b"}, {"c"}], X.Background(10, {}), min_df=2)
    assert set(df) == {"a b"} and score["a b"] > 0


def test_sklearn_backend_matches_python():
    pytest.importorskip("sklearn")
    docs = [d.phrases for d in _docs(SITE)]
    assert X.score_phrases(docs, BG, backend="sklearn") == X.score_phrases(docs, BG, backend="python")


def test_load_background_missing_file_is_empty(tmp_path):
    bg = X.load_background(str(tmp_path / "nope.json.gz"))
    assert bg.df == {} and bg.n_docs == 1


def test_bundled_background_has_provenance():
    bg = X.load_background()
    assert bg.meta.get("source") and "CC BY-SA" in bg.meta.get("licence_of_source", "")
    assert bg.n_docs > 5000 and len(bg.df) > 5000
    assert bg.df.get("united states", 0) > 0 or bg.df.get("united", 0) > 0


# ---------------------------------------------------------------- entities


def _resolver(tmp_path, wiki):
    client = FakeClient({}, wiki=wiki)
    return X.EntityResolver(client, tmp_path / "cache.json"), client


def test_entity_resolution_variants_redirects_disambiguation_and_cache(tmp_path):
    wiki = FakeWiki(
        pages={"Snowy Owl": "ok", "Wild Birds Directive": "US law", "Compliance": "!",
               "Kestrel": "2021 mobile app", "Vendor": "Seller of goods or services", "Governance": "ok", "Villain": "Evil character or person",
               "Migratory Bird Treaty Act": "US law"},
        redirects={"Snowy owl": "Snowy Owl", "WBD": "Wild Birds Directive",
                   "Nest box": "Villain", "Bird treaty act": "Migratory Bird Treaty Act"},
        opensearch={"bird treaty act": ["Bird treaty act"]},
    )
    r, client = _resolver(tmp_path, wiki)
    got = r.resolve(["snowy owl", "wbd", "compliance", "kestrel", "vendor", "governance", "nest box", "bird treaty act",
                     "zzz qqq"], limit=10)
    # single words need a short description that names a thing: "Vendor"/"Governance" are ordinary nouns
    # a redirect to an unrelated title ("nest box" -> "Villain") is rejected; one sharing a word is kept
    assert got == {"snowy owl": "Snowy Owl", "wbd": "Wild Birds Directive", "kestrel": "Kestrel",
                   "bird treaty act": "Migratory Bird Treaty Act"}
    calls = len(wiki.calls)
    assert calls <= 7                                  # one batch + opensearch fallback for multi-word misses
    # second run: everything (misses too) comes from the on-disk cache, zero requests
    r2, client2 = _resolver(tmp_path, wiki)
    assert r2.resolve(["snowy owl", "wbd", "compliance", "kestrel", "vendor", "governance", "nest box",
                       "bird treaty act", "zzz qqq"], limit=10) == got
    assert len(wiki.calls) == calls and client2.requests == []


def test_entity_opensearch_fallback_requires_exact_normalised_match(tmp_path):
    wiki = FakeWiki(pages={"Merlin Bird Atlas": "ok"}, opensearch={
        "merlin bird atlas": ["Merlin Bird Atlas (app)", "Merlin Bird Atlas"],
        "snow goose": ["Snow Goose guide"],
    })
    r, _ = _resolver(tmp_path, wiki)
    # "Merlin Bird Atlas (app)" normalises to "merlin bird atlas" but does not exist; the second hit does
    got = r.resolve(["merlin bird atlas", "snow goose"], limit=5)
    assert got == {"merlin bird atlas": "Merlin Bird Atlas"}


def test_is_named_entity():
    assert X.is_named_entity("2021 mobile app") and X.is_named_entity("American AI research organization")
    assert X.is_named_entity("European Union regulation on data protection")
    assert not X.is_named_entity("Seller of goods or services") and not X.is_named_entity("")
    assert not X.is_named_entity("Character in a story who has a negative effect")


def test_entity_limit_and_api_failure(tmp_path):
    wiki = FakeWiki(pages={"Alpha": "Software company", "Beta": "Software company"})
    r, _ = _resolver(tmp_path, wiki)
    assert r.resolve(["alpha", "beta"], limit=1) == {"alpha": "Alpha"}
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("net down")
    r2 = X.EntityResolver(Boom(), tmp_path / "c2.json")
    assert r2.resolve(["alpha"], limit=3) == {}


def test_split_keywords_entities():
    class R:
        def resolve(self, phrases, limit):
            return {p: p.title() for p in phrases[:limit] if p.startswith(("s", "b"))}
    kw, ents = X.split_keywords_entities(["snowy owl", "tier", "bird treaty act"], R(), entity_limit=5)
    assert kw == ["tier"] and ents == {"snowy owl": "Snowy Owl", "bird treaty act": "Bird Treaty Act"}
    assert X.split_keywords_entities(["a"], None) == (["a"], {})


# ---------------------------------------------------------------- catalogue tokens

CBG = X.Background(n_docs=1000, df={"text": 15, "arc": 9, "sol": 2, "mod": 2, "co": 300, "op": 7, "fra": 3, "eu": 14})


def test_is_catalogue_token():
    for code in ("xqz", "kvt", "bnr", "zwp", "qtl", "fra", "h100", "sta", "2025", "7"):
        assert X.is_catalogue_token(code, CBG), code
    for ok in ("arc", "text", "ai", "gdpr", "rpg", "ps5", "mods", "co", "op", "deck", "gpt", "eu"):
        assert not X.is_catalogue_token(ok, CBG), ok
    assert not X.is_catalogue_token("kestrel", CBG)        # longer than 4 chars: never a code
    assert not X.is_catalogue_token("zzzzz", None)


def test_codes_are_dropped_as_phrases_but_words_survive():
    docs = [frozenset({"xqz", "kvt", "xqz 12", "kvt standard", "peregrine falcon", "optic hawk", "mods", "text", "snowy owl"})] * 3
    df, score = X.score_phrases(docs, CBG, min_df=2)
    assert {"xqz", "kvt", "xqz 12", "kvt standard"}.isdisjoint(df)     # codes alone, or glued to a longer phrase
    assert {"peregrine falcon", "optic hawk", "mods", "text", "snowy owl"} <= set(df)   # short real words stay


def test_template_phrases_from_titles_not_in_slugs():
    slugs = [{"optic hawk"}, {"swift"}, {"bird", "starter"}] * 20
    titles = [{"bird profile", "range", f"species{i}"} for i in range(6)] + [{"bird profile", "range"}] * 4 + [{"bird", "owls"}] * 2
    tmpl = X.template_phrases(slugs, titles)
    assert tmpl == {"bird profile", "range"}          # "bird" is also in slugs (topic), so it is not template
    assert X.template_phrases(slugs, titles[:3]) == frozenset()     # too few titles to tell
    docs = X.build_docs(["https://a.test/species/blue-jay-bnr-216"], {"https://a.test/species/blue-jay-bnr-216":
                        ("Blue Jay (BNR #216) Bird Profile, Range & Habitat", "")}, drop=frozenset({"bird profile", "habitat"}))
    assert "blue jay" in docs[0].phrases and "bird profile" not in docs[0].phrases and "habitat" not in docs[0].phrases


def test_median_daily_views_cached_and_robust(tmp_path):
    from datetime import date
    client = FakeClient({}, wiki=FakeWiki({}))
    client.views = {"Snowy Owl": [100, 9000, 200, 300, 250], "Quiet": [1, 2, 3]}
    r = X.EntityResolver(client, tmp_path / "c.json")
    today = date(2026, 10, 31)
    assert r.median_daily_views("Snowy Owl", today) == 250          # median, not the spike
    n = len(client.requests)
    assert r.median_daily_views("Snowy Owl", today) == 250 and len(client.requests) == n       # cached
    assert X.EntityResolver(client, tmp_path / "c.json").median_daily_views("Snowy Owl", today) == 250
    assert len(client.requests) == n                                # ... also across runs
    assert r.median_daily_views("Snowy Owl", date(2026, 12, 15)) == 250 and len(client.requests) == n + 1  # > 30 days old
    assert r.median_daily_views("Unknown page", today) is None


def test_describe_survives_cache_reload(tmp_path):
    wiki = FakeWiki(pages={"Kestrel": "2021 mobile app"})
    r, _ = _resolver(tmp_path, wiki)
    r.resolve(["kestrel"], limit=3)
    assert X.EntityResolver(FakeClient({}), tmp_path / "cache.json").describe("Kestrel") == "2021 mobile app"


def test_leads_batched_cached_and_failure_tolerant(tmp_path):
    wiki = FakeWiki(pages={"Alpha": "ok", "Beta": "ok"}, extracts={"Alpha": "Alpha is a name. " * 60})
    r, client = _resolver(tmp_path, wiki)
    got = r.leads(["Alpha", "Beta", "Missing"])
    assert got["Alpha"].startswith("Alpha is a name.") and len(got["Alpha"]) == 600 and got["Beta"] == ""
    assert sum(1 for c in wiki.calls if c.get("prop") == "extracts") == 1          # one batched request
    n = len(wiki.calls)
    assert r.leads(["Alpha", "Beta"])["Alpha"].startswith("Alpha") and len(wiki.calls) == n       # cached
    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("down")
    assert X.EntityResolver(Boom(), tmp_path / "other.json").leads(["Alpha"]) == {"Alpha": ""}


def test_api_failures_are_not_cached_as_misses(tmp_path):
    wiki = FakeWiki(pages={"Snowy Owl": "ok"})
    class Flaky:
        fail = True
        def get(self, url, params=None, **kw):
            if self.fail:
                raise RuntimeError("blip")
            return FakeClient({}, wiki=wiki).get(url, params, **kw)
    cl = Flaky()
    r = X.EntityResolver(cl, tmp_path / "c.json")
    assert r.resolve(["snowy owl", "snow goose"], limit=5) == {}
    assert "snowy owl" not in r.cache and "snow goose" not in r.cache          # nothing cached on failure
    cl.fail = False
    assert r.resolve(["snowy owl", "snow goose"], limit=5) == {"snowy owl": "Snowy Owl"}
    assert r.cache["snow goose"] == ""                                           # a real miss is cached


def test_misses_beyond_the_search_budget_are_not_cached_and_acronym_variant(tmp_path):
    assert "Bird feeder" in X._title_variants("bird feeder") and "Bird treaty act" in X._title_variants("bird treaty act")
    wiki = FakeWiki(pages={}, opensearch={})
    r = X.EntityResolver(FakeClient({}, wiki=wiki), tmp_path / "c.json", opensearch_limit=1)
    r.resolve(["aa bb", "cc dd", "ee ff"], limit=5)
    assert list(k for k in r.cache if not k.startswith(("desc:", "lead:"))) == ["aa bb"]   # only the searched miss is final

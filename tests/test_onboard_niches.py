from onboard_fakes import HOST, FakeClient, FakeWiki, site_files

from nicheclock.onboard import niches as N
from nicheclock.onboard.crawl import crawl_site
from nicheclock.onboard.extract import Background, Doc, EntityResolver, build_docs


def doc(url, *phrases, section=None):
    sec = section or ("/" if url.count("/") < 4 else url.split("/")[3])
    return Doc(url, sec, frozenset(phrases))


def two_section_docs():
    docs = []
    for i in range(12):
        docs.append(doc(f"https://a.test/birds/g{i}", "snowy owl", "kestrel") if i % 2 else
                    doc(f"https://a.test/birds/g{i}", "snowy owl", "nuthatch"))
    for i in range(12):
        docs.append(doc(f"https://a.test/gear/d{i}", "starter kit", "nest box") if i % 2 else
                    doc(f"https://a.test/gear/d{i}", "starter kit", "wide horizons"))
    return docs


PHRASES = ["snowy owl", "kestrel", "nuthatch", "starter kit", "nest box", "wide horizons"]


def test_cluster_splits_by_section_cooccurrence():
    docs = two_section_docs()
    groups = N.cluster_phrases(PHRASES, docs, min_size=3)
    assert sorted(map(sorted, groups)) == sorted([sorted(["starter kit", "wide horizons", "nest box"]),
                                                  sorted(["snowy owl", "nuthatch", "kestrel"])])


def test_cluster_is_deterministic_under_input_order():
    docs = two_section_docs()
    a = N.cluster_phrases(PHRASES, docs)
    b = N.cluster_phrases(PHRASES, list(reversed(docs)))
    assert a == b
    assert a == N.cluster_phrases(PHRASES, docs)


def test_flat_site_without_cooccurrence_is_one_niche():
    docs = [doc(f"https://a.test/p{i}", p) for i, p in enumerate(PHRASES * 3)]
    assert N.cluster_phrases(PHRASES, docs) == [PHRASES]


def test_cluster_never_exceeds_max_niches_and_keeps_all_phrases():
    phrases = [f"topic{i} x" for i in range(30)]
    docs = []
    for i, p in enumerate(phrases):
        for j in range(4):
            docs.append(doc(f"https://a.test/s{i % 8}/{i}-{j}", p, section=f"s{i % 8}"))
    groups = N.cluster_phrases(phrases, docs, max_niches=3, min_size=2)
    assert 1 <= len(groups) <= 3
    assert sorted(p for g in groups for p in g) == sorted(phrases)


def test_cluster_edge_cases():
    assert N.cluster_phrases([], []) == []
    assert N.cluster_phrases(["a b"], [doc("https://a.test/x", "a b")]) == [["a b"]]


def test_classify_and_sources():
    assert N.classify(["boss raid", "speedrun", "esports"], ["tier list"]) == "games"
    assert N.classify(["bird treaty act", "wildlife law", "wbd"], []) == "policy"
    assert N.classify(["llm agents", "python api"], []) == "dev_ai"
    assert N.classify(["ios widgets", "app store"], []) == "apps"
    assert N.classify(["gardening", "tomatoes"], []) == "default"
    assert N.classify(["speedrun", "dlc"], ["patch notes"]) == "games"
    base = ["google_trends", "wikipedia", "hn", "gdelt"]
    assert N.suggest_sources("games") == base
    assert N.suggest_sources("dev_ai") == base + ["github"]
    assert N.suggest_sources("apps") == base + ["apple"]
    assert N.suggest_sources("policy") == base + ["federal_register"]
    assert N.suggest_sources("whatever") == base


def test_end_to_end_drafts_from_fixture_site(tmp_path):
    client = FakeClient(site_files(), wiki=FakeWiki(
        pages={"Snowy Owl": "ok", "Kestrel": "2021 mobile app", "Nuthatch": "2024 mobile app"},
        redirects={"Snowy owl": "Snowy Owl"}))
    c = crawl_site(client, HOST, max_pages=50)
    resolver = EntityResolver(client, tmp_path / "wiki.json")
    bg = Background(n_docs=1000, df={"guide": 400, "review": 300, "commons": 20})
    proj, drafts, ext = N.project_from_crawls("birdsite", [c], bg, resolver, min_size=3)
    assert proj["name"] == "birdsite" and proj["urls"] == [HOST]
    names = [d.name for d in drafts]
    assert len(set(names)) == len(names) and 1 <= len(drafts) <= 5
    all_ents = {e for d in drafts for e in d.entities}
    assert {"Snowy Owl", "Kestrel", "Nuthatch"} <= all_ents
    # entities leave the keyword list, and brand/format/exclude words never become keywords
    all_kw = {k for d in drafts for k in d.keywords}
    assert "snowy owl" not in all_kw
    assert not any("birdsite" in k or "tier list" in k or "beginner" in k for k in all_kw)
    g = next(d for d in drafts if "Snowy Owl" in d.entities)
    assert g.kind == "games" and g.sources == ["google_trends", "wikipedia", "hn", "gdelt"]
    assert "tier list" in g.formats and "patch notes" in g.formats
    assert "beginner" in " ".join(g.exclude) or "tips" in g.exclude
    assert all(n["rss"] == [] and n["upcoming"] is True for n in proj["niches"])
    # deterministic: same crawl, same drafts
    _, drafts2, _ = N.project_from_crawls("birdsite", [c], bg, resolver, min_size=3)
    assert [d.to_config() for d in drafts] == [d.to_config() for d in drafts2]


def test_drafts_without_resolver_keep_everything_as_keywords():
    docs = build_docs([f"{HOST}/games/{g}-tier-list" for g in ("snowy-owl", "kestrel", "nuthatch")] * 1
                      + [f"{HOST}/games/snowy-owl-builds", f"{HOST}/games/kestrel-review", f"{HOST}/games/nuthatch-map"])
    drafts = N.draft_from_docs(docs, Background(100, {}), None)
    assert drafts and all(d.entities == [] for d in drafts)
    assert any("snowy owl" in d.keywords for d in drafts)


# ---------------------------------------------------------------- sections / names


def sectioned_docs():
    docs = []
    for i in range(20):
        docs.append(doc(f"https://a.test/peregrine-falcon/p{i}", "peregrine falcon", "loadout tips" if i % 2 else "peregrine falcon maps",
                        section="peregrine-falcon"))
    for i in range(14):
        docs.append(doc(f"https://a.test/cards/c{i}", "optic hawk", "birding staples", section="cards"))
    for i in range(40):
        docs.append(doc(f"https://a.test/blog/b{i}", "garden birds", "spotting scope" if i % 3 else "field guide",
                        section="blog"))
    for i in range(12):
        docs.append(doc(f"https://a.test/glossary/g{i}", "garden birds", section="glossary"))
    docs += [doc(f"https://a.test/flat-{i}", "garden birds", section="/") for i in range(6)]
    return docs


def test_split_by_section_topical_sections_and_general_rest():
    docs = sectioned_docs()
    groups = N.split_by_section(docs, max_niches=4)
    assert [g[0] for g in groups] == ["peregrine-falcon", "cards", ""]          # blog/glossary are generic -> general
    assert [len(g[1]) for g in groups] == [20, 14, 58]
    assert [g[0] for g in N.split_by_section(docs, max_niches=2)] == ["peregrine-falcon", ""]
    assert N.split_by_section(docs, max_niches=1) is None                   # no room for a section niche
    flat = [doc(f"https://a.test/p{i}", "x y", section="/") for i in range(30)]
    assert N.split_by_section(flat) is None
    only_blog = [doc(f"https://a.test/blog/p{i}", "x y", section="blog") for i in range(30)]
    assert N.split_by_section(only_blog) is None


def test_multi_niche_split_names_and_separation():
    bg = Background(n_docs=1000, df={"indie": 5, "games": 80})
    drafts = N.draft_niches(sectioned_docs(), bg, None, max_niches=4, min_size=2)
    assert [d.section for d in drafts] == ["peregrine-falcon", "cards", ""]
    names = [d.name for d in drafts]
    assert names[0] == "Peregrine Falcon" and names[1] == "Birding Staples" and names[2] == "Garden Birds"
    assert all(len(d.keywords) + len(d.entities) >= 1 for d in drafts)
    # a phrase lives in exactly one niche
    kws = [k for d in drafts for k in d.keywords]
    assert len(kws) == len(set(kws)) and "peregrine falcon" in drafts[0].keywords and "optic hawk" in drafts[1].keywords
    assert "peregrine falcon" not in drafts[2].keywords
    assert all(u.startswith("https://a.test/peregrine-falcon/") for u in drafts[0].urls) and len(drafts[0].urls) == 20
    again = N.draft_niches(list(reversed(sectioned_docs())), bg, None, max_niches=4, min_size=2)
    assert [d.to_config() for d in again] == [d.to_config() for d in drafts]


def test_no_sections_falls_back_to_cooccurrence_and_caps_at_four():
    docs = two_section_docs()
    flat = [Doc(d.url.replace("/birds/", "/").replace("/gear/", "/"), "/", d.phrases) for d in docs]
    drafts = N.draft_niches(flat, Background(1000, {}), None, max_niches=4, min_size=3)
    assert 1 <= len(drafts) <= 4 and all(d.section == "" for d in drafts)
    many = [doc(f"https://a.test/s{i % 9}/{i}", f"topic{i % 9} thing{i % 9}", section=f"s{i % 9}") for i in range(90)]
    assert len(N.draft_niches(many, Background(1000, {}), None, max_niches=4)) <= 4


def test_title_case_names():
    assert N.title_case("us bird act") == "US Bird Act"
    assert N.title_case("eBird checklist") == "eBird Checklist"
    assert N.title_case("state of the union") == "State of the Union"
    assert N.title_case("field guide") == "Field Guide"
    assert N._niche_name(["field guide", "kestrel"], {"kestrel": "Kestrel"}, set()) == "Kestrel"   # top entity wins
    assert N._niche_name(["small teams"], {}, {"Small Teams"}) == "Small Teams (2)"


def test_entity_all_caps_dictionary_word_is_not_an_entity(tmp_path):
    class R:
        def resolve(self, phrases, limit):
            return {"text": "TEXT", "kestrel": "Kestrel"}

        def describe(self, title):
            return ""

        def median_daily_views(self, title):
            return 10

        def leads(self, titles):
            return {}
    docs = [doc(f"https://a.test/p{i}", "text", "kestrel") for i in range(6)]
    bg = Background(1000, {"text": 15})
    d = N.draft_niches(docs, bg, R(), min_size=1)[0]
    assert d.entities == ["Kestrel"] and "text" not in d.entities


def test_keywords_are_ordered_specific_first_and_generic_words_left_out():
    docs = [doc(f"https://a.test/p{i}", "standard", "windrose", "far far west", "tainted grail", "zorkmid")
            for i in range(6)]
    bg = Background(1000, {"standard": 38})
    (d,) = N.draft_niches(docs, bg, None, min_size=1)
    multi = [k for k in d.keywords if " " in k]
    assert d.keywords[: len(multi)] == multi              # multi-word phrases first
    assert "standard" not in d.keywords and {"windrose", "zorkmid"} <= set(d.keywords)


class _Res:
    """Resolver stub: phrase -> title, plus descriptions and median daily views."""

    def __init__(self, titles, desc, views, leads=None):
        self.titles, self._desc, self._views, self._leads = titles, desc, views, leads or {}

    def leads(self, titles):
        return {t: self._leads.get(t, "") for t in titles}

    def resolve(self, phrases, limit):
        return {p: self.titles[p] for p in phrases[:limit] if p in self.titles}

    def describe(self, title):
        return self._desc.get(title, "")

    def median_daily_views(self, title):
        return self._views.get(title)


def test_broad_entities_go_to_context_not_entities():
    res = _Res(
        {"california": "California", "openai": "OpenAI", "field guide": "Field guide", "bird treaty act": "Migratory Bird Treaty Act",
         "snowy owl": "Snowy Owl", "bird feeder": "Bird feeder", "optic hawk": "Optic Hawk"},
        {"California": "U.S. state", "OpenAI": "American AI company", "Field guide": "Type of reference book",
         "Migratory Bird Treaty Act": "US law", "Snowy Owl": "2022 mobile app", "Bird feeder": "ok", "Optic Hawk": "Scope model"},
        {"California": 9000, "OpenAI": 40000, "Field guide": 900, "Migratory Bird Treaty Act": 2500, "Snowy Owl": 4000,
         "Bird feeder": 6500, "Optic Hawk": 300})
    phrases = ["california", "openai", "field guide", "bird treaty act", "snowy owl", "bird feeder", "optic hawk", "bird watching kit"]
    docs = [doc(f"https://a.test/p{i}", *phrases) for i in range(6)]
    (d,) = N.draft_niches(docs, Background(1000, {}), res, min_size=1)
    assert d.context == ["California", "OpenAI", "Field guide", "Bird feeder"] or set(d.context) == {
        "California", "OpenAI", "Field guide", "Bird feeder"}
    assert set(d.entities) == {"Migratory Bird Treaty Act", "Snowy Owl", "Optic Hawk"}
    # specific multi-word entities first; broad phrases do not remain as keywords either
    assert all(" " in e for e in d.entities)
    assert not {"california", "openai", "field guide", "bird feeder"} & set(d.keywords)
    assert "bird treaty act" in d.keywords                      # redirect title differs: the phrase stays a keyword
    assert d.to_config()["context"] == d.context
    assert d.name not in d.context


def test_is_broad_entity_rules():
    from nicheclock.onboard.extract import is_broad_entity as b
    assert b("City in California", None) and b("U.S. state", 10) and b("Capital of France", None)
    assert b("Type of reference book", 100) and b("Area of law", None)
    assert b("2021 mobile app", 5001) and not b("2021 mobile app", 5000)
    assert not b("US law", 2500) and not b("Scope model", None) and not b("", None)


def test_common_noun_entities_are_demoted_to_context():
    res = _Res(
        {"bird feeder": "Bird feeder", "field guide": "Field guide", "operational risk": "Operational risk",
         "snowy owl": "Snowy Owl", "bird treaty act": "Migratory Bird Treaty Act", "optic hawk": "Optic Hawk"},
        {"Bird feeder": "ok", "Field guide": "ok", "Operational risk": "ok", "Snowy Owl": "2022 mobile app",
         "Migratory Bird Treaty Act": "US law", "Optic Hawk": "Scope model"},
        {},
        leads={"Bird feeder": "In the context of backyard birding, bird feeders are devices that supply food for wild birds.",
               "Field guide": "Field guides are carried on hikes. A field guide is a book.",
               "Operational risk": "Operational risk is the risk of loss. Banks manage operational risk daily.",
               "Snowy Owl": "Snowy Owl is a 2014 app. Snowy Owl was developed by Cornell Lab.",
               "Migratory Bird Treaty Act": "The Migratory Bird Treaty Act is a US law.",
               "Optic Hawk": "Optic Hawk is a scope. Optic Hawk magnifies eight times."})
    phrases = ["bird feeder", "field guide", "operational risk", "snowy owl", "bird treaty act", "optic hawk", "feeder tools"]
    docs = [doc(f"https://a.test/p{i}", *phrases) for i in range(6)]
    (d,) = N.draft_niches(docs, Background(1000, {}), res, min_size=1)
    assert set(d.context) == {"Bird feeder", "Field guide", "Operational risk"}
    assert set(d.entities) == {"Snowy Owl", "Migratory Bird Treaty Act", "Optic Hawk"}
    assert not {"bird feeder", "field guide", "operational risk"} & set(d.keywords)


def test_common_noun_reason_rules():
    from nicheclock.onboard.extract import common_noun_reason as r
    # short description names a kind of thing
    assert r("Field guide", "Type of reference book", "") == "description"
    assert r("Bird feeder", "Type of device that feeds birds", "") == "description"
    assert r("Product liability", "Area of law", "") == "description"
    # plural use in the lead sentence
    assert r("Meme", "ok", "Memes are cultural items that spread.") == "plural"
    # lowercase mid-sentence use of a sentence-case title
    assert r("Operational risk", "ok", "Operational risk is the risk of loss. Firms manage operational risk.") == "sentence-case"
    assert r("Field guide", "", "") == "sentence-case" and r("Bird feeder", "Device that feeds wild birds", "") == "sentence-case"
    assert r("Product liability (law)", "ok", "") == "sentence-case"
    # a single capitalised word with lowercase repeats in the lead
    assert r("Gardening", "ok", "Gardening is a hobby. Many people enjoy gardening.") == "lowercase"
    # proper names stay specific
    assert r("Snowy Owl", "2022 mobile app", "Snowy Owl is an app. Snowy Owl was released in 2014.") == ""
    assert r("Migratory Bird Treaty Act", "US law", "The Migratory Bird Treaty Act is a US law.") == ""
    assert r("Optic Hawk", "Scope model", "Optic Hawk is a card.") == ""
    assert r("OpenAI", "American AI company", "OpenAI is a company. OpenAI released ChatGPT.") == ""
    assert r("Kestrel", "2021 mobile app", "") == ""                     # no lead available: the description decides
    assert r("Class action", "ok", "Lawsuit. A class action begins when...") == "sentence-case"
    assert r("Lake of Swans", "2009 mobile app", "") == "" and r("Call of the Wild", "mobile app series", "") == ""
    assert r("Bird Song 2", "2004 mobile app", "") == "" and r("Kingfisher (app)", "ok", "") == ""
    assert r("Barack Obama", "44th US president", "Barack Obama served. Barack Obama won.") == ""

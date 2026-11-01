"""Level-1 categories and the Wikidata class mapping (no network)."""
import copy

import pytest

from nicheclock.catalog.cache import Cache
from nicheclock.catalog.categories import (Classifier, category_ids, category_label, check_symmetry, load_categories,
                                           load_map)

# What trends.google.com/trending showed on 2026-11-01 (see data/categories.json).
GOOGLE_LIST = ["Autos and Vehicles", "Beauty and Fashion", "Business and Finance", "Climate", "Entertainment",
               "Food and Drink", "Games", "Health", "Hobbies and Leisure", "Jobs and Education",
               "Law and Government", "Other", "Pets and Animals", "Politics", "Science", "Shopping", "Sports",
               "Technology", "Travel and Transportation"]


def rec(qid, label="", **claims):
    return {"id": qid, "label": label or qid, "description": "", "aliases": [], "sitelinks": 10, "titles": {},
            "claims": {k: v for k, v in claims.items()}}


class FakeWD:
    def __init__(self, *recs):
        self.cache = Cache(":memory:")
        self.recs = {r["id"]: r for r in recs}

    def entities(self, ids):
        return {i: self.recs.get(i) for i in ids}


def test_level1_list_is_googles_nineteen_categories():
    labels = [c["label"] for c in load_categories()["categories"]]
    assert sorted(labels) == sorted(GOOGLE_LIST) and len(labels) == 19
    assert load_categories()["source"]["verified_on"] == "2026-11-01"
    assert category_label("food-drink") == "Food and Drink" and len(set(category_ids())) == 19


def test_mapping_is_consistent_and_symmetric():
    m = load_map()
    ids = set(category_ids())
    assert check_symmetry() == []
    assert set(m["class_rules"]) | set(m["occupation_rules"]) <= set(m["priority"])  # every category can win a tie
    for section in ("class_rules", "occupation_rules"):
        assert set(m[section]) <= ids
    assert set(m["priority"]) <= ids and "other" not in m["priority"]
    # a class listed twice in class_rules would make the first one silently win
    seen = [it["class"] for items in m["class_rules"].values() for it in items]
    assert len(seen) == len(set(seen))
    assert all(q.startswith("Q") and q[1:].isdigit() for q in seen)


def test_symmetry_check_flags_an_uneven_branch():
    m = copy.deepcopy(load_map())
    m["class_rules"]["food-drink"] += [{"class": f"Q{9000 + i}", "label": "x"} for i in range(40)]
    assert any("food-drink" in p for p in check_symmetry(m))
    m2 = copy.deepcopy(load_map())
    m2["class_rules"]["shopping"] = m2["class_rules"]["shopping"][:1]
    assert any("shopping" in p and "min_rules" in p for p in check_symmetry(m2))
    m3 = copy.deepcopy(load_map())
    m3["class_rules"]["made-up"] = [{"class": "Q1", "label": "x"}]
    assert any("unknown category" in p for p in check_symmetry(m3))


def test_class_walk_picks_the_nearest_rule():
    # Q_FILM --P279--> Q11424 (film, entertainment) --P279--> Q838948 (work of art)
    wd = FakeWD(rec("QX", P31=["QFILM"]), rec("QFILM", P279=["Q11424"]), rec("Q11424", P279=["Q838948"]), rec("Q838948"))
    c = Classifier(wd).classify("QX")
    assert (c.category, c.kind, c.via, c.depth) == ("entertainment", "class", "Q11424", 1)


def test_class_like_item_uses_its_own_subclass_of():
    # espresso has no "instance of", only "subclass of": coffee drink -> drink
    wd = FakeWD(rec("QE", P279=["QCOFFEE"]), rec("QCOFFEE", P279=["Q40050"]), rec("Q40050"))
    assert Classifier(wd).classify("QE").category == "food-drink"


def test_sport_statement_beats_other_classes():
    wd = FakeWD(rec("QF1", P31=["Q786820"], P641=["Q349"]), rec("Q786820"))
    c = Classifier(wd).classify("QF1")
    assert (c.category, c.kind) == ("sports", "property")


def test_film_or_character_about_a_sport_stays_entertainment_but_a_match_is_sports():
    wd = FakeWD(rec("QFILM1", P31=["Q11424"], P641=["Q349"]), rec("Q11424"),
                rec("QCHAR", P31=["QFICT"], P641=["QFENC"]), rec("QFICT", P279=["Q95074"]), rec("Q95074"),
                rec("QMATCH", P31=["QMATCHCLS"], P641=["Q349"]), rec("QMATCHCLS"))
    cl = Classifier(wd)
    assert cl.classify("QFILM1").category == "entertainment" and cl.classify("QCHAR").category == "entertainment"
    assert cl.classify("QMATCH").category == "sports"


def test_ties_go_to_the_specific_category_not_the_generic_one():
    # both rules at distance 0: automobile manufacturer (autos) vs business: autos is earlier in priority
    wd = FakeWD(rec("QCO", P31=["Q4830453", "Q786820"]), rec("Q4830453"), rec("Q786820"))
    assert Classifier(wd).classify("QCO").category == "autos-vehicles"


def test_human_takes_the_category_most_occupations_vote_for():
    wd = FakeWD(rec("QH", P31=["Q5"], P106=["Q43845", "Q131524", "Q82955"]),
                rec("Q43845"), rec("Q131524"), rec("Q82955"))
    c = Classifier(wd).classify("QH")
    assert c.category == "business-finance" and c.kind == "occupation"


def test_a_sport_statement_is_one_vote_for_a_human_not_the_verdict():
    actor = FakeWD(rec("QH", P31=["Q5"], P641=["Q349"], P106=["Q33999", "Q639669", "Q36180"]),
                   rec("Q33999"), rec("Q639669"), rec("Q36180"))
    assert Classifier(actor).classify("QH").category == "entertainment"
    boxer_only = FakeWD(rec("QB", P31=["Q5"], P641=["Q349"]))
    assert Classifier(boxer_only).classify("QB").category == "sports"


def test_very_general_classes_only_count_when_close():
    # "scientist" is capped at 2 steps: a theologian five classes below it is not a scientist
    chain = [rec("QOCC", P279=["QA"]), rec("QA", P279=["QB"]), rec("QB", P279=["QC"]), rec("QC", P279=["Q901"]), rec("Q901")]
    wd = FakeWD(rec("QH", P31=["Q5"], P106=["QOCC"]), *chain)
    assert Classifier(wd).classify("QH").category == "other"
    near = FakeWD(rec("QH", P31=["Q5"], P106=["QOCC"]), rec("QOCC", P279=["Q901"]), rec("Q901"))
    assert Classifier(near).classify("QH").category == "science"


def test_human_occupation_reaches_athlete_through_subclass():
    wd = FakeWD(rec("QH", P31=["Q5"], P106=["Q3665646"]), rec("Q3665646", P279=["Q2066131"]), rec("Q2066131"))
    assert Classifier(wd).classify("QH").category == "sports"


def test_taxon_is_animal_when_its_lineage_reaches_animalia_else_science():
    wd = FakeWD(rec("QBIRD", P31=["Q16521"], P171=["QGENUS"]), rec("QGENUS", P171=["QCLASS"]),
                rec("QCLASS", P171=["Q729"]), rec("Q729"),
                rec("QFERN", P31=["Q16521"], P171=["QPLANTS"]), rec("QPLANTS", P171=["Q19088"]), rec("Q19088"))
    cl = Classifier(wd)
    assert cl.classify("QBIRD").category == "pets-animals" and cl.classify("QFERN").category == "science"


def test_unknown_and_unmatched_fall_back_to_other():
    wd = FakeWD(rec("QX", P31=["QNOTHING"]), rec("QNOTHING"))
    cl = Classifier(wd)
    assert cl.classify("QX").category == "other" and cl.classify("QMISSING").category == "other"


def test_classify_many_batches_and_returns_every_id():
    wd = FakeWD(rec("QA", P31=["Q7889"]), rec("QB", P31=["Q2095"]), rec("Q7889"), rec("Q2095"))
    out = Classifier(wd).classify_many(["QA", "QB", "QA"])
    assert {k: v.category for k, v in out.items()} == {"QA": "games", "QB": "food-drink"}


@pytest.mark.parametrize("slugs,cat", [(["food-and-drug-administration"], "health"),
                                       (["environmental-protection-agency", "x"], "climate"),
                                       (["unknown-agency"], "law-government"), ([], "law-government")])
def test_federal_register_agency_mapping(slugs, cat):
    assert Classifier(FakeWD()).classify_agency(slugs) == cat

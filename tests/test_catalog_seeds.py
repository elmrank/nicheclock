"""Category-seeded pool: seed classes, members, category-relative floors (no network)."""
from nicheclock.catalog import seeds
from nicheclock.catalog.categories import category_ids
from nicheclock.catalog.trending import apply_category_floors


def test_seed_classes_cover_every_category_but_other_and_are_symmetric():
    cfg = seeds.load()
    assert seeds.check_seed_symmetry() == []
    assert set(cfg["classes"]) == set(category_ids()) - {"other"}
    assert all(5 <= len(v) <= 6 for v in cfg["classes"].values())


def test_symmetry_check_flags_uneven_missing_and_shared_seed_classes():
    cfg = {"min_classes": 5, "max_classes": 6, "classes": {"games": ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"], "health": ["Q1", "Q2", "Q3", "Q4", "Q8"],
                                                           "made-up": ["bad", "Q9", "Q10", "Q11", "Q12"]}}
    p = " | ".join(seeds.check_seed_symmetry(cfg))
    assert "games has 7" in p and "Q1 is in both games and health" in p and "unknown category 'made-up'" in p
    assert "bad id" in p and "autos-vehicles has none" in p


class FakeWD:
    def __init__(self):
        self.queries = []

    def sparql(self, q):
        self.queries.append(q)
        cls = q.split("wd:")[1].split()[0]
        return {"Q39367": [{"item": "http://www.wikidata.org/entity/Q100", "sl": "90"}, {"item": "http://www.wikidata.org/entity/Q101", "sl": "40"}],
                "Q43577": [{"item": "http://www.wikidata.org/entity/Q100", "sl": "95"}, {"item": "http://www.wikidata.org/entity/Q102", "sl": "30"}]}.get(cls, [])


def test_members_merge_a_category_keep_most_linked_and_never_assign_an_item_twice():
    cfg = {"classes": {"pets-animals": ["Q39367", "Q43577"], "food-drink": ["Q39367"]}, "per_class_limit": 50, "min_sitelinks": 12, "per_category_pool": 2}
    wd = FakeWD()
    out = seeds.members(wd, log=lambda *_: None, cfg=cfg)
    assert set(out) == {"Q100", "Q102"} or set(out) == {"Q100", "Q101"}
    assert out["Q100"] == {"category": "pets-animals", "class": "Q43577", "sitelinks": 95}       # max sitelinks, and its class
    assert len([q for q in out.values() if q["category"] == "food-drink"]) == 0                  # Q100/Q101 already taken
    assert "wdt:P31 wd:Q39367" in wd.queries[0] and "FILTER(?sl >= 12)" in wd.queries[0] and "LIMIT 50" in wd.queries[0]


def test_percentile_and_category_floors_are_relative_clamped_and_need_enough_members():
    assert seeds.percentile([1, 2, 3, 4], 75) == 3.25 and seeds.percentile([7], 75) == 7
    vols = {"pets-animals": [float(v) for v in range(100, 2100, 50)],            # 40 members, p75 = 1600ish
            "food-drink": [float(v) for v in range(1000, 41000, 1000)],           # p75 far above the cap
            "hobbies-leisure": [10.0] * 30,                                       # tiny audience: the minimum applies
            "shopping": [900.0] * 5}                                              # too few members
    f = seeds.category_floors(vols, cap=5000.0)
    assert 1500 < f["pets-animals"] < 1700 and f["food-drink"] == 5000.0
    assert f["hobbies-leisure"] == seeds.MIN_CATEGORY_FLOOR and f["shopping"] == seeds.MIN_CATEGORY_FLOOR


def test_apply_category_floors_sets_an_override_on_seeded_candidates_only():
    cands = {f"Q{i}": {"seed": "pets-animals", "wiki_series": {"en": [300 + i] * 28}} for i in range(30)}
    cands["Q999"] = {"wiki_series": {"en": [100000] * 28}}
    fl = apply_category_floors(cands, {})
    assert 300 <= fl["pets-animals"] <= 330 and cands["Q5"]["floor_override"] == fl["pets-animals"]
    assert "floor_override" not in cands["Q999"]

import json
import re
from pathlib import Path

import pytest

import nicheclock
from nicheclock import i18n

ROOT = Path(nicheclock.__file__).parent
EN = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))
# t("key"...) / _t("key"...) / tr("key"...) with a literal key
CALL = re.compile(r"""\b(?:t|_t|tr)\(\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']""")
# keys built at run time: prefix -> where they come from
DYNAMIC = ("out.cite.", "doctor.region_support.", "notify.kind.", "region.precision.", "doctor.mark.")


def used_keys() -> set[str]:
    keys: set[str] = set()
    for p in ROOT.rglob("*.py"):
        keys |= set(CALL.findall(p.read_text(encoding="utf-8")))
    return keys


def test_every_key_used_in_code_exists():
    missing = sorted(used_keys() - set(EN))
    assert not missing, f"keys used in code but not in en.json: {missing}"


def test_no_dead_strings():
    dead = sorted(k for k in set(EN) - used_keys() if not k.startswith(DYNAMIC))
    assert not dead, f"keys in en.json that no code uses: {dead}"


def test_t_formats_and_falls_back_to_english():
    assert i18n.t("region.approx", region="VN") == "VN (approx.)"
    assert i18n.t("out.no_signals") == EN["out.no_signals"]
    i18n.set_language("xx")                      # no such catalog: English is used
    try:
        assert i18n.t("out.upcoming") == "Upcoming"
    finally:
        i18n.set_language("en")
    with pytest.raises(KeyError):
        i18n.t("no.such.key")


def test_placeholders_are_plain_identifiers():
    # every {placeholder} is a plain identifier (a typo like {0} or {a b} would break str.format)
    unformatted = {"onboard.help.edit.add_notify"}      # shown as-is (no kwargs), so its braces are literal
    for key, text in EN.items():
        if key in unformatted:
            continue
        for ph in re.findall(r"\{([^}]*)\}", text.replace("{{", "").replace("}}", "")):
            assert re.fullmatch(r"[a-z_]+(![rs])?(:[.0-9a-z]+)?", ph), (key, ph)

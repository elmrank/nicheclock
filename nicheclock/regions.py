"""Regions: which countries a project/niche is read for, and how signals carry that.

A region is an ISO 3166-1 alpha-2 country code (``US``, ``GB``, ``VN``) or ``global``.
``region_precision`` says how much the tag can be trusted:

* ``exact``  the source itself is per country (Google Trends RSS geo, Apple country charts, a
  government feed of one country);
* ``approx`` the source is selected by something close to a country (Wikipedia language edition,
  GDELT ``sourcecountry`` = where the publisher is, not whom the story is about);
* ``global`` the source has no region at all (Hacker News, GitHub, your own feeds).

Every source module declares ``REGION_SUPPORT`` = ``country`` | ``language`` | ``source_country`` |
``global`` so docs and `doctor` can say what each source does with a region."""

from __future__ import annotations

import re
from typing import Any, Iterable

GLOBAL = "global"
MULTI = "multi"              # a combined topic seen in more than one country
MODES = ("per_country", "combined")
DEFAULT_MODE = "combined"
PRECISIONS = ("exact", "approx", "global")
SUPPORT_KINDS = ("country", "language", "source_country", "global")
_RANK = {"global": 0, "approx": 1, "exact": 2}
_CODE = re.compile(r"^[A-Z]{2}$")

# Country -> main Wikipedia language edition (approximation: a language is not a country).
COUNTRY_LANGUAGE = {
    "US": "en", "GB": "en", "CA": "en", "AU": "en", "NZ": "en", "IE": "en", "IN": "en", "SG": "en",
    "PH": "en", "ZA": "en", "VN": "vi", "DE": "de", "AT": "de", "CH": "de", "FR": "fr", "BE": "fr",
    "ES": "es", "MX": "es", "AR": "es", "CO": "es", "CL": "es", "PE": "es", "IT": "it", "PT": "pt",
    "BR": "pt", "NL": "nl", "PL": "pl", "RU": "ru", "UA": "uk", "TR": "tr", "JP": "ja", "KR": "ko",
    "CN": "zh", "TW": "zh", "ID": "id", "TH": "th", "SE": "sv", "NO": "no", "DK": "da", "FI": "fi",
    "CZ": "cs", "GR": "el", "HU": "hu", "RO": "ro", "IL": "he", "SA": "ar", "EG": "ar", "AE": "ar",
}
# ISO 3166 alpha-2 -> FIPS 10-4 for the GDELT `sourcecountry:` operator (where they differ or to
# document the supported set). Countries not listed are not queried (planned).
COUNTRY_FIPS = {
    "US": "US", "GB": "UK", "CA": "CA", "AU": "AS", "NZ": "NZ", "IE": "EI", "IN": "IN", "SG": "SN",
    "PH": "RP", "ZA": "SF", "VN": "VM", "DE": "GM", "AT": "AU", "CH": "SZ", "FR": "FR", "BE": "BE",
    "ES": "SP", "MX": "MX", "AR": "AR", "CO": "CO", "CL": "CI", "PE": "PE", "IT": "IT", "PT": "PO",
    "BR": "BR", "NL": "NL", "PL": "PL", "RU": "RS", "UA": "UP", "TR": "TU", "JP": "JA", "KR": "KS",
    "CN": "CH", "TW": "TW", "ID": "ID", "TH": "TH", "SE": "SW", "NO": "NO", "DK": "DA", "FI": "FI",
}


def normalize(regions: Iterable[str] | str | None) -> list[str]:
    """['us', 'GB'] -> ['US', 'GB']; None / empty -> ['global']; order kept, duplicates dropped."""
    if regions is None:
        return [GLOBAL]
    if isinstance(regions, str):
        regions = [regions]
    out: list[str] = []
    for r in regions:
        r = str(r).strip()
        r = GLOBAL if r.lower() == GLOBAL else r.upper()
        if r and r not in out:
            out.append(r)
    return out or [GLOBAL]


def invalid(regions: Iterable[str]) -> list[str]:
    """Entries that are neither 'global' nor an ISO-style two-letter code."""
    return [r for r in normalize(list(regions)) if r != GLOBAL and not _CODE.match(r)]


def countries(regions: Iterable[str]) -> list[str]:
    return [r for r in normalize(list(regions)) if r != GLOBAL]


def effective(project: Any, niche: Any) -> tuple[list[str], str]:
    """(regions, mode) for a niche: the niche's own setting, else its project's, else global/combined."""
    regions = getattr(niche, "regions", None) or getattr(project, "regions", None) or [GLOBAL]
    mode = getattr(niche, "region_mode", None) or getattr(project, "region_mode", None) or DEFAULT_MODE
    return normalize(regions), mode


def label(region: str | None, precision: str | None = None) -> str:
    """Short text for a region badge, e.g. 'US', 'VN (approx.)', 'global'."""
    from nicheclock.i18n import t
    region = region or GLOBAL
    if region == GLOBAL:
        return t("region.global")
    if region == MULTI:
        return t("region.multi")
    if precision == "approx":
        return t("region.approx", region=region)
    return region


def keeps(item_region: str | None, wanted: str | None, item_regions: Iterable[str] | None = None) -> bool:
    """Region filter: ``wanted`` keeps items of that country plus global ones (they apply
    everywhere). A 'multi' item (seen in several countries) is kept when ``wanted`` is among its
    ``item_regions``."""
    if not wanted or wanted.lower() == GLOBAL:
        return True
    wanted = wanted.upper()
    r = item_region or GLOBAL
    if r == MULTI:
        return wanted in (item_regions or [])
    return r in (wanted, GLOBAL)


def combine(signals: Iterable[Any]) -> tuple[str, str, list[str]]:
    """(region, precision, regions) of a group of signals. Global signals do not dilute a country:
    one country among the region-bearing signals -> that country, several -> 'multi', none -> 'global'.
    Precision is the best precision among the region-bearing signals."""
    codes: list[str] = []
    best = "global"
    for s in signals:
        r = getattr(s, "region", GLOBAL) or GLOBAL
        if r == GLOBAL:
            continue
        if r not in codes:
            codes.append(r)
        p = getattr(s, "region_precision", "exact") or "exact"
        if _RANK.get(p, 0) > _RANK[best]:
            best = p
    codes.sort()
    if not codes:
        return GLOBAL, "global", []
    return (codes[0] if len(codes) == 1 else MULTI), best, codes

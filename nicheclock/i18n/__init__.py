"""User-facing strings live in ``en.json``; code asks for them by key with ``t(key, **kw)``.

English only for now. A translation is another ``<lang>.json`` with the same keys; ``set_language``
picks it, and a missing key falls back to English. Log lines and technical identifiers stay in
code on purpose."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent
DEFAULT_LANGUAGE = "en"
_language = DEFAULT_LANGUAGE


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    p = _DIR / f"{lang}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def set_language(lang: str) -> None:
    global _language
    _language = lang


def t(key: str, /, **kw: Any) -> str:
    """The string for ``key`` with ``{placeholders}`` filled from ``kw``."""
    s = catalog(_language).get(key)
    if s is None:
        s = catalog(DEFAULT_LANGUAGE)[key]      # KeyError on a typo: tests cover every key in use
    return s.format(**kw) if kw else s

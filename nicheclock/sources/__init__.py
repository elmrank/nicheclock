"""Data sources. Every module has NAME, TERMS_URL, TERMS_NOTE and
``collect(client, niche, day) -> list[Signal]``; dated sources also have
``collect_events(client, niche, day) -> list[Event]``.

Only sources whose terms allow automated use ship here (see ARCHITECTURE.md "Hard rules")."""

from __future__ import annotations

import importlib
from types import ModuleType

SOURCE_NAMES = (
    "google_trends", "wikipedia", "hn", "github", "apple", "gdelt", "federal_register", "rss",
)
EVENT_SOURCE_NAMES = ("federal_register", "rss", "ics")   # modules exposing collect_events
HELPER_NAMES = ("mediawiki",)                              # facts helper, no collect()


def load(name: str) -> ModuleType:
    if name not in SOURCE_NAMES + EVENT_SOURCE_NAMES + HELPER_NAMES:
        raise KeyError(f"unknown source {name!r}")
    return importlib.import_module(f"nicheclock.sources.{name}")


def attribution() -> dict[str, tuple[str, str]]:
    """{name: (TERMS_URL, TERMS_NOTE)} for every shipped source (shown in feed footers)."""
    out = {}
    for n in dict.fromkeys(SOURCE_NAMES + EVENT_SOURCE_NAMES + HELPER_NAMES):
        m = load(n)
        out[m.NAME] = (m.TERMS_URL, m.TERMS_NOTE)
    return out

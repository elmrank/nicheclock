"""Regions of the catalog: Worldwide plus the ten most populous countries (UN WPP 2024, 2026)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    return json.loads((DATA / "regions.json").read_text(encoding="utf-8"))


def regions() -> list[dict[str, Any]]:
    return load()["regions"]


def region_ids() -> list[str]:
    return [r["id"] for r in regions()]


def trending_file(region_id: str) -> str:
    """``trending.json`` for Worldwide, ``trending-IN.json`` for a country."""
    return "trending.json" if region_id == "global" else f"trending-{region_id}.json"


@lru_cache(maxsize=1)
def ledger() -> list[dict[str, Any]]:
    return json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]

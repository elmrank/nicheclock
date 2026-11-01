"""Wires an identity, the shared HTTP client and the cache for catalog commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nicheclock.catalog.cache import Cache
from nicheclock.config import Identity, load_config
from nicheclock.http import Client


@dataclass
class Runtime:
    client: Any
    cache: Cache
    state_dir: Path


def identity_from(config: str | None = None) -> Identity:
    """``--config nicheclock.yaml`` identity, else NICHECLOCK_USER_AGENT + NICHECLOCK_CONTACT_EMAIL."""
    if config:
        return load_config(config).identity
    return Identity(os.environ.get("NICHECLOCK_USER_AGENT", ""), os.environ.get("NICHECLOCK_CONTACT_EMAIL", ""))


def make_runtime(identity: Identity, state_dir: str | Path, min_interval: float = 0.15) -> Runtime:
    """One client for the whole run: >= ``min_interval`` seconds between requests to one host."""
    class _Cfg:                       # Client only reads ``.identity``
        pass
    cfg = _Cfg()
    cfg.identity = identity           # type: ignore[attr-defined]
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    return Runtime(Client(cfg, min_interval=min_interval), Cache(state / "catalog-cache.sqlite"), state)

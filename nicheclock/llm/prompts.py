"""Versioned prompt files: ``prompts/<task>.md`` starts with ``---\\nversion: N\\n---``."""
from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import NamedTuple

_HEAD = re.compile(r"\A---\s*\nversion:\s*(\d+)\s*\n---\s*\n", re.S)


class Prompt(NamedTuple):
    name: str
    version: int
    text: str


@lru_cache(maxsize=None)
def load(name: str) -> Prompt:
    raw = (resources.files("nicheclock.llm") / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    m = _HEAD.match(raw)
    if not m:
        raise ValueError(f"prompt {name}: missing '---\\nversion: N\\n---' header")
    return Prompt(name, int(m.group(1)), raw[m.end():].strip())

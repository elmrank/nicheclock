"""Helpers to read docs/*.md: front matter and fenced code blocks.

    python tests/docs_blocks.py docs/docker.md caddyfile      # print every block whose language or extra tag is caddyfile

A fence's first word is its language; extra words are tags the tests use (```yaml niche = a fragment of a niche).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
FENCE = re.compile(r"^(\s*)```(\S*)[ \t]*(.*)$")


@dataclass
class Block:
    lang: str
    tags: list[str]
    text: str
    line: int


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return {}, text
    meta = {}
    for ln in head.splitlines():
        k, _, v = ln.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body


def blocks(text: str) -> list[Block]:
    out: list[Block] = []
    cur: list[str] | None = None
    start, lang, tags, indent = 0, "", [], 0
    for n, ln in enumerate(text.splitlines(), 1):
        m = FENCE.match(ln)
        if cur is None and m:
            cur, start, lang, tags, indent = [], n, m.group(2), m.group(3).split(), len(m.group(1))
        elif cur is not None and ln.lstrip().startswith("```"):
            out.append(Block(lang, tags, "\n".join(cur) + "\n", start))
            cur = None
        elif cur is not None:
            cur.append(ln[indent:] if ln[:indent].strip() == "" else ln)
    return out


def published() -> list[Path]:
    return sorted(p for p in DOCS.glob("*.md") if split_front_matter(p.read_text(encoding="utf-8"))[0].get("publish") == "true")


if __name__ == "__main__":
    for b in blocks(Path(sys.argv[1]).read_text(encoding="utf-8")):
        if sys.argv[2] == b.lang or sys.argv[2] in b.tags:
            print(b.text)

"""Task ``profile`` (onboarding): propose niches from at most 50 page titles and slugs."""
from __future__ import annotations

from typing import Any, Sequence

from nicheclock.llm import prompts
from nicheclock.llm.session import LLMSession
from nicheclock.llm.types import messages_of
from nicheclock.llm.tasks.common import dumps

TASK = "profile"
MAX_PAGES = 50
MAX_NICHES = 4
_STR = {"type": "string", "minLength": 1, "maxLength": 80}
SCHEMA: dict[str, Any] = {
    "type": "object", "required": ["niches"],
    "properties": {"niches": {"type": "array", "minItems": 1, "maxItems": MAX_NICHES, "items": {
        "type": "object", "required": ["name", "keywords"],
        "properties": {"name": _STR,
                       "keywords": {"type": "array", "minItems": 1, "maxItems": 12, "items": _STR},
                       "formats": {"type": "array", "maxItems": 8, "items": _STR},
                       "exclude": {"type": "array", "maxItems": 8, "items": _STR}}}}},
}


def propose(session: LLMSession, site: dict[str, str], pages: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """Niches ``{name, keywords, formats, exclude}`` from ``pages`` = [(title, slug)] (first 50 used).
    ``site`` holds host and, when known, title/description. Nothing else leaves the machine."""
    prompt = prompts.load(TASK)
    user = dumps({"site": site, "pages": [{"title": t, "slug": s} for t, s in list(pages)[:MAX_PAGES]]})
    res = session.call(TASK, messages_of(prompt.text, user), SCHEMA, max_tokens=1500)
    out = []
    for n in res.json["niches"]:
        out.append({"name": " ".join(n["name"].split()), "keywords": _clean(n["keywords"]),
                    "formats": _clean(n.get("formats") or []), "exclude": _clean(n.get("exclude") or [])})
    return out


def _clean(xs: Sequence[str]) -> list[str]:
    seen: dict[str, str] = {}
    for x in xs:
        v = " ".join(str(x).lower().split())
        if v:
            seen.setdefault(v, v)
    return list(seen)

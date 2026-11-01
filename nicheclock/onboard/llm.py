"""`init --llm`: niches proposed by YOUR model from page titles and slugs, merged with the TF-IDF result.

Never a default: it runs only when the user passes --llm AND has an `llm:` section (see
``nicheclock.llm``). At most 50 page titles with their URL slugs and the site host leave the machine;
no page text. The model's niches are merged into the rule-based drafts (same topic: the model's name and
extra keywords/formats/exclusions are added; new topic: a new niche), and the user still edits the YAML.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence
from urllib.parse import urlsplit

from nicheclock.llm.tasks.profile import MAX_PAGES

from .niches import SOURCES_BY_KIND, UPCOMING_KINDS, NicheDraft

MAX_KEYWORDS = 14


def pages_for_prompt(crawls: Sequence[Any], limit: int = MAX_PAGES) -> list[tuple[str, str]]:
    """``(title, slug)`` for up to ``limit`` fetched pages (home page first)."""
    out: list[tuple[str, str]] = []
    for c in crawls:
        titles = c.titles()
        for page in c.pages:
            title = titles.get(page.url, (page.title, page.h1))[0] or page.h1 or page.title
            slug = urlsplit(page.url).path.strip("/")
            if title or slug:
                out.append((" ".join(title.split())[:140], slug[:120]))
    return out[:limit]


def site_info(crawls: Sequence[Any]) -> dict[str, str]:
    c = crawls[0]
    info = {"host": c.host}
    prof = site_profile(crawls)
    info.update(prof)
    return info


def site_profile(crawls: Sequence[Any]) -> dict[str, str]:
    """``{title, description}`` of the site for the config's ``profile:`` (empty values left out)."""
    c = crawls[0]
    home = next((p for p in c.pages if urlsplit(p.url).path in ("", "/")), None)
    title = (c.site_names[0] if c.site_names else "") or (home.title if home else "")
    prof = {"title": " ".join(title.split())[:120], "description": c.description}
    return {k: v for k, v in prof.items() if v}


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _union(base: Sequence[str], extra: Sequence[str], cap: int = 99) -> list[str]:
    seen = {_norm(x) for x in base}
    out = list(base)
    for x in extra:
        if _norm(x) not in seen and len(out) < cap:
            seen.add(_norm(x))
            out.append(x)
    return out


def merge_proposals(drafts: Sequence[NicheDraft], proposals: Sequence[dict[str, Any]],
                    max_niches: int = 4) -> list[NicheDraft]:
    """Fold the model's niches into the rule-based drafts: a proposal that shares a keyword/entity with
    a draft enriches it, any other becomes a new niche (kept within ``max_niches`` overall)."""
    out = list(drafts)
    names = {_norm(d.name) for d in out}
    new: list[NicheDraft] = []
    for p in proposals:
        kws = [_norm(k) for k in p["keywords"]]
        best, best_n = None, 0
        for i, d in enumerate(out):
            have = {_norm(x) for x in d.keywords + d.entities + d.phrases}
            n = len(have & set(kws))
            if n > best_n:
                best, best_n = i, n
        if best is not None:
            d = out[best]
            exclude = _union(d.exclude, [e for e in p["exclude"] if _norm(e) not in {_norm(k) for k in d.keywords + kws}])
            name = p["name"] if _norm(p["name"]) not in names - {_norm(d.name)} else d.name
            names.discard(_norm(d.name))
            names.add(_norm(name))
            out[best] = replace(d, name=name, keywords=_union(d.keywords, kws, MAX_KEYWORDS),
                                formats=_union(d.formats, p["formats"]), exclude=exclude)
        elif _norm(p["name"]) not in names:
            names.add(_norm(p["name"]))
            new.append(NicheDraft(
                name=p["name"], keywords=kws[:MAX_KEYWORDS], entities=[], formats=list(p["formats"]),
                exclude=[e for e in p["exclude"] if _norm(e) not in set(kws)], kind="default",
                sources=list(SOURCES_BY_KIND["default"]), upcoming_kinds=list(UPCOMING_KINDS["default"]),
                phrases=list(kws), section="proposed by your model"))
    room = max(0, max_niches - len(out))
    return out + new[:room]

"""Render feed items into notification text (plain, Markdown, HTML).

Items are duck-typed ``FeedItem`` objects (project, niche, kind, title, url,
why, score, at), so this module has no hard dependency on ``models.py``.
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any, Iterable

from nicheclock.i18n import t
from nicheclock.regions import label

DEFAULT_TOP_N = 5
MAX_TITLE = 200
MAX_WHY = 300
MAX_URL = 500

KIND_KEYS = {"topic": "notify.kind.topic", "event": "notify.kind.event", "gap": "notify.kind.gap"}


def kind_label(kind: str) -> str:
    return t(KIND_KEYS[kind]) if kind in KIND_KEYS else str(kind).upper()


def region_tag(it: Any) -> str:
    """' [US]' for an item tied to a country (' [VN (approx.)]' when only approximately), else ''."""
    region = getattr(it, "region", None)
    if not region or region == "global":
        return ""
    return f" [{label(region, getattr(it, 'region_precision', None))}]"


def _clip(text: Any, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def group_top(items: Iterable[Any], top_n: int = DEFAULT_TOP_N) -> dict[str, list[Any]]:
    """Top ``top_n`` items per niche by score (desc); niches ordered by best score."""
    by_niche: dict[str, list[Any]] = defaultdict(list)
    for it in items:
        by_niche[it.niche or t("notify.no_niche")].append(it)
    for lst in by_niche.values():
        lst.sort(key=lambda i: (-(i.score or 0.0), str(i.title)))
        del lst[max(top_n, 0):]
    order = sorted(by_niche, key=lambda n: (-(by_niche[n][0].score or 0.0), n))
    return {n: by_niche[n] for n in order if by_niche[n]}


def _plain_item(idx: int, it: Any) -> str:
    lines = [f"{idx}. [{kind_label(it.kind)}]{region_tag(it)} {_clip(it.title, MAX_TITLE)}"]
    if it.why:
        lines.append("   " + t("notify.why", why=_clip(it.why, MAX_WHY)))
    if it.url and len(it.url) <= MAX_URL:
        lines.append(f"   {it.url}")
    return "\n".join(lines)


def _md_item(idx: int, it: Any) -> str:
    title = _clip(it.title, MAX_TITLE)
    # Discord-flavoured Markdown: escape link-text brackets.
    title = title.replace("[", "\\[").replace("]", "\\]")
    head = f"{idx}. **{kind_label(it.kind)}**{region_tag(it)} "
    if it.url and len(it.url) <= MAX_URL and ")" not in it.url:
        head += f"[{title}]({it.url})"
    else:
        head += title + (f" <{it.url}>" if it.url and len(it.url) <= MAX_URL else "")
    lines = [head]
    if it.why:
        lines.append("   " + t("notify.why", why=_clip(it.why, MAX_WHY)))
    return "\n".join(lines)


def _html_item(idx: int, it: Any) -> str:
    title = html.escape(_clip(it.title, MAX_TITLE), quote=False)
    if it.url and len(it.url) <= MAX_URL:
        title = f'<a href="{html.escape(it.url, quote=True)}">{title}</a>'
    lines = [f"{idx}. <b>{kind_label(it.kind)}</b>{html.escape(region_tag(it), quote=False)} {title}"]
    if it.why:
        lines.append("   " + t("notify.why", why=html.escape(_clip(it.why, MAX_WHY), quote=False)))
    return "\n".join(lines)


def _title(project: str, day: str) -> str:
    return " · ".join(p for p in ("nicheclock", project, day) if p)


def _render(
    project: str,
    items: Iterable[Any],
    top_n: int,
    day: str,
    *,
    header,
    niche,
    item,
    limit: int,
) -> list[str]:
    groups = group_top(items, top_n)
    if not groups:
        return []
    head = header(_title(project, day))
    chunks: list[str] = []
    cur = head
    cur_niche: str | None = None
    for name, its in groups.items():
        nhead = niche(name, len(its))
        for idx, it in enumerate(its, 1):
            piece = item(idx, it)
            need_head = cur_niche != name
            add = ("\n\n" + nhead if need_head else "") + "\n" + piece
            if len(cur) + len(add) > limit and cur_niche is not None:
                chunks.append(cur)
                cur = nhead  # repeat the niche header in the continuation
                cur_niche = name
                add = "\n" + piece
            cur += add
            cur_niche = name
    chunks.append(cur)
    return chunks


def render_text(project: str, items: Iterable[Any], top_n: int = DEFAULT_TOP_N,
                day: str = "", limit: int = 4000) -> list[str]:
    return _render(project, items, top_n, day, header=lambda t: t,
                   niche=lambda n, c: f"== {n} ({c})", item=_plain_item, limit=limit)


def render_markdown(project: str, items: Iterable[Any], top_n: int = DEFAULT_TOP_N,
                    day: str = "", limit: int = 1900) -> list[str]:
    return _render(project, items, top_n, day, header=lambda t: f"**{t}**",
                   niche=lambda n, c: f"__{n}__ ({c})", item=_md_item, limit=limit)


def render_html(project: str, items: Iterable[Any], top_n: int = DEFAULT_TOP_N,
                day: str = "", limit: int = 4000) -> list[str]:
    esc = lambda s: html.escape(s, quote=False)  # noqa: E731
    return _render(project, items, top_n, day, header=lambda t: f"<b>{esc(t)}</b>",
                   niche=lambda n, c: f"<u>{esc(n)}</u> ({c})", item=_html_item, limit=limit)

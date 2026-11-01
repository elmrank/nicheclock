"""`preview`: which items would match each niche, and which would be dropped by `exclude`.

Items come from the last N days of the local store; with an empty store (first run) a quick live pull
of Google Trends trending RSS + Wikipedia's top articles is used instead. Matching is Core's
`match.match_text`, so the preview agrees with what `run` will do.
"""
from __future__ import annotations

from nicheclock.i18n import t as _t

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from nicheclock.match import match_text


@dataclass
class Item:
    text: str
    source: str


@dataclass
class NichePreview:
    niche: str
    matched: list[tuple[Item, list[str]]] = field(default_factory=list)    # (item, hits)
    excluded: list[tuple[Item, list[str], list[str]]] = field(default_factory=list)  # (item, hits, exclude words)
    n_matched: int = 0
    n_excluded: int = 0


def dedupe(items: Iterable[Item]) -> list[Item]:
    seen: set[tuple[str, str]] = set()
    out = []
    for it in items:
        k = (it.source, it.text.strip().lower())
        if it.text.strip() and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def preview_niches(niches: Sequence[Any], items: Sequence[Item], limit: int = 10) -> list[NichePreview]:
    """Match every item against every niche (keywords/entities, then exclude)."""
    out: list[NichePreview] = []
    for n in niches:
        pv = NichePreview(niche=n.name)
        for it in items:
            m = match_text(it.text, n)
            if not m.hits:
                continue
            if m.excluded:
                pv.n_excluded += 1
                if len(pv.excluded) < limit:
                    pv.excluded.append((it, m.hits, m.excluded))
            else:
                pv.n_matched += 1
                if len(pv.matched) < limit:
                    pv.matched.append((it, m.hits))
        out.append(pv)
    return out


def items_from_store(cfg: Any, project: str, days: int = 7) -> list[Item]:
    from nicheclock.store import Store

    path = cfg.data_dir / "nicheclock.db"
    if not path.exists():
        return []
    with Store.open(cfg) as st:
        return dedupe(Item(s.term, s.source) for s in st.recent_signals(project=project, days=days))


def items_live(client: Any, day: date | None = None, geos: Sequence[str] = ("US",)) -> tuple[list[Item], list[str]]:
    """Google Trends trending RSS + Wikipedia top articles (yesterday or the day before). -> (items, warnings)."""
    from nicheclock.http import HttpError
    from nicheclock.sources import google_trends, wikipedia

    day = day or date.today()
    items: list[Item] = []
    warns: list[str] = []
    for geo in geos:
        try:
            text = client.get_text(google_trends.RSS_URL, params={"geo": geo})
            items += [Item(s.term, "google_trends") for s in google_trends.parse(text, geo)]
        except HttpError as e:
            warns.append(f"google_trends {geo}: {e}")
    for back in (1, 2, 3):
        d = day - timedelta(days=back)
        try:
            data = client.get_json(wikipedia.TOP_URL.format(lang="en", y=d.year, m=f"{d.month:02d}", d=f"{d.day:02d}"))
        except HttpError as e:
            warns.append(f"wikipedia top {d}: {e}")
            continue
        items += [Item(t, "wikipedia") for _, t, _ in wikipedia.parse_top(data)]
        break
    return dedupe(items), warns


def render_feeds(niche: Any) -> list[str]:
    """Feeds a niche will read, for the user to confirm (they are responsible for each feed's terms)."""
    rss, watch = list(niche.rss), list(getattr(niche, "watch", []))
    if not rss and not watch:
        return [_t("onboard.feeds.none")]
    lines = [_t("onboard.feeds.header")]
    lines += [f"    * {u}" for u in rss]
    lines += [_t("onboard.feeds.watched", url=u) for u in watch]
    return lines


def render(previews: Sequence[NichePreview], n_items: int, origin: str, niches: Sequence[Any] = ()) -> str:
    lines = [_t("onboard.preview.header", count=n_items, origin=origin)]
    by_name = {n.name: n for n in niches}
    for pv in previews:
        lines.append("")
        lines.append(_t("onboard.preview.niche", niche=pv.niche, matched=pv.n_matched, excluded=pv.n_excluded))
        if pv.niche in by_name:
            lines += render_feeds(by_name[pv.niche])
        if pv.matched:
            lines.append(_t("onboard.preview.would_match"))
            for it, hits in pv.matched:
                lines.append(_t("onboard.preview.match_item", text=it.text, source=it.source, hits=", ".join(hits[:3])))
        else:
            lines.append(_t("onboard.preview.nothing"))
        if pv.excluded:
            lines.append(_t("onboard.preview.would_exclude"))
            for it, hits, ex in pv.excluded:
                lines.append(_t("onboard.preview.exclude_item", text=it.text, source=it.source,
                                hits=", ".join(hits[:3]), by=", ".join(ex)))
        else:
            lines.append(_t("onboard.preview.nothing_excluded"))
    return "\n".join(lines)

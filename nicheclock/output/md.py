from __future__ import annotations

from nicheclock.i18n import t
from nicheclock.output import Feed
from nicheclock.output.common import (by_niche, by_region, citations, fit_text, llm_line, publish_by,
                                      region_text, when)


def _link(title: str, url: str | None) -> str:
    text = title.replace("[", "(").replace("]", ")")
    return f"[{text}]({url})" if url else text


def render(feed: Feed) -> str:
    show = feed.shows_regions()
    L = [f"# {t('out.title', project=feed.project)}", "",
         t("out.generated", when=f"{feed.generated_at:%Y-%m-%d %H:%M}") + ". "
         + t("out.sites", urls=", ".join(feed.urls) or "-"), ""]
    if show or feed.region:
        L += [t("out.regions_line", regions=", ".join(feed.regions), mode=feed.region_mode)
              + (" · " + t("out.region_view", region=feed.region) if feed.region else ""), ""]
    groups = by_niche(feed)
    meta = {n.name: n for n in feed.niches}
    for niche, items in groups.items():
        L += [f"## {niche}", ""]
        if meta.get(niche) and meta[niche].formats:
            L += [t("out.formats", formats=", ".join(meta[niche].formats)), ""]
        if not items:
            L += [f"_{t('out.no_signals')}_", ""]
        for region, section in by_region(items, meta[niche].region_mode if niche in meta else feed.region_mode):
            if region is not None:
                L += [f"### {region_text(region, None)}", ""]
            for it in section:
                badge = f" `{region_text(it.region, it.region_precision)}`" if show and region is None else ""
                L.append(f"- **{it.score:.0f}**{badge} {_link(it.title, it.url)}: {it.why}")
                if it.llm_fit is not None or it.llm_note:
                    L.append(f"  - {fit_text(it)}")
            L.append("")
    L += [f"## {t('out.upcoming')}", ""]
    if not feed.upcoming:
        L += [f"_{t('out.nothing_ahead')}_", ""]
    else:
        cols = [t("out.col.date"), t("out.col.publish_by")] + ([t("out.col.region")] if show else []) + [
            t("out.col.niche"), t("out.col.kind"), t("out.col.what")]
        L += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for e in feed.upcoming:
            cells = [when(e), publish_by(e)] + ([region_text(e.region, e.region_precision)] if show else []) + [
                e.niche or "", e.kind, _link(e.title, e.url)]
            L.append("| " + " | ".join(cells) + " |")
        L.append("")
    if feed.gaps:
        L += [f"## {t('out.gaps')}", ""]
        for g in feed.gaps:
            L.append(f"- {_link(g.title, g.url)}: {g.why}")
            if g.angle:
                L.append("  - " + t("out.angle", angle=g.angle))
            L += ["  - " + t("out.reader_asks", question=q) for q in g.questions or []]
        L.append("")
    L += ["---", t("out.sources", sources=", ".join(feed.sources)) if feed.sources else t("out.sources_none"), ""]
    if llm_line(feed):
        L += [llm_line(feed), ""]
    L += [c + "  " for c in citations(feed)]
    return "\n".join(L).rstrip() + "\n"

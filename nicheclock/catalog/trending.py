"""The daily "trending now" build, per region: candidates from several source families, resolved to
Wikidata QIDs, scored per entity, one row per topic with a volume and a unit per channel.

Regions: ``global`` (Worldwide) and the ten most populous countries (data/regions.json). Families: ``wiki``
(Wikimedia pageviews: per-article series of the language editions, or the per-country dataset),
``news`` (GDELT files: articles naming a place in the country), ``community`` (Hacker News, Worldwide only)
and ``video`` (YouTube mostPopular chart, per country). A topic is ``multi_source`` when at least two
families are rising for it at once. No channel is ever turned into a "search volume", and a source with no
data for a region is absent from that region's rows (never 0).
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from nicheclock.catalog import countrypv
from nicheclock.catalog import feeds as feedmod
from nicheclock.catalog import github as ghmod
from nicheclock.catalog import hn as hnmod
from nicheclock.catalog import news as newsmod
from nicheclock.catalog import pageviews as pv
from nicheclock.catalog import seeds as seedmod
from nicheclock.catalog import youtube as ytmod
from nicheclock.catalog.categories import Classification, Classifier, category_label
from nicheclock.catalog.resolver import NOT_TOPIC, Resolution, Resolver
from nicheclock.catalog.scoring import (ACTIVE_RATIO, BASELINE_FRACTION, FLOORS, peak_info, OTD_FLOOR_FACTOR, SCORE_SCALE, WIKI_FLOORS, WINDOWS,
                                        channel_windows, rank_score, score_topic, seasonality, trend_of, window_stats)
from nicheclock.catalog.wd import Wikidata

SERIES_SHOWN = 14
PAGEVIEW_DAYS = 28
COUNTRY_DAYS = 22
POOL_TOP = 600                     # titles read from each edition's top list: the pool the categories are filled from
SERIES_TOP = {"en": 500}           # per-article series are fetched for the first N titles of a list (others: 150) ...
SERIES_TOP_OTHER = 150
PER_CATEGORY_POOL = 40             # ... plus the biggest N of each category (so thin categories can be filled)
RISING_CAP = 25                    # strong + mild risers kept per category
CATEGORY_TARGET = 10               # every category gets at least this many rows when the data has them
PRIOR_DAYS, PRIOR_TOP = 5, 150     # English top lists of the days before: finds topics that peaked and ended
SEASONAL_FOR = 150            # monthly series are fetched for the best N topics only
LOG = Callable[[str], None]


def _accept(res: Resolution) -> bool:
    return bool(res.qid) and res.match in ("label", "alias") and (not res.ambiguous or res.confidence >= 0.55)


@dataclass
class Ctx:
    rt: Any
    wd: Wikidata
    resolver: Resolver
    classifier: Classifier
    end: date
    now: datetime
    log: LOG
    regions: list[dict[str, Any]]
    series: dict[tuple[str, str], list[int] | None] = field(default_factory=dict)
    gdelt: dict = field(default_factory=dict)
    cpv: dict = field(default_factory=dict)
    cpv_end: date | None = None
    yt_key: str | None = None
    yt_units: int = 0
    first_seen: dict[str, str] = field(default_factory=dict)
    feat: dict[str, dict[str, set]] = field(default_factory=dict)
    gh_topics: list[dict[str, Any]] = field(default_factory=list)
    seeds: dict[str, dict[str, Any]] = field(default_factory=dict)
    feeds: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)      # source -> region -> rows
    feed_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    experimental: frozenset[str] = frozenset()    # experimental sources the operator switched on (default: none)
    fetched: int = 0
    deadline: float | None = None            # time.monotonic() value after which no new network work starts

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def featured(self, lang: str) -> dict[str, set]:
        """Main Page titles for an edition: TFA of the last 8 days (its spike ends up in 7-day windows too),
        "On this day" pages of the last 2 days."""
        if lang not in self.feat:
            f: dict[str, set] = {"tfa": set(), "otd": set()}
            for back in range(0, 8):
                day = pv.featured(self.rt.client, lang, self.end - timedelta(days=back))
                f["tfa"] |= day["tfa"]
                if back < 2:
                    f["otd"] |= day["otd"]
            self.feat[lang] = f
        return self.feat[lang]

    def wiki_series(self, lang: str, title: str) -> list[int] | None:
        k = (lang, title)
        if k not in self.series:
            if self.out_of_time():
                found, hit = self.rt.cache.get("pvday", f"{lang}|{title}|{self.end.isoformat()}")
                return hit if found else None       # budget used: cached series only, no new request
            ck = f"{lang}|{title}|{self.end.isoformat()}"
            found, hit = self.rt.cache.get("pvday", ck)
            if found:
                self.series[k] = hit
            else:
                self.series[k] = pv.daily_series(self.rt.client, lang, title, self.end, PAGEVIEW_DAYS)
                if self.series[k] is not None:
                    self.rt.cache.put("pvday", ck, self.series[k])
                    self.fetched += 1
                    if self.fetched % 100 == 0:            # a killed run keeps what it fetched
                        self.rt.cache.commit()
        return self.series[k]


# -- candidates -----------------------------------------------------------------------------

def wiki_candidates(ctx: Ctx, langs: list[str], prior_en: bool) -> dict[str, dict[str, Any]]:
    """{qid: {"wiki": {lang: {title, views}}}} from each edition's top list of ``ctx.end``."""
    out: dict[str, dict[str, Any]] = {}
    for lang in langs:
        top = pv.top_articles(ctx.rt.client, lang, ctx.end, retries=2)
        if not top:
            ctx.log(f"  top {lang}: not available")
            continue
        tfa = ctx.featured(lang)["tfa"]
        top = [(t, v, i) for i, (t, v) in enumerate(top[:POOL_TOP], start=1) if t not in tfa]
        qids = ctx.wd.qids_for_titles(f"{lang}wiki", [t for t, _v, _i in top])
        n = 0
        for title, views, rank in top:
            q = qids.get(title)
            if q:
                out.setdefault(q, {"wiki": {}})["wiki"][lang] = {"title": title, "views": views, "rank": rank}
                n += 1
        ctx.log(f"  top {lang}: {len(top)} titles -> {n} QIDs")
    if prior_en:
        n = 0
        for back in range(1, PRIOR_DAYS + 1):
            top = pv.top_articles(ctx.rt.client, "en", ctx.end - timedelta(days=back), retries=1)
            if not top:
                continue
            tfa = ctx.featured("en")["tfa"]
            top = [(t, v, i) for i, (t, v) in enumerate(top[:PRIOR_TOP], start=1) if t not in tfa]
            qids = ctx.wd.qids_for_titles("enwiki", [t for t, _v, _i in top])
            for title, views, rank in top:
                q = qids.get(title)
                if q and "en" not in out.setdefault(q, {"wiki": {}})["wiki"]:
                    out[q]["wiki"]["en"] = {"title": title, "views": views, "rank": rank}
                    n += 1
        ctx.log(f"  top en, {PRIOR_DAYS} days before: {n} more QIDs")
    return out


def news_candidates(ctx: Ctx, region: str) -> tuple[dict[str, dict[str, Any]], int]:
    """({qid: {"news_names": [...]}}, candidate names) for GDELT's ``region`` counters ("WW" = world)."""
    out: dict[str, dict[str, Any]] = {}
    names = newsmod.candidate_names(ctx.gdelt, ctx.end, region=region)
    resolved = 0
    for name in names:
        res = ctx.resolver.resolve(name, min_sitelinks=10)
        if _accept(res):
            out.setdefault(res.qid, {}).setdefault("news_names", []).append(name)
            resolved += 1
    ctx.log(f"  gdelt {region}: {len(names)} candidate names -> {resolved} resolved")
    return out, len(names)


def phrase_candidates(ctx: Ctx, items: list[dict[str, Any]], known_labels: dict[str, str], key: str
                      ) -> dict[str, dict[str, Any]]:
    """Attach story/video ``items`` (each with a ``title``) to QIDs: capitalised phrases resolved through
    Wikidata, plus the labels of topics other channels already found. {qid: {key: [items]}}."""
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        found: set[str] = set()
        for ph in hnmod.candidate_phrases(it["title"]):
            res = ctx.resolver.resolve(ph, min_sitelinks=40 if " " not in ph else 15)
            if _accept(res):
                found.add(res.qid)
        for q, label in known_labels.items():
            if hnmod.title_mentions(it["title"], label):
                found.add(q)
        for q in found:
            out.setdefault(q, {}).setdefault(key, []).append(it)
    return out


# -- assembling one topic (pure) --------------------------------------------------------------

def _wiki_channel(c: dict[str, Any]) -> dict[str, Any] | None:
    """The Wikipedia channel: per-country dataset series (scope "country") or per-article series of the
    editions (scope "global" / "language")."""
    cs = c.get("country_series")
    if cs:
        vals, censored = cs
        st = window_stats(vals, 1)
        if not st:
            return None
        title, project = c["country_title"], c["country_project"]
        lang = project.split(".")[0]
        return {"value": st["value"], "unit": countrypv.UNIT, "baseline": st["baseline"], "ratio": st["ratio"], **peak_info(vals),
                "floor": c.get("country_floor", FLOORS["wiki"]) * (OTD_FLOOR_FACTOR if c.get("country_otd") else 1),
                "windows": channel_windows(vals), "series": vals[-SERIES_SHOWN:], "scope": "country",
                "censored": censored, "wiki": lang, "title": title,
                "source": countrypv.NAME, "source_url": pv.article_url(lang, title), "license": countrypv.LICENSE}
    series_by_wiki = c.get("wiki_series") or {}
    otd = set(c.get("otd_langs") or [])

    def floor_of(lang: str) -> float:
        base = c["floor_override"] if c.get("floor_override") else WIKI_FLOORS.get(lang, FLOORS["wiki"])
        return base * (OTD_FLOOR_FACTOR if lang in otd else 1)

    best_lang, best = None, -1.0
    by_wiki = {}
    for lang, s in series_by_wiki.items():
        st = window_stats(s, 1)
        if not st:
            continue
        by_wiki[lang] = {"title": c["titles"][lang], "value": st["value"], "ratio": st["ratio"]}
        lvl = statistics.fmean(s[-2:]) / floor_of(lang)       # the edition where the topic is biggest for its audience
        if lvl > best:
            best_lang, best = lang, lvl
    if best_lang is None:
        return None
    s = series_by_wiki[best_lang]
    st = window_stats(s, 1) or {}
    title = c["titles"][best_lang]
    return {"value": st["value"], "unit": "pageviews/day", "baseline": st["baseline"], "ratio": st["ratio"], **peak_info(s),
            "floor": floor_of(best_lang),
            "windows": channel_windows(s), "series": s[-SERIES_SHOWN:], "scope": c.get("wiki_scope", "global"),
            "wiki": best_lang, "title": title, "by_wiki": by_wiki, "source": pv.NAME,
            "source_url": pv.article_url(best_lang, title), "license": pv.LICENSE}


def _news_channel(series: list[float], examples: list[list[str]], region: str) -> dict[str, Any] | None:
    st = window_stats(series, 1)
    if not st:
        return None
    url = next((e[1] for e in examples if e[1]), "https://www.gdeltproject.org/")
    unit = newsmod.UNIT if region == "WW" else newsmod.UNIT_COUNTRY
    ch = {"value": st["value"], "unit": unit, "baseline": st["baseline"], "ratio": st["ratio"], **peak_info(series),
          "windows": channel_windows(series), "series": series[-SERIES_SHOWN:], "source": newsmod.NAME,
          "source_url": url, "license": newsmod.ATTRIBUTION}
    if st["baseline"] < FLOORS["news"] / BASELINE_FRACTION:        # nothing to compare with: a ratio would be a division by ~0
        ch.update(ratio=None, windows={}, ratio_note="no baseline: fewer than 5 articles a day before the window")
    return ch


def _hn_channel(stories: list[dict[str, Any]]) -> dict[str, Any]:
    top = stories[0]
    return {"value": sum(s["points"] for s in stories), "unit": hnmod.UNIT, "stories": len(stories),
            "source": hnmod.NAME, "source_url": top["hn_url"], "license": hnmod.LICENSE}


def _youtube_channel(videos: list[dict[str, Any]]) -> dict[str, Any]:
    """Chart presence plus the raw view counts YouTube reports; no ratio, no derived number."""
    best = min(videos, key=lambda v: v["rank"])
    views = [v["views"] for v in videos if v.get("views") is not None]
    return {"chart": True, "rank": best["rank"], "videos": len(videos), "value": sum(views) if views else None,
            "unit": ytmod.UNIT, "source": ytmod.NAME, "source_url": best["url"], "license": ytmod.LICENSE}


def assemble_topic(c: dict[str, Any], cls: Classification, end: date, now: datetime,
                   first_seen: str | None, seasonal: dict[str, Any] | None = None,
                   region: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One topic row from a candidate; None when nothing is rising (not listed).

    ``c``: qid, rec (entity record), titles {lang: title}, wiki_series {lang: [..]} or country_series
    ((values, censored)) + country_title + country_project, news_names, news_series, news_examples, hn
    (stories), youtube (videos)."""
    region = region or {"id": "global", "languages": []}
    rid = region["id"]
    channels: dict[str, dict[str, Any]] = {}
    w = _wiki_channel(c)
    if w:
        channels["wikipedia"] = w
    if c.get("news_series"):
        n = _news_channel(c["news_series"], c.get("news_examples") or [], "WW" if rid == "global" else rid)
        if n:
            channels["news"] = n
    if c.get("hn"):
        channels["hn"] = _hn_channel(c["hn"])
    if c.get("youtube"):
        channels["youtube"] = _youtube_channel(c["youtube"])
    tr = trend_of(channels)
    if tr is None:
        return None
    trend, rise, status = tr
    sc = score_topic(channels)
    if sc["ratio"] is None:                     # steady and fading rows keep their real ratio
        best = None
        for ck, ch in channels.items():
            w = ch.get("windows") or {}
            for k in ("24h", "48h"):
                if k in w and (best is None or w[k]["ratio"] > best[0]):
                    best = (w[k]["ratio"], k, ck)
        if best:
            sc["ratio"], sc["ratio_window"], sc["ratio_channel"] = best
        if rise == "mild":                      # ranked like a riser, but one family at most
            sc["rank_score"] = round(rank_score(sc["volume"], sc["ratio"], 1), 3)
            sc["score"] = min(100, round(SCORE_SCALE * sc["rank_score"]))
    if trend == "fading":                       # past its peak: listed, never ranked as a riser
        sc["rank_score"], sc["score"] = 0.0, 0
    evidence = _evidence(channels, c)
    ranked = [k for k in (sc["rising_channels"] or list(channels)) if channels[k].get("ratio") is not None]
    primary = max(ranked, key=lambda k: channels[k]["ratio"]) if ranked else (sc["rising_channels"] or list(channels))[0]
    pch = channels[primary]
    rec = c["rec"]
    windows = {}
    for label in WINDOWS:
        rs = [(ch["windows"][label]["ratio"], ck) for ck, ch in channels.items() if label in (ch.get("windows") or {})]
        if rs:
            top = max(rs)
            windows[label] = {"ratio": top[0], "active": top[0] >= ACTIVE_RATIO, "channel": top[1]}
    pk_ch = next((channels[k] for k in ("wikipedia", "news") if k in channels and channels[k].get("peak") is not None), None)
    peak = ({"value": pk_ch["peak"], "days_ago": pk_ch["peak_days_ago"], "from_peak": pk_ch["from_peak"], "unit": pk_ch["unit"]} if pk_ch else None)
    native = next((rec["labels"][lg] for lg in region.get("languages", []) if lg in rec.get("labels", {})), None)
    label = rec["label"] or next(iter((c.get("titles") or {}).values()), None) or c.get("country_title") or rec["id"]
    return {
        "id": rec["id"], "qid": rec["id"], "label": label,
        "native_label": native if native and native != label else None,
        "description": rec["description"],
        "category": cls.category, "category_label": category_label(cls.category),
        "category_via": {"kind": cls.kind, "class": cls.via, "label": cls.via_label},
        "region": rid, "trend": trend, "rise": rise, "status": status, "ratio": sc["ratio"], "ratio_window": sc.get("ratio_window"), "ratio_channel": sc.get("ratio_channel"),
        "peak": peak, "volume": sc["volume"], "volume_unit": sc["volume_unit"],
        "rank_score": sc["rank_score"], "score": sc["score"],
        "source_families": sc["families"], "multi_source": sc["multi_source"],
        "rising_channels": sc["rising_channels"],
        "seasonal": (seasonal or {}).get("verdict", "unknown"), "seasonal_detail": seasonal,
        "first_seen": first_seen or end.isoformat(), "series_end": end.isoformat(), "windows": windows,
        "channels": channels, "evidence": evidence,
        "source": pch["source"], "source_url": pch["source_url"], "license": pch["license"],
        "sources": sorted({ch["source"] for ch in channels.values()}),
        "aliases": list(rec.get("aliases") or [])[:8],
        "breakdown": _breakdown(c, rec),
        "wikipedia_url": pv.article_url("en", rec["titles"]["enwiki"]) if "enwiki" in rec["titles"] else None,
    }


def _breakdown(c: dict[str, Any], rec: dict[str, Any]) -> list[str]:
    """Other names the topic goes by ("Trend breakdown"): Wikidata aliases, article titles in the other
    wikis, and the names GDELT counted it under. Not queries: nobody searched for these."""
    names = list(rec.get("aliases") or []) + [t for lang, t in (c.get("titles") or {}).items() if lang != "en"]
    if c.get("country_title"):
        names.append(c["country_title"])
    names += c.get("news_names") or []
    seen = {rec["label"].casefold()}
    out = []
    for n in names:
        if n and n.casefold() not in seen:
            seen.add(n.casefold())
            out.append(n)
    return out[:8]


def _evidence(channels: dict[str, dict[str, Any]], c: dict[str, Any]) -> list[dict[str, Any]]:
    ev = []
    if "wikipedia" in channels:
        w = channels["wikipedia"]
        ev.append({"source": w["source"], "source_url": w["source_url"], "license": w["license"],
                   "title": f"{w['wiki']}.wikipedia: {w['title']}", "value": w["value"], "unit": w["unit"]})
    if "news" in channels:
        n = channels["news"]
        seen = set()
        for title, url in (c.get("news_examples") or [])[:3]:
            if url and url not in seen:
                seen.add(url)
                ev.append({"source": n["source"], "source_url": url, "license": n["license"],
                           "title": title or url, "value": n["value"], "unit": n["unit"]})
        if not seen:
            ev.append({"source": n["source"], "source_url": n["source_url"], "license": n["license"],
                       "title": "GDELT article counts", "value": n["value"], "unit": n["unit"]})
    for s in (c.get("hn") or [])[:3]:
        ev.append({"source": hnmod.NAME, "source_url": s["hn_url"], "license": hnmod.LICENSE,
                   "title": s["title"], "value": s["points"], "unit": "HN points", "comments": s["comments"]})
    for v in (c.get("youtube") or [])[:3]:
        ev.append({"source": ytmod.NAME, "source_url": v["url"], "license": ytmod.LICENSE, "title": v["title"],
                   "value": v.get("views"), "unit": ytmod.UNIT, "rank": v["rank"]})
    return ev


# -- the build --------------------------------------------------------------------------------

def _first_seen_load(state_dir: Path) -> dict[str, str]:
    p = state_dir / "first_seen.json"
    return json.loads(p.read_text()) if p.exists() else {}


def is_main_page(rec: dict[str, Any]) -> bool:
    """The front page of a Wikimedia project in any edition (Q5296 "Wikimedia main page"): it is read by everyone
    who opens the site, not a topic."""
    return rec["id"] == "Q5296" or (rec.get("description") or "").lower().startswith("main page of a wikimedia")


def _drop_non_topics(ctx: Ctx, cands: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    recs = ctx.wd.entities(list(cands))
    for q in list(cands):
        r = recs.get(q)
        if r is None or set(r["claims"].get("P31", [])) & NOT_TOPIC.keys() or is_main_page(r):
            del cands[q]
    return recs


def _merge(into: dict[str, dict[str, Any]], more: dict[str, dict[str, Any]]) -> None:
    for q, v in more.items():
        into.setdefault(q, {}).update(v)


def select_per_category(topics: list[dict[str, Any]], target: int = CATEGORY_TARGET, cap: int = RISING_CAP
                        ) -> list[dict[str, Any]]:
    """Per category: every strong riser (best ``cap``), then mild risers and finally steady top items until the
    category has ``target`` rows or the data runs out. Nothing is invented: a category that cannot reach
    ``target`` keeps fewer rows and is reported as limited. Order: risers by rank_score, then steady by volume."""
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for t in topics:
        by_cat.setdefault(t["category"], []).append(t)
    keep: list[dict[str, Any]] = []
    for rows in by_cat.values():
        strong = sorted((t for t in rows if t["rise"] == "strong"), key=lambda t: -t["rank_score"])[:cap]
        mild = sorted((t for t in rows if t["rise"] == "mild"), key=lambda t: -t["rank_score"])
        steady = sorted((t for t in rows if t["trend"] in ("steady", "fading")), key=lambda t: -(t["volume"] or 0))
        chosen = list(strong)
        for pool in (mild, steady):
            for t in pool:
                if len(chosen) >= target:
                    break
                chosen.append(t)
        keep += chosen
    keep.sort(key=lambda t: (t["trend"] != "rising", -t["rank_score"], -(t["volume"] or 0)))
    return keep


def coverage(topics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{category id: {rising, steady, total, limited, by_window}} for one region's rows.

    Counts the rows the page lists under its default window (7d), the widest one: a row without a 7d window is
    not shown there, so it is not counted. This is the same count the page uses for "limited data".
    ``by_window`` = per window the rows a reader sees there: rising rows active in that window plus every
    steady row (steady rows are listed under every window)."""
    from nicheclock.catalog.categories import category_ids

    out = {}
    for cid in category_ids():
        every = [t for t in topics if t["category"] == cid]
        rows = [t for t in every if (t.get("windows") or {}).get("7d")]
        steady = sum(1 for t in rows if t["trend"] != "rising")
        out[cid] = {"rising": len(rows) - steady, "steady": steady, "total": len(rows),
                    "limited": len(rows) < CATEGORY_TARGET,
                    "by_window": {w: {"rising": sum(1 for t in every if t["trend"] == "rising" and (t["windows"].get(w) or {}).get("active")),
                                      "total": steady + sum(1 for t in every if t["trend"] == "rising" and (t["windows"].get(w) or {}).get("active"))}
                                  for w in WINDOWS}}
    return out


def _finish(ctx: Ctx, region: dict[str, Any], cands: dict[str, dict[str, Any]], recs: dict[str, Any],
            end: date, classes: dict[str, Classification]) -> list[dict[str, Any]]:
    """Assemble, choose per category, add seasonality and first_seen for one region's candidates."""
    rid = region["id"]
    topics = []
    for q, c in cands.items():
        c["rec"] = recs[q]
        t = assemble_topic(c, classes[q], end, ctx.now,
                           ctx.first_seen.get(f"{rid}:{q}" if rid != "global" else q), region=region)
        if t:
            topics.append(t)
    if rid == "global":
        topics += ctx.gh_topics                    # GitHub repos are Technology rows of the Worldwide list
    n_rising = sum(1 for t in topics if t["rise"] == "strong")
    topics = select_per_category(topics)
    ctx.log(f"  rows: {len(topics)} ({n_rising} strong risers found, {sum(1 for t in topics if t['trend'] == 'steady')} steady fill)")
    best = sorted((t for t in topics if t["trend"] == "rising"), key=lambda t: -t["rank_score"])[:SEASONAL_FOR]
    for t in best:
        w = t["channels"].get("wikipedia")
        if not w:
            continue
        m = pv.monthly_series(ctx.rt.client, w["wiki"], w["title"], ctx.end)
        if m is not None:
            sea = seasonality(m, ctx.end)
            t["seasonal"], t["seasonal_detail"] = sea["verdict"], sea
    for t in topics:
        k = t["qid"] if rid == "global" else f"{rid}:{t['qid']}"
        ctx.first_seen.setdefault(k, t["series_end"])
        t["first_seen"] = ctx.first_seen[k]
    return topics


def _series_cutoff(lang: str) -> int:
    return SERIES_TOP.get(lang, SERIES_TOP_OTHER)


def _mark_wanted(cands: dict[str, dict[str, Any]], classes: dict[str, Classification]) -> None:
    """Pool entries beyond the series cutoff get a series when they are among the biggest of their category
    (volume relative to the edition's floor), so thin categories can be filled with steady items."""
    by_cat: dict[str, list[tuple[float, str]]] = {}
    for q, c in cands.items():
        best = max(((v["views"] / WIKI_FLOORS.get(lang, FLOORS["wiki"])) for lang, v in (c.get("wiki") or {}).items()), default=0.0)
        by_cat.setdefault(classes[q].category, []).append((best, q))
    for rows in by_cat.values():
        for _score, q in sorted(rows, reverse=True)[:PER_CATEGORY_POOL]:
            cands[q]["want_series"] = True


def _add_wiki_series(ctx: Ctx, cands: dict[str, dict[str, Any]], recs: dict[str, Any], scope: str) -> int:
    fetched = 0
    for q, c in cands.items():
        c["titles"] = {lang: v["title"] for lang, v in (c.get("wiki") or {}).items()}
        if scope == "global" and "en" not in c["titles"] and "enwiki" in recs[q]["titles"]:
            c["titles"]["en"] = recs[q]["titles"]["enwiki"]
        c["wiki_scope"] = scope
        c["wiki_series"] = {}
        c["otd_langs"] = []
        for lang, title in list(c["titles"].items()):
            info = (c.get("wiki") or {}).get(lang)
            if info and info.get("rank", 0) > _series_cutoff(lang) and not c.get("want_series"):
                continue
            f = ctx.featured(lang)
            if title in f["tfa"]:
                del c["titles"][lang]
                continue
            if title in f["otd"]:
                c["otd_langs"].append(lang)
            s = ctx.wiki_series(lang, title)
            if s is not None:
                c["wiki_series"][lang] = s
                fetched += 1
    return fetched


def apply_category_floors(cands: dict[str, dict[str, Any]], classes: dict[str, Classification]) -> dict[str, float]:
    """Category-relative floors for seeded pages (global scope): the category's own p75 of last-2-day English
    views, clamped to [150, en floor]; replaces the fixed 5,000/day for those pages."""
    vols: dict[str, list[float]] = {}
    for q, c in cands.items():
        if "seed" in c:
            s = (c.get("wiki_series") or {}).get("en")
            if s:
                vols.setdefault(c["seed"], []).append(statistics.fmean(s[-2:]))
    floors = seedmod.category_floors(vols, WIKI_FLOORS["en"])
    for c in cands.values():
        if "seed" in c and c["seed"] in floors:
            c["floor_override"] = floors[c["seed"]]
    return floors


def _add_news(ctx: Ctx, cands: dict[str, dict[str, Any]], region: str) -> None:
    for c in cands.values():
        names = c.get("news_names")
        if names and ctx.end in ctx.gdelt:
            c["news_series"] = newsmod.series_for(names, ctx.gdelt, ctx.end, 28, region)
            ex = ctx.gdelt[ctx.end]["examples"].get(region, {})
            c["news_examples"] = [ex[n] for n in names if n in ex]


def _youtube(ctx: Ctx, region: dict[str, Any], known: dict[str, str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    cc = region.get("youtube")
    if not cc:
        return {}, {"status": "no_data", "detail": "YouTube is not available for this region"}
    if not ctx.yt_key:
        return {}, {"status": "pending_key", "detail": "no YOUTUBE_API_KEY at build time"}
    if ctx.yt_units >= ytmod.DAILY_UNIT_BUDGET:
        return {}, {"status": "no_data", "detail": "daily quota budget used"}
    ctx.yt_units += 1
    videos, detail = ytmod.fetch_chart(ctx.rt.client, ctx.yt_key, cc)
    if videos is None:
        return {}, {"status": "no_data", "detail": detail}
    out = phrase_candidates(ctx, videos, known, "youtube")
    return out, {"status": "ok", "detail": f"{len(videos)} chart videos -> {len(out)} topics"}


def build_region(ctx: Ctx, region: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """(trending doc, {source key: {status, detail}}) for one region."""
    rid = region["id"]
    ctx.log(f"region {rid}")
    st: dict[str, dict[str, Any]] = {}
    cands: dict[str, dict[str, Any]] = {}
    end = ctx.end
    if rid == "global":
        _merge(cands, wiki_candidates(ctx, region["wikipedia"], prior_en=True))
        for q, m in ctx.seeds.items():
            cands.setdefault(q, {}).update({"seed": m["category"], "seed_class": m["class"]})
        st["wikipedia"] = {"status": "ok", "detail": f"editions {', '.join(region['wikipedia'])}; {len(ctx.seeds)} category-seeded pages"}
        wscope = "global"
    else:
        iso2 = region["iso2"]
        page = (ctx.cpv.get(ctx.cpv_end or ctx.end, {}) or {}).get(iso2) if ctx.cpv else None
        rows = page["rows"] if page else 0
        if rows >= countrypv.MIN_ROWS:
            end = ctx.cpv_end or ctx.end
            qids = countrypv.pool(ctx.cpv, end, iso2)
            floor = ctx.cpv[end][iso2]["floor"]
            for q in qids:
                p = ctx.cpv[end][iso2]["pages"][q]
                lang = p[2].split(".")[0]
                f = ctx.featured(lang)
                if p[1] in f["tfa"]:
                    continue
                cands[q] = {"country_title": p[1], "country_project": p[2], "country_floor": max(400.0, 4 * floor),
                            "country_otd": p[1] in f["otd"]}
            pages = ctx.cpv[end][iso2]["pages"]
            vols: dict[str, list[float]] = {}
            for q, m in ctx.seeds.items():
                if q in pages:
                    vols.setdefault(m["category"], []).append(pages[q][0])
            cfloor = {k: max(2 * floor, v) for k, v in seedmod.category_floors(vols, 400.0, minimum=2 * floor).items()}
            for q, m in ctx.seeds.items():
                if q in pages and pages[q][1] not in ctx.featured(pages[q][2].split(".")[0])["tfa"]:
                    cands.setdefault(q, {}).update({"country_title": pages[q][1], "country_project": pages[q][2],
                                                    "seed": m["category"], "seed_class": m["class"],
                                                    "country_floor": cfloor.get(m["category"], 2 * floor)})
            st["wikipedia"] = {"status": "ok", "detail": f"Wikimedia per-country data, {rows} published pages on {end}; {sum(1 for c in cands.values() if 'seed' in c)} category-seeded pages"}
            wscope = "country"
        else:
            _merge(cands, wiki_candidates(ctx, region["wikipedia"], prior_en=False))
            why = region.get("note") or f"Wikimedia published only {rows} {'page' if rows == 1 else 'pages'} for this country"
            st["wikipedia"] = {"status": "limited", "detail": f"language edition(s) {', '.join(region['wikipedia'])}, read worldwide, not this country. {why}"}
            wscope = "language"
    gkey = "WW" if rid == "global" else rid
    if ctx.gdelt and ctx.end in ctx.gdelt and gkey in ctx.gdelt[ctx.end]["counts"]:
        nc, nn = news_candidates(ctx, gkey)
        _merge(cands, nc)
        st["news"] = {"status": "ok", "detail": f"{nn} candidate names" + ("" if rid == "global" else "; articles mentioning places in this country")}
    else:
        st["news"] = {"status": "no_data", "detail": "no GDELT sample for this region"}
    if rid == "global":
        recs0 = _drop_non_topics(ctx, cands)
        known = {q: recs0[q]["label"] for q in cands if recs0[q]["label"]}
        stories = hnmod.fetch_stories(ctx.rt.client)
        if stories:
            hc = phrase_candidates(ctx, stories, known, "hn")
            _merge(cands, hc)
            st["hn"] = {"status": "ok", "detail": f"{len(stories)} stories -> {len(hc)} topics"}
        else:
            st["hn"] = {"status": "no_data", "detail": "no stories fetched"}
    else:
        recs0 = _drop_non_topics(ctx, cands)
        known = {q: recs0[q]["label"] for q in cands if recs0[q]["label"]}
        yc, yst = _youtube(ctx, region, known)
        _merge(cands, yc)
        st["youtube"] = yst
    recs = _drop_non_topics(ctx, cands)
    ctx.log(f"  candidates: {len(cands)} entities")
    classes = ctx.classifier.classify_many([q for q in cands if "seed" not in cands[q]])
    for q, c in cands.items():
        if "seed" in c:                            # a direct instance of a seed class: the seed's category is exact
            classes[q] = Classification(c["seed"], "seed", c["seed_class"], ctx.wd.entities([c["seed_class"]]).get(c["seed_class"], {}).get("label", ""), 0)
    if wscope != "country":
        _mark_wanted(cands, classes)
    if wscope == "country":
        got = 0
        for q, c in list(cands.items()):
            if "country_title" in c:
                s = countrypv.series(ctx.cpv, end, region["iso2"], q, COUNTRY_DAYS)
                if s:
                    c["country_series"] = s
                    got += 1
    else:
        got = _add_wiki_series(ctx, cands, recs, wscope)
        if wscope == "global":
            fl = apply_category_floors(cands, classes)
            ctx.log("  category floors: " + ", ".join(f"{k}={v:.0f}" for k, v in sorted(fl.items())))
    ctx.log(f"  wikipedia series: {got}")
    _add_news(ctx, cands, gkey)
    topics = _finish(ctx, region, cands, recs, end, classes)
    for fkey, by_region in ctx.feeds.items():
        rows = [dict(r, region=rid) for r in by_region.get(rid, [])]
        if rows:
            topics += rows
            st[fkey] = {"status": "ok", "detail": ctx.feed_status[fkey]["detail"]}
    if rid != "global":
        topics += fill_youtube(ctx, region, topics, end)
    for t in topics:
        t["region"] = rid
    for key in st:
        st[key]["topics"] = sum(1 for t in topics if key in t["channels"])
    doc = {"schema": 2, "region": rid, "date": end.isoformat(), "generated_at": ctx.now.strftime("%Y-%m-%dT%H:%MZ"),
           "window_note": ("Each channel is compared with its own median of the 14 days before the window: "
                           "pageviews/day (Wikipedia), estimated articles/day (GDELT), points (Hacker News). "
                           "Units are never mixed and none of them is search volume. A source with no data for "
                           "a region is absent from its rows."),
           "coverage": coverage(topics), "topics": topics}
    return doc, st


def _short_categories(topics: list[dict[str, Any]], target: int = CATEGORY_TARGET) -> dict[str, int]:
    """{category: rows still missing} for categories under ``target`` (the "other" bucket is never filled)."""
    from nicheclock.catalog.categories import category_ids

    have = {c: 0 for c in category_ids() if c != "other"}
    for t in topics:
        if t["category"] in have:
            have[t["category"]] += 1
    return {c: target - n for c, n in have.items() if n < target}


def fill_youtube(ctx: Ctx, region: dict[str, Any], topics: list[dict[str, Any]], end: date) -> list[dict[str, Any]]:
    """Chart videos of YouTube's own category charts for short categories (sidecar only, steady/top, raw views)."""
    cc = region.get("youtube")
    if not cc or not ctx.yt_key:
        return []
    short = _short_categories(topics)
    out: list[dict[str, Any]] = []
    for yid, cat in ytmod.CATEGORY_MAP.items():
        missing = short.get(cat, 0) - sum(1 for t in out if t["category"] == cat)
        if missing <= 0 or ctx.yt_units >= ytmod.DAILY_UNIT_BUDGET:
            continue
        ctx.yt_units += 1
        videos, detail = ytmod.fetch_chart(ctx.rt.client, ctx.yt_key, cc, yid)
        if not videos:
            ctx.log(f"  youtube {cc} category {yid}: {detail}")
            continue
        for v in videos[:missing]:
            out.append(ytmod.video_row(v, cat, category_label(cat), region, end.isoformat(), yid))
        ctx.log(f"  youtube {cc} category {yid} -> {cat}: {min(missing, len(videos))} video rows")
    return out


DISABLED = {"status": "disabled", "detail": "experimental source, off by default (enable with --experimental after reading its terms)"}


def load_feeds(ctx: Ctx, today: date) -> None:
    """Fetch the M2 feeds once per build (each one fails alone: a broken source is reported, the rest still build)."""
    end = ctx.end.isoformat()
    c = ctx.rt.client
    jobs = {"gdacs": lambda: feedmod.gdacs_rows(c, end),
            "tvmaze": lambda: feedmod.tvmaze_rows(c, today, ctx.regions, end),
            "stackexchange": lambda: feedmod.stackexchange_rows(c, end),
            "pypi": lambda: feedmod.pypi_rows(c, ctx.end, end),
            "openlibrary": lambda: feedmod.openlibrary_rows(c, end)}
    for key, job in jobs.items():
        if key in feedmod.EXPERIMENTAL and key not in ctx.experimental:
            ctx.feed_status[key] = DISABLED
            continue
        if ctx.out_of_time():
            ctx.feed_status[key] = {"status": "skipped", "detail": "time budget used"}
            continue
        try:
            rows, status = job()
        except Exception as e:
            rows, status = {}, {"status": "no_data", "detail": f"{type(e).__name__}: {e}"}
        ctx.feeds[key] = rows
        ctx.feed_status[key] = status
        ctx.log(f"feed {key}: {status['status']}: {status['detail']}")


def build_github(ctx: Ctx, today: date) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(github-rank doc or None, status). Fills ``ctx.gh_topics``."""
    if "gharchive" not in ctx.experimental:
        return None, DISABLED
    token = ghmod.load_token()
    if not token:
        return None, {"status": "pending_key", "detail": "no GITHUB_TOKEN and no `gh` login"}
    end = ghmod.latest_complete_day(ctx.rt.client, today)
    if end is None:
        return None, {"status": "no_data", "detail": "GH Archive has no complete day yet"}
    samples = ghmod.gather(ctx.rt.client, ctx.rt.state_dir, end)
    if end not in samples:
        return None, {"status": "no_data", "detail": "GH Archive day could not be read"}
    totals = ghmod.window_totals(samples, end)
    names = ghmod.pool(totals)
    new = ghmod.search_new_repos(ctx.rt.client, token, end, ctx.rt.cache)
    names = list(dict.fromkeys(names + [n for n in new if n in totals]))
    meta = ghmod.fetch_meta(ctx.rt.client, token, names, ctx.rt.cache)
    rows = ghmod.build_rows(samples, end, meta)
    now_iso = ctx.now.strftime("%Y-%m-%dT%H:%MZ")
    doc = ghmod.rank_doc(rows, end, now_iso)
    ctx.gh_topics = ghmod.trending_topics(rows, end, now_iso)
    ctx.log(f"github: {len(samples)} archive days to {end}, {len(names)} repos looked up, {len(rows)} ranked, "
            f"{len(ctx.gh_topics)} trending rows")
    return doc, {"status": "ok", "detail": f"{len(rows)} repos ranked from GH Archive stars (days to {end}) + GitHub API metadata",
                 "topics": len(ctx.gh_topics)}


def build_all(rt: Any, wd: Wikidata, resolver: Resolver, classifier: Classifier, today: date,
              regions: list[dict[str, Any]], log: LOG = print, max_seconds: float | None = None,
              experimental: Any = ()) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]], dict[str, Any]]:
    """({region id: trending doc}, {region id: {source key: status}}, {extra file name: doc})."""
    end = pv.latest_day(rt.client, today)
    if end is None:
        raise RuntimeError("Wikimedia pageviews not available")
    log(f"data day {end}")
    now = datetime.now(timezone.utc)
    ctx = Ctx(rt, wd, resolver, classifier, end, now, log, regions, first_seen=_first_seen_load(rt.state_dir),
              deadline=(time.monotonic() + max_seconds) if max_seconds else None, experimental=frozenset(experimental))
    countries = [r for r in regions if r["id"] != "global"]
    fips = {r["fips"]: r["id"] for r in countries}
    try:
        ctx.gdelt = newsmod.gather(rt.client, rt.state_dir, end, 17, fips)
        log(f"gdelt: {len(ctx.gdelt)} sampled days")
    except Exception as e:                     # one broken source never kills the run
        log(f"gdelt FAILED: {type(e).__name__}: {e}")
    cend = countrypv.latest_day(rt.client)
    if cend is not None:
        ctx.cpv_end = min(cend, end)
        ctx.cpv = countrypv.gather(rt.client, rt.state_dir, ctx.cpv_end, [r["iso2"] for r in countries], COUNTRY_DAYS)
        log(f"country pageviews: {len(ctx.cpv)} days up to {ctx.cpv_end}")
    ctx.yt_key = ytmod.load_key()
    try:
        ctx.seeds = seedmod.members(wd, log)
        log(f"seeds: {len(ctx.seeds)} members")
    except Exception as e:
        log(f"seeds FAILED: {type(e).__name__}: {e}")
    load_feeds(ctx, today)
    extra: dict[str, Any] = {}
    gh_status: dict[str, Any] = {}
    try:
        gh_doc, gh_status = build_github(ctx, today)
        if gh_doc:
            extra["github-rank.json"] = gh_doc
    except Exception as e:                     # a failing source is reported, the rest still builds
        log(f"github FAILED: {type(e).__name__}: {e}")
        gh_status = {"status": "no_data", "detail": f"{type(e).__name__}: {e}"}
    docs, status = {}, {}
    first = ["global", "US", "IN"]               # the regions that matter most are built first, so a cut-off loses the least
    for region in sorted(regions, key=lambda r: (first.index(r["id"]) if r["id"] in first else len(first), r["rank"])):
        if ctx.out_of_time():
            log(f"region {region['id']} SKIPPED: time budget used")
            status[region["id"]] = {"build": {"status": "skipped", "detail": "time budget used; the previous build of this region stays published"}}
            continue
        try:
            docs[region["id"]], status[region["id"]] = build_region(ctx, region)
        except Exception as e:                 # a region that fails is reported, the others still build
            log(f"region {region['id']} FAILED: {type(e).__name__}: {e}")
            status[region["id"]] = {"build": {"status": "failed", "detail": f"{type(e).__name__}: {e}"}}
    if "global" in status:
        status["global"]["github"] = gh_status
        for key, st in ctx.feed_status.items():
            if st is DISABLED:
                status["global"][key] = dict(st, topics=0)
    (rt.state_dir / "first_seen.json").write_text(json.dumps(ctx.first_seen))
    return docs, status, extra


def split_youtube(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """(public doc without any YouTube-derived data, sidecar {qid: {"channel", "evidence"}}, extra rows).

    ``extra rows`` are the complete rows of topics that exist only because of the chart (no other channel, or
    rising on the chart alone): the public file cannot hold them, the served sidecar appends them.

    YouTube's terms limit how long its statistics may be stored (30 days), so the git-tracked file must not hold
    them: the sidecar (``youtube-<ID>.json``, git-ignored) is regenerated by every build and merged at serve time.
    A topic that only existed because of the chart is dropped from the public file; families, multi_source,
    rising_channels, sources, evidence and the primary source are recomputed without YouTube. Coverage is
    recomputed on what remains."""
    import copy

    out = copy.deepcopy(doc)
    side: dict[str, Any] = {}
    extra: list[dict[str, Any]] = []
    kept = []
    for t in out["topics"]:
        if t.get("sidecar_only"):                 # a chart video row: complete row in the sidecar, nothing public
            extra.append(next(x for x in doc["topics"] if x["id"] == t["id"]))
            continue
        yt = t["channels"].pop("youtube", None)
        if yt is None:
            kept.append(t)
            continue
        side[t["qid"]] = {"channel": yt, "evidence": [e for e in t["evidence"] if e["source"] == ytmod.NAME]}
        if not t["channels"]:
            extra.append(next(x for x in doc["topics"] if x["qid"] == t["qid"]))
            continue
        t["evidence"] = [e for e in t["evidence"] if e["source"] != ytmod.NAME]
        t["sources"] = sorted(c["source"] for c in t["channels"].values())
        t["rising_channels"] = [c for c in t["rising_channels"] if c != "youtube"]
        t["source_families"] = [f for f in t["source_families"] if f != "video"]
        t["multi_source"] = len(t["source_families"]) >= 2
        if t["source"] == ytmod.NAME:
            first = t["channels"][next(iter(t["channels"]))]
            t["source"], t["source_url"], t["license"] = first["source"], first["source_url"], first["license"]
        if t["trend"] == "rising" and not t["rising_channels"]:
            extra.append(next(x for x in doc["topics"] if x["qid"] == t["qid"]))
            continue                            # rose only on the chart: not a public row
        kept.append(t)
    out["topics"] = kept
    out["coverage"] = coverage(kept)
    return out, side, extra

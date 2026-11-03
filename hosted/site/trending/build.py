#!/usr/bin/env python3
"""Build the Trending now page (static HTML + JS + CSS + the catalog JSON it reads).

    python3 hosted/site/trending/build.py [--out DIR] [--data DIR]

--out   default hosted/site/trending/dist (gitignored). Serve it with any static server: python3 -m http.server -d DIR 8788
--data  directory with meta.json, trending.json, upcoming.json. Default: hosted/site/catalog-data if it exists
        (written by the catalog job), else hosted/site/trending/fixture (SAMPLE data, invented numbers).

site/build.py --home trending uses stage() and index_html() to make this page the homepage.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE_DIR = HERE.parent
sys.path.insert(0, str(SITE_DIR))
from ghmark import github_link  # noqa: E402  (shared with site/build.py)
REPO = HERE.parents[2]
CATALOG = SITE_DIR / "catalog-data"
FIXTURE = HERE / "fixture"
DATA_FILES = ("meta.json", "trending.json", "upcoming.json")

# Copy: says only what is true today: MCP runs locally from the repository.
TITLE = "nicheclock: rising topics and when to publish them"
DESC = ("Rising topics from Wikipedia, news and community channels, upcoming events with a publish-by date, "
        "and a table that keeps each volume in its own unit.")
TAGLINE = "Rising topics, dated events, and when to publish."
SUBLINE = "Rising on Wikipedia, news and community channels, with a publish-by date for each event. Agents use it over MCP, run locally."

# Sources kept off the live site until their terms are re-read. Matched against a channel's key, its `source`, an
# evidence item's `source` and the ids in meta.json. "github" is the stars/day channel and the GitHub rank tab.
HIDDEN_SOURCES = frozenset({"stackexchange", "stack_exchange", "npm_pypi", "pypi", "npm", "gharchive", "github"})
UNIT_NOTE = "Every volume has its own unit; none is search volume."


def data_files(data: Path) -> list[Path]:
    """meta.json, trending.json, upcoming.json, one trending-<REGION>.json per country the catalog lists, and github-rank.json when present."""
    extra = [data / "github-rank.json"] if (data / "github-rank.json").is_file() else []
    return [data / f for f in DATA_FILES] + sorted(data.glob("trending-*.json")) + extra + sorted(data.glob("youtube*.json"))


def default_data() -> Path:
    return CATALOG if all((CATALOG / f).is_file() for f in DATA_FILES) else FIXTURE


def _hashed(data: bytes, name: str) -> str:
    stem, _, ext = name.rpartition(".")
    return f"{stem}.{hashlib.sha256(data).hexdigest()[:8]}.{ext}"


def public_meta(path: Path, match: dict | None = None) -> str:
    """meta.json as published: only the sources the catalog uses and the live site shows. The `excluded` and `planned` entries hold
    internal decisions and a roadmap; HIDDEN_SOURCES are kept off the site until their terms are re-read."""
    meta = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(meta.get("sources"), list):
        meta["sources"] = [x for x in meta["sources"] if (not x.get("status") or x.get("status") == "used") and x.get("id") not in HIDDEN_SOURCES]
    if isinstance(meta.get("source_filter"), list):
        meta["source_filter"] = [x for x in meta["source_filter"] if x.get("key") not in HIDDEN_SOURCES]
    if match:
        meta["match"] = match
    return json.dumps(meta, ensure_ascii=False, indent=1) + "\n"


def _hidden_channel(key: str, ch: dict) -> bool:
    return key in HIDDEN_SOURCES or (isinstance(ch, dict) and ch.get("source") in HIDDEN_SOURCES)


# Safety hotfix (01/11): topics never published on the site, whatever the catalog emits. Adult / NSFW and known bot-spike pages.
# Matched on the lowercased label and on the Wikidata id. The catalog's own junk filter replaces most of this; keep the list short.
BLOCKED_LABELS = {".xxx", "xxx", "pornhub", "xvideos", "xhamster", "onlyfans", "chaturbate", "stripchat", "xnxx", "redtube",
                  "youporn", "brazzers", "pornography", "hentai", "main page"}
BLOCKED_PATTERN = re.compile(r"(?<![\w.])(?:porn\w*|xxx|nsfw|hentai|onlyfans|sex (?:tape|video)s?)(?!\w)", re.I)
BLOCKED_QIDS = {"Q5296"}  # Wikimedia main page


def _blocked(t: dict) -> bool:
    label = (t.get("label") or "").strip()
    return label.lower() in BLOCKED_LABELS or bool(BLOCKED_PATTERN.search(label)) or t.get("qid") in BLOCKED_QIDS


def public_topics(path: Path) -> str:
    """A trending*.json without the hidden sources: their channels and evidence are removed, a topic left with no channel is dropped,
    and a family no channel belongs to any more is dropped from `source_families`."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    kept = []
    for t in doc.get("topics", []):
        if _blocked(t):
            continue
        chans = t.get("channels") or {}
        left = {k: c for k, c in chans.items() if not _hidden_channel(k, c)}
        if chans and not left:
            continue
        if len(left) != len(chans):
            t["channels"] = left
            if isinstance(t.get("rising_channels"), list):
                t["rising_channels"] = [k for k in t["rising_channels"] if k in left]
            if isinstance(t.get("evidence"), list):
                t["evidence"] = [e for e in t["evidence"] if e.get("source") not in HIDDEN_SOURCES]
            if isinstance(t.get("source_families"), list) and "github" in chans:
                t["source_families"] = [f for f in t["source_families"] if f != "developer"]
        kept.append(t)
    doc["topics"] = kept
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"


def _write_hashed(dest: Path, name: str, doc: dict) -> tuple[str, int]:
    blob = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    hashed = _hashed(blob, name)
    (dest / hashed).write_bytes(blob)
    return "/catalog-data/match/" + hashed, len(gzip.compress(blob, 9))


def stage_match(dest: Path, data: Path) -> dict | None:
    """The niche files (nicheclock/catalog/match_cmd.py) under catalog-data/match/ with a content hash in their names, so a browser
    keeps them for a year and a new build is a new URL. The names are listed in the published meta.json ("match"); the page reads
    them from there, and fetches a pool only when a visitor opens "For your niche". Adult and blocked titles are dropped again here,
    as for the trending files."""
    src = data / "match"
    if not (src / "manifest.json").is_file():
        return None
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    out = dest / "match"
    out.mkdir(parents=True)
    info: dict = {"date": manifest["date"], "regions": {}}
    for iso, st in manifest["regions"].items():
        doc = json.loads((src / f"pool-{iso}.json").read_text(encoding="utf-8"))
        doc["rows"] = [r for r in doc["rows"] if not _blocked({"label": r[1], "qid": f"Q{r[0]}"})]
        file, gz = _write_hashed(out, f"pool-{iso}.json", doc)
        info["regions"][iso] = {"file": file, "rows": len(doc["rows"]), "rising": st["rising"], "gzip_bytes": gz}
    doc = json.loads((src / "concepts.json").read_text(encoding="utf-8"))
    doc["rows"] = [r for r in doc["rows"] if not _blocked({"label": r[1], "qid": f"Q{r[0]}"})]
    file, gz = _write_hashed(out, "concepts.json", doc)
    info["concepts"] = {"file": file, "rows": len(doc["rows"]), "gzip_bytes": gz}
    for key, name in (("bridges", "bridges.json"), ("observances", "observances.json")):
        if (src / name).is_file():
            file, gz = _write_hashed(out, name, json.loads((src / name).read_text(encoding="utf-8")))
            info[key] = {"file": file, "gzip_bytes": gz}
    info["shards"] = stage_shards(dest, data, manifest)
    return info


def stage_shards(dest: Path, data: Path, manifest: dict) -> dict:
    """The same pool, concept index and events cut into shards of at most 150 KB (nicheclock/catalog/shard.py) for the Worker's profile
    tools, under catalog-data/shards/ with fixed names (only the Worker reads them, through the assets binding, so they are not cached
    as immutable). Blocked titles are dropped as for the files above. The page does not read them."""
    sys.path.insert(0, str(REPO))
    from nicheclock.catalog import shard
    src = data / "match"
    out = dest / "shards"
    ok = lambda qid, label: not _blocked({"label": label, "qid": f"Q{qid}"})      # noqa: E731
    concepts = json.loads((src / "concepts.json").read_text(encoding="utf-8"))
    label_of = {r[0]: r[1] for r in concepts.get("labels", [])}
    label_of.update({r[0]: r[1] for r in concepts["rows"]})
    info: dict = {"cap_bytes": shard.SHARD_CAP, "regions": {}}
    for iso in manifest["regions"]:
        doc = json.loads((src / f"pool-{iso}.json").read_text(encoding="utf-8"))
        rows = [r for r in doc["rows"] if ok(r[0], r[1])]
        m = shard.build_pool(iso, rows, label_of, out, doc.get("date", ""))
        info["regions"][iso] = {"rows": m["rows"], "shards": m["shards"], "max_shard_bytes": m["max_shard_bytes"]}
    m = shard.build_concepts([r for r in concepts["rows"] if ok(r[0], r[1])], out, concepts["date"])
    info["concepts"] = {"rows": m["rows"], "shards": m["shards"], "max_shard_bytes": m["max_shard_bytes"]}
    ev_rows: list = []
    try:
        upcoming = json.loads((data / "upcoming.json").read_text(encoding="utf-8")).get("events", [])
    except OSError:
        upcoming = []
    for ev in upcoming:
        n = ev.get("niche")
        if ev.get("qid") and n and ok(ev["qid"][1:], ev.get("title", "")):
            ev_rows.append([int(ev["qid"][1:]), ev["title"], 0, 0, n.get("h", []), n.get("f", 0), ["e", {k: v for k, v in ev.items() if k != "niche"}]])
    obs_meta: dict = {}
    if (src / "observances.json").is_file():
        obs = json.loads((src / "observances.json").read_text(encoding="utf-8"))
        obs_meta = {k: obs[k] for k in ("lag_days", "source", "license", "recurrence") if k in obs}
        for r in obs["rows"]:
            if ok(r[0], r[1]):
                ev_rows.append([r[0], r[1], 0, 0, r[6], r[7], ["o", r[2], r[3], r[4], r[5]]])
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    obs_meta["categories"] = {c["id"]: c["label"] for c in meta.get("categories", [])}
    m = shard.build_events(ev_rows, label_of, out, obs_meta)
    info["events"] = {"rows": m["rows"], "shards": m["shards"], "max_shard_bytes": m["max_shard_bytes"]}
    return info


def stage(out: Path, data: Path) -> dict[str, str]:
    """Write hashed css/js/font into out/assets and the JSON into out/catalog-data. Returns the hashed names."""
    for f in DATA_FILES:
        if not (data / f).is_file():
            sys.exit(f"{data}: missing {f}")
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}
    css = (HERE / "trending.css").read_bytes()
    for fname in ("google-sans-flex.woff2",):      # the one UI face, OFL, self-hosted; its hashed name is also preloaded by index_html
        blob = (HERE / "fonts" / fname).read_bytes()
        names[fname] = _hashed(blob, fname)
        (assets / names[fname]).write_bytes(blob)
        css = css.replace(f'url("{fname}")'.encode(), f'url("{names[fname]}")'.encode())
    names["css"] = _hashed(css, "trending.css")
    (assets / names["css"]).write_bytes(css)
    js = (HERE / "trending.js").read_bytes()
    names["js"] = _hashed(js, "trending.js")
    (assets / names["js"]).write_bytes(js)
    # the niche modules: niche.js imports match.js and profile.js by relative name, so those names are rewritten to the hashed ones
    for mod in ("match", "profile"):
        blob = (HERE / f"{mod}.js").read_bytes()
        names[mod] = _hashed(blob, f"{mod}.js")
        (assets / names[mod]).write_bytes(blob)
    niche = (HERE / "niche.js").read_text(encoding="utf-8")
    for mod in ("match", "profile"):
        if f'"./{mod}.js"' not in niche:
            sys.exit(f"niche.js does not import ./{mod}.js")
        niche = niche.replace(f'"./{mod}.js"', f'"./{names[mod]}"')
    names["niche"] = _hashed(niche.encode("utf-8"), "niche.js")
    (assets / names["niche"]).write_text(niche, encoding="utf-8")
    shutil.copy(HERE / "fonts" / "OFL-Google-Sans-Flex.txt", assets / "OFL-Google-Sans-Flex.txt")
    dest = out / "catalog-data"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    match_info = stage_match(dest, data)
    for f in data_files(data):
        if f.name == "meta.json":
            (dest / f.name).write_text(public_meta(f, match_info), encoding="utf-8")
        elif f.name.startswith("trending") and f.suffix == ".json":
            (dest / f.name).write_text(public_topics(f), encoding="utf-8")
        elif f.name == "github-rank.json" and "gharchive" in HIDDEN_SOURCES:
            continue       # the GitHub rank tab stays hidden with the hidden sources
        else:
            shutil.copy(f, dest / f.name)
    return names


CLOCK_MARK = ('<svg class="mark" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true" focusable="false">'
              '<circle cx="12" cy="12" r="9.5"/><path d="M12 6v6.2l3.8 2.3" stroke-linecap="square"/></svg>')

PRERENDER_ROWS = 20
PRERENDER_EVENTS = 10
CAP_RATIO = 11            # same cap as trending.js: "+1,000%+" from 11x


def _pct(ratio: float) -> str:
    if ratio >= CAP_RATIO:
        return "+1,000%+"
    return f"+{round((ratio - 1) * 100):,}%" if ratio >= 1 else f"\u2212{round((1 - ratio) * 100)}%"


def _compact(n: float) -> str:
    a = abs(n)
    for div, suffix in ((1e6, "M"), (1e3, "K")):
        if a >= div:
            v = n / div
            return (f"{v:.0f}" if v >= 100 or v == int(v) else f"{v:.1f}".rstrip("0").rstrip(".")) + suffix
    return str(round(n))


def _unit(u: str) -> str:
    if u.startswith("pageviews/day"):
        return "views/day"
    if u.startswith("HN points"):
        return "points"
    return (u.split(" (")[0] + (" est." if "estimated" in u else "")).strip()


def _rise(t: dict, window: str = "7d") -> str | None:
    """The row's Rise for one window, the same rule as trending.js riseInfo: the largest ratio among the rising channels that have a baseline."""
    best = None
    for k in t.get("rising_channels") or []:
        c = (t.get("channels") or {}).get(k) or {}
        w = (c.get("windows") or {}).get(window)
        if c.get("baseline", 0) > 0 and w and isinstance(w.get("ratio"), (int, float)):
            best = w["ratio"] if best is None else max(best, w["ratio"])
    return None if best is None else _pct(best) + (f" (\u00D7{best:.0f})" if best >= CAP_RATIO else "")


def prerender_html(public_data: Path, today: str | None = None) -> str:
    """A real <table> of today's top Worldwide rising rows and the next dated events, from the PUBLISHED data files (so the hidden
    sources are already gone), written into the static HTML for readers and crawlers that do not run JavaScript. trending.js
    replaces the whole container once it has the data."""
    import datetime as dt
    e = html.escape
    meta = json.loads((public_data / "meta.json").read_text(encoding="utf-8"))
    topics = json.loads((public_data / "trending.json").read_text(encoding="utf-8")).get("topics", [])
    events = json.loads((public_data / "upcoming.json").read_text(encoding="utf-8")).get("events", [])
    label = {x.get("id"): x.get("label") for x in meta.get("sources", [])}
    today = today or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for t in topics:                                    # the catalog file is already sorted by relevance
        if t.get("trend") != "rising" or not (t.get("windows") or {}).get("7d"):
            continue
        rise = _rise(t)
        if rise is None:
            continue
        vol = t.get("volume")
        rows.append(f'<tr><th scope="row">{e(t["label"])}</th><td>{e(t.get("category_label") or t.get("category") or "")}</td>'
                    f'<td>{e(rise)}</td><td>{e(_compact(vol) + " " + _unit(t.get("volume_unit") or "")) if isinstance(vol, (int, float)) else ""}</td></tr>')
        if len(rows) == PRERENDER_ROWS:
            break
    ev = sorted((x for x in events if x.get("date", "") >= today and x.get("publish_by", "") >= today and x.get("kind") != "holiday"),
                key=lambda x: (x["date"], x["title"]))[:PRERENDER_EVENTS]
    day = meta.get("date") or (meta.get("generated_at") or "")[:10]
    out = [f'<section id="prerender" aria-labelledby="pr-h"><h2 id="pr-h">Rising now, worldwide (data for {e(day)})</h2>',
           f'<table class="trend pre"><caption>The top {len(rows)} rising topics today. Rise is the 7-day window against the topic\'s own median of the 14 days before, capped at +1,000%+. '
           'Volume is the latest daily value in its own unit; none of it is search volume.</caption>'
           '<thead><tr><th scope="col">Topic</th><th scope="col">Category</th><th scope="col">Rise (7d)</th><th scope="col">Volume</th></tr></thead>'
           f'<tbody>{"".join(rows)}</tbody></table>']
    if ev:
        out.append('<h2>Upcoming events and when to publish</h2><table class="trend pre"><caption>The next dated events with a publish-by date (the event date minus a default lead time per kind of event).</caption>'
                   '<thead><tr><th scope="col">Event</th><th scope="col">Date</th><th scope="col">Publish by</th><th scope="col">Source</th></tr></thead><tbody>'
                   + "".join(f'<tr><th scope="row">{e(x["title"])}</th><td>{e(x["date"])}</td><td>{e(x["publish_by"])}</td><td>{e(label.get(x.get("source")) or x.get("source") or "")}</td></tr>' for x in ev)
                   + "</tbody></table>")
    out.append('<p class="dim small">This summary is the page as served; the full interactive table loads below it when JavaScript is available.</p></section>')
    return "".join(out)


def index_html(names: dict[str, str], noindex: bool = True, canonical: str = "https://nicheclock.elmrank.com/",
               beacon: str | None = None, repo_note: str = "Source code: coming soon.", prerender: str = "",
               repo_url: str | None = None) -> str:
    """The homepage. `noindex=False` makes it the one indexable URL. `beacon` is the hashed b.js name (first-party page counts)."""
    e = html.escape
    ld = json.dumps({
        "@context": "https://schema.org", "@type": "SoftwareApplication", "name": "nicheclock", "url": canonical,
        "applicationCategory": "DeveloperApplication", "description": DESC, "license": "https://www.gnu.org/licenses/agpl-3.0.html",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }, separators=(",", ":"))
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<meta name="color-scheme" content="light dark">\n'
        f"<title>{e(TITLE)}</title>\n<meta name=\"description\" content=\"{e(DESC)}\">\n"
        + ('<meta name="robots" content="noindex,nofollow">\n' if noindex else '<meta name="robots" content="index,follow">\n')
        + f'<link rel="canonical" href="{e(canonical)}">\n<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f'<meta property="og:site_name" content="nicheclock"><meta property="og:type" content="website">'
        f'<meta property="og:title" content="{e(TITLE)}"><meta property="og:description" content="{e(DESC)}">'
        f'<meta property="og:url" content="{e(canonical)}"><meta name="twitter:card" content="summary">\n'
        f'<script type="application/ld+json">{ld}</script>\n'
        f'<link rel="preload" href="/assets/{names["google-sans-flex.woff2"]}" as="font" type="font/woff2" crossorigin>\n'
        f'<link rel="stylesheet" href="/assets/{names["css"]}">\n<script src="/assets/{names["js"]}" defer></script>\n'
        + (f'<script src="/assets/{beacon}" defer></script>\n' if beacon else "")
        + '</head>\n<body>\n<a class="skip" href="#app">Skip to the table</a>\n'
        f'<header class="bar"><div class="bar-in"><a class="brand" href="/">{CLOCK_MARK}<span>nicheclock</span></a>'
        '<nav class="bar-links" aria-label="Documentation"><a class="mybtn" id="nichebtn" href="#/niche">Your niche</a><a href="/docs/">Docs</a><a href="/docs/mcp/">MCP</a><a href="/docs/sources/">Sources</a>'
        + github_link(repo_url) + '</nav></div></header>\n'
        f'<section class="hero"><div class="hero-in"><div class="hero-text"><h1>{e(TAGLINE)}</h1><p class="sub">{e(SUBLINE)}</p></div></div></section>\n'
        f'<main id="app" tabindex="-1" aria-live="polite" data-src="/catalog-data/" data-niche="/assets/{names["niche"]}" data-unit-note="{e(UNIT_NOTE)}"{" data-prerender" if prerender else ""}>\n'
        + (prerender if prerender else '<p class="state">Loading data…</p>') + '\n'
        '<noscript><p class="state">The interactive table needs JavaScript. The data is plain JSON: <a href="/catalog-data/trending.json">/catalog-data/trending.json</a>.</p></noscript>\n</main>\n'
        '<section class="about" id="about"><h2>What nicheclock is</h2>'
        '<p>nicheclock is a free trend radar under the AGPL-3.0 licence. It reads open data (Wikipedia pageviews, news counts from GDELT, Hacker News '
        'and dated event calendars) and lists the topics that are rising, each with the source, the licence and the unit it was measured in.</p>'
        '<h2>What it is for</h2>'
        '<p>It helps SEO and content people, and the AI agents they work with, decide what to write about and by when. '
        'The local MCP server answers four questions from your own data: which topics are rising in a niche, which dated events are coming and when to publish, '
        'which Search Console queries are gaps, and what the evidence behind a topic is.</p>'
        '<h2>What it is not</h2>'
        '<p>It is not search volume and it does not replace Google Trends or a keyword tool. A Wikipedia reader count, a news article count and a Hacker News point total are different things, '
        'so the table never adds them together. Country lists for small countries are thin and say so.</p>'
        '<h2>MCP and API</h2>'
        '<p>The local MCP server speaks stdio. There is no hosted API and no API key sign-up yet, and the Trending now table above is not available through MCP or an API yet.</p>'
        f'<p class="dim small">{repo_note} <a href="/docs/limitations/">What it does not do yet</a>.</p></section>\n'
        '<footer id="foot"><div id="foot-sources"></div><p>nicheclock is free software under AGPL-3.0. '
        '<a href="/privacy/">Privacy</a> · <a href="/terms/">Terms</a></p></footer>\n</body>\n</html>\n'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "dist")
    ap.add_argument("--data", type=Path, default=None)
    args = ap.parse_args()
    data = args.data or default_data()
    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True)
    names = stage(args.out, data)
    shutil.copy(SITE_DIR / "static" / "favicon.svg", args.out / "favicon.svg")
    (args.out / "index.html").write_text(index_html(names), encoding="utf-8")
    print(f"built Trending now -> {args.out} (data: {data})")


if __name__ == "__main__":
    main()

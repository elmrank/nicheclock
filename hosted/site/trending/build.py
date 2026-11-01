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
import hashlib
import html
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE_DIR = HERE.parent
REPO = HERE.parents[2]
CATALOG = SITE_DIR / "catalog-data"
FIXTURE = HERE / "fixture"
DATA_FILES = ("meta.json", "trending.json", "upcoming.json")

# Copy: option C of docs/nicheclock-geo-plan-2026-11-01.md (Chief 01/11). It says only what is true today: MCP runs locally from the repository.
TITLE = "nicheclock: rising topics and when to publish them"
DESC = ("Rising topics from Wikipedia, news and community channels, upcoming events with a publish-by date, "
        "and a table that keeps each volume in its own unit.")
TAGLINE = "Rising topics, dated events, and when to publish."
SUBLINE = ("nicheclock follows what is rising on Wikipedia, news and community channels and gives each upcoming event a publish-by date. "
           "The web table is for reading; AI agents get niche trends and events over MCP, which runs locally from the repository.")

# Sources kept off the live site until their terms are re-read (Chief 01/11). Matched against a channel's key, its `source`, an
# evidence item's `source` and the ids in meta.json. "github" is the stars/day channel and the GitHub rank tab, both counted from GH Archive.
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


def public_meta(path: Path) -> str:
    """meta.json as published: only the sources the catalog uses and the live site shows. The `excluded` and `planned` entries hold
    internal decisions and a roadmap; HIDDEN_SOURCES are kept off the site until their terms are re-read."""
    meta = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(meta.get("sources"), list):
        meta["sources"] = [x for x in meta["sources"] if (not x.get("status") or x.get("status") == "used") and x.get("id") not in HIDDEN_SOURCES]
    if isinstance(meta.get("source_filter"), list):
        meta["source_filter"] = [x for x in meta["source_filter"] if x.get("key") not in HIDDEN_SOURCES]
    return json.dumps(meta, ensure_ascii=False, indent=1) + "\n"


def _hidden_channel(key: str, ch: dict) -> bool:
    return key in HIDDEN_SOURCES or (isinstance(ch, dict) and ch.get("source") in HIDDEN_SOURCES)


def public_topics(path: Path) -> str:
    """A trending*.json without the hidden sources: their channels and evidence are removed, a topic left with no channel is dropped,
    and a family no channel belongs to any more is dropped from `source_families`."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    kept = []
    for t in doc.get("topics", []):
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


def stage(out: Path, data: Path) -> dict[str, str]:
    """Write hashed css/js/font into out/assets and the JSON into out/catalog-data. Returns the hashed names."""
    for f in DATA_FILES:
        if not (data / f).is_file():
            sys.exit(f"{data}: missing {f}")
    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    names: dict[str, str] = {}
    css = (HERE / "trending.css").read_bytes()
    for fname in ("roboto.woff2", "google-sans-flex.woff2"):      # OFL fonts measured on Google Trends, self-hosted
        blob = (HERE / "fonts" / fname).read_bytes()
        names[fname] = _hashed(blob, fname)
        (assets / names[fname]).write_bytes(blob)
        css = css.replace(f'url("{fname}")'.encode(), f'url("{names[fname]}")'.encode())
    names["css"] = _hashed(css, "trending.css")
    (assets / names["css"]).write_bytes(css)
    js = (HERE / "trending.js").read_bytes()
    names["js"] = _hashed(js, "trending.js")
    (assets / names["js"]).write_bytes(js)
    for lic in ("OFL-Roboto.txt", "OFL-Google-Sans-Flex.txt"):
        shutil.copy(HERE / "fonts" / lic, assets / lic)
    dest = out / "catalog-data"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for f in data_files(data):
        if f.name == "meta.json":
            (dest / f.name).write_text(public_meta(f), encoding="utf-8")
        elif f.name.startswith("trending") and f.suffix == ".json":
            (dest / f.name).write_text(public_topics(f), encoding="utf-8")
        elif f.name == "github-rank.json" and "gharchive" in HIDDEN_SOURCES:
            continue       # stars and forks counted from GH Archive: the GitHub rank tab stays hidden with it
        else:
            shutil.copy(f, dest / f.name)
    return names


def index_html(names: dict[str, str], noindex: bool = True, canonical: str = "https://nicheclock.elmrank.com/",
               beacon: str | None = None, repo_note: str = "Source code: coming soon.") -> str:
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
        f'<link rel="stylesheet" href="/assets/{names["css"]}">\n<script src="/assets/{names["js"]}" defer></script>\n'
        + (f'<script src="/assets/{beacon}" defer></script>\n' if beacon else "")
        + '</head>\n<body>\n'
        '<header class="bar"><div><strong class="brand">nicheclock</strong>'
        '<nav class="bar-links" aria-label="Documentation"><a href="/docs/">Docs</a><a href="/docs/mcp/">MCP</a><a href="/docs/sources/">Sources</a></nav></div></header>\n'
        f'<section class="hero"><h1>{e(TAGLINE)}</h1><p class="sub">{e(SUBLINE)}</p>'
        '<p class="dim small">MCP runs locally from the source repository, over stdio. There is no hosted API and no API key sign-up yet, '
        'and the Trending now table below is not available through MCP or an API yet.</p></section>\n'
        f'<main id="app" aria-live="polite" data-src="/catalog-data/" data-unit-note="{e(UNIT_NOTE)}">\n<p class="state">Loading data…</p>\n'
        '<noscript><p class="state">This page draws its table with JavaScript. The data is plain JSON: <a href="/catalog-data/trending.json">/catalog-data/trending.json</a>.</p></noscript>\n</main>\n'
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

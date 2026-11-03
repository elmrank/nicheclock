#!/usr/bin/env python3
"""Build the nicheclock site into public/ (the Workers static-assets directory).

    uv run -q --with markdown-it-py python hosted/site/build.py [--docs DIR]

Inputs
  content/*.html   hand-written pages: the landing page, /privacy/, /terms/ and the 404 page.
  <repo>/docs/*.md the documentation, single source of truth for the README and for /docs/ (owned by the docs branch).
  static/*         stylesheet, beacon script, robots.txt, _headers, favicon (stylesheet and script get a content hash).

content/<name>.html starts with `key: value` lines, a line `---`, then an HTML fragment:
    path: /privacy/    title: ...    description: ...    section: home | legal | error    full: true (title is complete)
docs/<name>.md starts with YAML front matter between `---` lines:
    title: (<= 45 chars, sentence case)   description: (100-160 chars)   order: int   section: start|run|connect|reference
    nav: optional short sidebar label     publish: false hides a file that has a title
    jsonld: optional path (relative to docs/) of a JSON-LD file, written into the page head
A docs file is published when its front matter has a `title`. URL: /docs/<name>/ (index.md is /docs/). Links between
docs are relative (`[x](docker.md#top)`) and are rewritten; images are `img/x.png`, copied to /docs/img/.
A blockquote that starts with `**Status.**` or `**Coming soon:**` is drawn as a note box.

The build refuses to finish when a page lacks a single <h1>, a title or description is out of range, an em or en dash
is in the prose, or an internal link or image does not resolve. Indexing: only the paths in INDEXABLE are indexable (see the comment there). REPO_PUBLIC and CONTACT_LIVE switch the footer lines.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the report page lives in the package (nicheclock/output/app/)
from nicheclock.output import page as nc_page  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ghmark import github_link  # noqa: E402  (shared with site/trending/build.py)
import google_updates  # noqa: E402  (the /google-updates/ page, site/google_updates.py)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SITE = "https://nicheclock.elmrank.com"
NAME = "nicheclock"
UPDATED = "2026-10-31"
PILOT = False     # True = banner "private pilot" on every docs/legal page
REPO_PUBLIC = True    # False prints "Source code: coming soon" instead of the link to REPO_URL
REPO_URL = "https://github.com/elmrank/nicheclock"
CONTACT_LIVE = False  # True once nicheclock@elmrank.com exists (launch prep); until then pages say "Contact: coming soon"
CONTACT = "nicheclock@elmrank.com"
# Indexing rule: the homepage first, then one more URL every 2 days. Every other page carries <meta robots noindex> and the
# Worker adds X-Robots-Tag: noindex (src/index.ts INDEXABLE_PATHS must list the same paths). The sitemap lists INDEXABLE only.
# Opening a URL later: add its path here AND in src/index.ts, rebuild, deploy.
INDEXABLE = {"/", "/docs/what-is-nicheclock/"}
NOINDEX_META = '<meta name="robots" content="noindex,nofollow">'
INDEX_META = '<meta name="robots" content="index,follow">'


def robots_meta(path: str) -> str:
    return INDEX_META if path in INDEXABLE else NOINDEX_META

REQUIRED_DOCS = ["index", "quickstart", "configuration", "models", "mcp", "api", "sources"]
SECTIONS = [("start", "Start"), ("run", "Run"), ("connect", "Connect"), ("reference", "Reference")]
NAV = [("/docs/", "Docs"), ("/docs/api/", "API"), ("/docs/mcp/", "MCP"), ("/docs/sources/", "Sources")]
FOOT = [("/docs/", "Docs"), ("/privacy/", "Privacy"), ("/terms/", "Terms")]

CUR = ' aria-current="page"'
LOGO = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9.5"/><path d="M12 6v6.2l3.8 2.3" stroke="var(--accent)" stroke-linecap="square"/></svg>'
)
MD = MarkdownIt("commonmark", {"html": True}).enable("table")


def front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, m.group(2)


def parse_html_page(path: Path) -> dict:
    head, sep, body = path.read_text(encoding="utf-8").partition("\n---\n")
    if not sep:
        sys.exit(f"{path.name}: no '---' line after the header block")
    meta = dict((k.strip(), v.strip()) for k, _, v in (l.partition(":") for l in head.splitlines() if l.strip()))
    meta.update(body=body.strip() + "\n", file=path.name)
    return meta


def slug(text: str) -> str:
    """GitHub-style heading id (the docs' own link test uses the same rule): lowercase, tags stripped, punctuation removed, spaces to '-'."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", text)).lower()
    return re.sub(r"[^\w\- ]", "", plain).strip().replace(" ", "-")


def doc_url(name: str) -> str:
    return "/docs/" if name == "index" else f"/docs/{name}/"


def post_process(body: str, doc_names: set[str], file: str, problems: list[str]) -> str:
    """Fix links and images from the docs, give headings ids, mark notes and narrow table cells."""

    def link(m: re.Match) -> str:
        target, anchor = m.group(1), m.group(2) or ""
        name = target[:-3]
        if name not in doc_names:
            problems.append(f"{file}: link to {target} which is not a published doc")
            return m.group(0)
        return f'href="{doc_url(name)}{anchor}"'

    body = re.sub(r'href="(?!https?:|/|#|mailto:)([A-Za-z0-9_\-]+\.md)(#[^"]*)?"', link, body)
    body = re.sub(r'(src|href)="(?:\./)?img/([^"]+)"', r'\1="/docs/img/\2"', body)
    body = re.sub(r'<code class="language-(\S+)[^"]*">', r'<code class="language-\1">', body)
    seen: dict[str, int] = {}

    def heading(m: re.Match) -> str:
        s = slug(m.group(3)) or "section"
        seen[s] = seen.get(s, 0) + 1
        sid = s if seen[s] == 1 else f"{s}-{seen[s]}"
        return f'<h{m.group(1)}{m.group(2)} id="{sid}">{m.group(3)}</h{m.group(1)}>'

    body = re.sub(r"<h([2-4])([^>]*)>(.*?)</h\1>", lambda m: heading(m) if "id=" not in m.group(2) else m.group(0), body, flags=re.S)
    body = re.sub(r"<blockquote>\s*<p>(<strong>(?:Status\.|Coming soon:)[^<]*</strong>)", r'<blockquote class="note"><p>\1', body)
    body = body.replace('<blockquote class="note">', '<div class="note">').replace("</blockquote>", "</div>")

    def cells(m: re.Match) -> str:
        row = m.group(0)
        first = re.search(r"<td>(.*?)</td>", row, re.S)
        if first and len(re.sub(r"<[^>]+>", "", html.unescape(first.group(1)))) <= 28:
            row = row.replace("<td>", '<td class="n">', 1)
        return row

    return re.sub(r"<tr>.*?</tr>", cells, body, flags=re.S)


def load_jsonld(docs_dir: Path, rel: str | None) -> str:
    """The JSON-LD of a docs page (front matter `jsonld: geo/x.json`, relative to docs/): checked as JSON, written on one line, safe inside <script>."""
    if not rel:
        return ""
    data = json.loads((docs_dir / rel).read_text(encoding="utf-8"))
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def load_docs(docs_dir: Path) -> list[dict]:
    docs = []
    for f in sorted(docs_dir.glob("*.md")):
        meta, text = front_matter(f.read_text(encoding="utf-8"))
        if "title" not in meta or meta.get("publish", "true").lower() == "false":
            continue
        docs.append({
            "name": f.stem, "path": doc_url(f.stem), "title": meta["title"], "description": meta.get("description", ""),
            "order": int(meta.get("order", 999)), "group": meta.get("section", ""), "nav": meta.get("nav", "Overview" if f.stem == "index" else meta["title"]),
            "section": "docs", "file": f.name, "md": text, "updated": meta.get("updated", UPDATED), "jsonld": load_jsonld(docs_dir, meta.get("jsonld")),
        })
    return sorted(docs, key=lambda d: (d["order"], d["name"]))


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def header(cur: str) -> str:
    links = "".join(
        f'<a href="{href}"{CUR if cur == href or (href == "/docs/" and cur.startswith("/docs/") and cur not in {h for h, _ in NAV}) else ""}>{label}</a>'
        for href, label in NAV
    )
    banner = ""
    if PILOT:
        banner = ('<div class="pilot"><div class="wrap">Private pilot. This site is not indexed '
                  "and public API keys are not open.</div></div>")
    return (
        f'<a class="skip" href="#main">Skip to content</a>{banner}'
        f'<header class="site-head"><div class="wrap"><a class="brand" href="/">{LOGO}{NAME}</a>'
        f'<nav aria-label="Main">{links}{github_link(REPO_URL if REPO_PUBLIC else None)}</nav></div></header>'
    )


def source_note() -> str:
    return f'Source code: <a href="{REPO_URL}">github.com/elmrank/nicheclock</a>.' if REPO_PUBLIC else "Source code: coming soon."


def footer() -> str:
    links = "".join(f'<a href="{h}">{l}</a>' for h, l in FOOT)
    return (
        '<footer class="site-foot"><div class="wrap">'
        f"<p>{NAME} is free software under AGPL-3.0. {source_note()}</p>"
        f'<nav class="links" aria-label="Footer">{links}</nav></div></footer>'
    )


def sidebar(cur: str, docs: list[dict]) -> str:
    def items(ds: list[dict]) -> str:
        return "".join(f'<li><a href="{d["path"]}"{CUR if d["path"] == cur else ""}>{esc(d["nav"])}</a></li>' for d in ds)

    known = {k for k, _ in SECTIONS}
    groups = [("", [d for d in docs if d["group"] not in known])] if any(d["group"] not in known for d in docs) else []
    groups += [(label, [d for d in docs if d["group"] == key]) for key, label in SECTIONS]
    out = ""
    for label, ds in groups:
        if ds:
            out += (f"<strong>{label}</strong>" if label else "") + f"<ul>{items(ds)}</ul>"
    return f'<nav class="side" aria-label="Documentation">{out}</nav>'


def pager(cur: str, docs: list[dict]) -> str:
    i = [d["path"] for d in docs].index(cur)
    prev = f'<a href="{docs[i-1]["path"]}">&larr; {esc(docs[i-1]["nav"])}</a>' if i > 0 else "<span></span>"
    nxt = f'<a href="{docs[i+1]["path"]}">{esc(docs[i+1]["nav"])} &rarr;</a>' if i + 1 < len(docs) else "<span></span>"
    return f'<div class="pager">{prev}{nxt}</div>'


def render(page: dict, css: str, js: str, docs: list[dict]) -> str:
    url = SITE + page["path"]
    title = page["title"] if page.get("full") == "true" else f'{page["title"]} | {NAME}'
    robots = robots_meta(page["path"])
    ld = f'<script type="application/ld+json">{page["jsonld"]}</script>' if page.get("jsonld") else ""
    err = page["section"] == "error"
    canonical = "" if err else f'<link rel="canonical" href="{url}">'
    og = "" if err else (
        f'<meta property="og:site_name" content="{NAME}"><meta property="og:type" content="website">'
        f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(page["description"])}">'
        f'<meta property="og:url" content="{url}"><meta name="twitter:card" content="summary">'
    )
    head = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title>"
        f'<meta name="description" content="{esc(page["description"])}">'
        f"{robots}{canonical}"
        '<meta name="color-scheme" content="light dark">'
        f"{og}{ld}"
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
        f'<link rel="stylesheet" href="/assets/{css}">'
        f'<script src="/assets/{js}" defer></script></head><body>'
    )
    sec = page["section"]
    if sec == "docs":
        main = (f'<div class="wrap docs">{sidebar(page["path"], docs)}'
                f'<main class="doc" id="main">{page["body"]}{pager(page["path"], docs)}</main></div>')
    elif sec == "legal":
        main = f'<div class="wrap docs"><main class="doc full" id="main">{page["body"]}</main></div>'
    elif sec == "error":
        main = f'<main class="wrap nf" id="main">{page["body"]}</main>'
    else:
        main = f'<main id="main">{page["body"]}</main>'
    return f"{head}{header(page['path'])}{main}{footer()}</body></html>\n"


def check(page: dict) -> list[str]:
    errs = []
    body = page["body"]
    n = len(re.findall(r"<h1[ >]", body))
    if n != 1:
        errs.append(f"{n} <h1> (need exactly 1)")
    title = page["title"] if page.get("full") == "true" else f'{page["title"]} | {NAME}'
    if not 15 <= len(title) <= 65:
        errs.append(f"title is {len(title)} characters (15-65): {title!r}")
    d = len(page["description"])
    if page["section"] != "error" and not 100 <= d <= 160:
        errs.append(f"description is {d} characters (100-160)")
    text = re.sub(r"<(pre|code)\b.*?</\1>", "", body, flags=re.S)
    if re.search("[\u2013\u2014]", text + page["title"] + page["description"]):
        errs.append("em/en dash in the prose (humanizer rule)")
    for h in re.findall(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text, re.S):
        plain = re.sub(r"<[^>]+>", "", h).strip()
        big = [w for w in plain.split() if len(w) > 3]
        if len(big) >= 3 and all(w[0].isupper() for w in big):
            errs.append(f"WARN heading looks Title Cased: {plain!r}")
    return errs


HOME_TITLE = "nicheclock: trend radar demo on sample niches"
HOME_DESC = ("Live demo of nicheclock, a self-hosted trend radar for SEOs who work with AI: rising topics, publish-by "
             "dates and content gaps for sample niches.")
INSTALL = "pipx install '.[gsc,mcp]'"


def home_page(names: dict[str, str], path: str = "/") -> str:
    """The homepage is the app: the report page of `nicheclock serve` reading /demo-data/demo.json, under one thin top bar."""
    bar = (
        '<header class="bar"><div class="bar-top"><strong class="brand">nicheclock</strong>'
        '<span class="pitch">Early trend signals and publish-by dates for SEOs who work with AI, built from pageviews, '
        'news and event dates that often move before keyword volume. Live demo on <span data-niche-count>sample niches</span>.</span>'
        '<nav class="bar-links" aria-label="Documentation"><a href="/docs/">Docs</a><a href="/docs/api/">API</a>'
        '<a href="/docs/mcp/">MCP</a></nav></div>'
        f'<div class="bar-sub"><div class="install"><code id="install">{esc(INSTALL)}</code>'
        '<button class="btn" id="copy" type="button">Copy</button><span>from a source checkout, not on PyPI yet</span></div>'
        '<p id="stamp"></p></div></header>'
    )
    foot = (
        '<div id="foot-sources"></div>'
        f"<p>{NAME} is free software under AGPL-3.0. {source_note()} "
        '<a href="/privacy/">Privacy</a> · <a href="/terms/">Terms</a></p>'
    )
    head = (
        f'<meta name="description" content="{esc(HOME_DESC)}">'
        + robots_meta(path)
        + f'<link rel="canonical" href="{SITE}{path}">'
        f'<meta property="og:site_name" content="{NAME}"><meta property="og:type" content="website">'
        f'<meta property="og:title" content="{esc(HOME_TITLE)}"><meta property="og:description" content="{esc(HOME_DESC)}">'
        f'<meta property="og:url" content="{SITE}/"><meta name="twitter:card" content="summary">'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
    )
    return nc_page.page(
        title=HOME_TITLE, bar=bar, foot=foot, css=f"/assets/{names['app.css']}", js=f"/assets/{names['app.js']}", head=head,
        data_src="/demo-data/demo.json", stamp_fmt="Demo data, sample niches. Updated daily; last run {time} UTC.")


def build_google_updates(out: Path, data_dir: Path, names: dict[str, str], repo_note: str, repo_url: str | None) -> None:
    """/google-updates/ and /catalog-data/google-updates.json (read by the MCP tool search_updates), from data_dir/google-updates.json.
    The file is written by the operator's watcher and copied in by deploy-site.sh; without it neither exists (the deploy script then refuses).
    The page is noindex while its path is not in INDEXABLE, and it is never in the sitemap."""
    src = data_dir / "google-updates.json"
    if not src.is_file():
        print(f"no {src.name} in {data_dir}: /google-updates/ not built", file=sys.stderr)
        return
    try:
        data = google_updates.load(src)
    except google_updates.DataError as err:
        sys.exit(f"{src}: {err}")
    shutil.copy(src, out / "catalog-data" / "google-updates.json")
    import datetime as dt
    names = {**names, "updates_css": google_updates.stage_css(out / "assets")}
    page = google_updates.page_html(data, names, noindex=google_updates.PATH not in INDEXABLE, today=dt.datetime.now(dt.timezone.utc).date(),
                                    bar_links=github_link(repo_url), repo_note=repo_note)
    # the page checks of the docs (one h1, title and description length, no em or en dash in OUR prose); Google's titles in the tables are quotes and are not checked
    body = re.sub(r"<table\b.*?</table>", "", page.split("<body>", 1)[1], flags=re.S)
    problems = [f"google-updates: {m}" for m in check({"body": body, "title": google_updates.TITLE, "description": google_updates.DESC, "section": "docs"})
                if " WARN " not in m and not m.startswith("WARN")]
    if problems:
        sys.exit("\n".join(problems))
    target = out / "google-updates" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=Path, default=REPO / "docs")
    ap.add_argument("--home", choices=("report", "trending"), default="report",
                    help="report: the demo report page is the homepage (current). trending: the Trending now page is, and the report moves to /demo/")
    ap.add_argument("--catalog-data", type=Path, default=None, help="with --home trending: dir with meta.json, trending.json, upcoming.json (default: site/catalog-data, else the sample fixture)")
    args = ap.parse_args()

    out = HERE / "public"
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    static = HERE / "static"
    names = {}
    for f in ("site.css", "b.js"):
        data = (static / f).read_bytes()
        stem, _, ext = f.rpartition(".")
        hashed = f"{stem}.{hashlib.sha256(data).hexdigest()[:8]}.{ext}"
        (out / "assets" / hashed).write_bytes(data)
        names[f] = hashed
    app_names = {}         # the report page shared with `nicheclock serve`: font first, so the stylesheet can name its hashed file
    for f in ("source-sans-3.woff2", "app.css", "app.js"):
        data = (nc_page.DIR / f).read_bytes()
        if f == "app.css":
            data = data.replace(b'url("source-sans-3.woff2")', f'url("{app_names["source-sans-3.woff2"]}")'.encode())
        stem, _, ext = f.rpartition(".")
        app_names[f] = f"{stem}.{hashlib.sha256(data).hexdigest()[:8]}.{ext}"
        (out / "assets" / app_names[f]).write_bytes(data)
    shutil.copy(nc_page.DIR / "OFL-Source-Sans-3.txt", out / "assets" / "OFL-Source-Sans-3.txt")
    for f in ("robots.txt", "_headers", "favicon.svg"):
        shutil.copy(static / f, out / f)
    trending = None
    if args.home == "trending":
        sys.path.insert(0, str(HERE / "trending"))
        import build as trending      # site/trending/build.py
        trending_names = trending.stage(out, args.catalog_data or trending.default_data())
    if (args.docs / "img").is_dir():
        shutil.copytree(args.docs / "img", out / "docs" / "img")
    # The demo data is served as it is (no hashing, no page checks): the home page fetches /demo-data/demo.json.
    if (HERE / "demo-data").is_dir():
        shutil.copytree(HERE / "demo-data", out / "demo-data")

    problems: list[str] = []
    docs = load_docs(args.docs)
    doc_names = {d["name"] for d in docs}
    for d in docs:
        d["body"] = post_process(MD.render(d["md"]), doc_names, d["file"], problems)
    pages = [parse_html_page(p) for p in sorted((HERE / "content").glob("*.html"))]
    contact = f'Contact: <a href="mailto:{CONTACT}">{CONTACT}</a>.' if CONTACT_LIVE else "Contact: coming soon."
    for p in pages:
        p["body"] = p["body"].replace("{{CONTACT}}", contact)
        p["body"] = post_process(p["body"], doc_names, p["file"], problems)
    allpages = docs + pages
    urls = {p["path"] for p in allpages if p["section"] != "error"}
    urls.add("/")          # the app page, built from nicheclock/output/app/ (see home_page), or Trending now (--home trending)
    if trending:
        urls.add("/demo/")
    for want in REQUIRED_DOCS:
        if want not in doc_names:
            problems.append(f"missing doc {want}.md in {args.docs}")
    for want in ("/", "/privacy/", "/terms/"):
        if want not in urls:
            problems.append(f"missing page {want}")

    ids = {p["path"]: set(re.findall(r'id="([^"]+)"', p["body"])) for p in allpages}
    for page in allpages:
        for tgt, frag in re.findall(r'href="(/[^"#]*)#([^"]+)"', page["body"]):
            if tgt in ids and frag not in ids[tgt]:
                problems.append(f'{page["file"]}: anchor {tgt}#{frag} does not exist')
        for frag in re.findall(r'href="#([^"]+)"', page["body"]):
            if frag not in ids[page["path"]]:
                problems.append(f'{page["file"]}: anchor #{frag} does not exist')
        problems += [f'{page["file"]}: {e}' for e in check(page)]
        for href in re.findall(r'href="(/[^"#]*)', page["body"]):
            if not href.startswith("/assets/") and href not in urls and href not in ("/keys", "/favicon.svg"):
                problems.append(f'{page["file"]}: internal link {href} does not resolve')
        for src in re.findall(r'src="(/docs/img/[^"]+)"', page["body"]):
            if not (out / src.lstrip("/")).is_file():
                problems.append(f'{page["file"]}: image {src} is not in docs/img/')
    warns = [p for p in problems if " WARN " in p]
    problems = [p for p in problems if " WARN " not in p]
    if warns:
        print("\n".join(dict.fromkeys(warns)), file=sys.stderr)
    if problems:
        print("\n".join(dict.fromkeys(problems)), file=sys.stderr)
        sys.exit(1)

    for page in allpages:
        if page["section"] == "error":
            target = out / "404.html"
        else:
            target = out / (page["path"].strip("/") + "/index.html" if page["path"] != "/" else "index.html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(page, names["site.css"], names["b.js"], docs), encoding="utf-8")
    if trending:
        (out / "index.html").write_text(trending.index_html(trending_names, noindex="/" not in INDEXABLE, canonical=f"{SITE}/", beacon=names["b.js"], repo_note=source_note(), repo_url=REPO_URL if REPO_PUBLIC else None, prerender=trending.prerender_html(out / "catalog-data")), encoding="utf-8")
        (out / "demo").mkdir()
        (out / "demo" / "index.html").write_text(home_page(app_names, "/demo/"), encoding="utf-8")
    else:
        (out / "index.html").write_text(home_page(app_names), encoding="utf-8")
    if trending:
        build_google_updates(out, args.catalog_data or trending.default_data(), {**trending_names, "beacon": names["b.js"]}, source_note(), REPO_URL if REPO_PUBLIC else None)

    home_lastmod = UPDATED
    if trending:   # the homepage changes when the catalog does
        home_lastmod = json.loads((out / "catalog-data" / "meta.json").read_text(encoding="utf-8"))["generated_at"][:10]
    lastmods = {d["path"]: d["updated"] for d in docs}      # a docs page: its own `updated:` date
    rows = "".join(f"<url><loc>{SITE}{path}</loc><lastmod>{lastmods.get(path, home_lastmod)}</lastmod></url>" for path in sorted(INDEXABLE))
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + rows + "</urlset>\n",
        encoding="utf-8",
    )
    print(f"built {len(allpages)} pages ({len(docs)} from {args.docs}) -> {out}")


if __name__ == "__main__":
    main()

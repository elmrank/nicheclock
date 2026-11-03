"""The /google-updates/ page: is a Google core or spam update rolling out, the ranking updates of the last 12 months, and the
Search and Crawling documentation changes and Search Central blog posts.

Input: google-updates.json (schema 1), written by the operator's watcher (export_public.py) and copied into the build by
deploy-site.sh. It is the same file the hosted MCP tool `search_updates` reads (published at /catalog-data/google-updates.json).
`load` is strict: a field that is not in the allow-list below stops the build, so an internal field can never reach the page or the tool.

The page is static HTML (no script), noindex until it is opened (site/build.py INDEXABLE, src/index.ts INDEXABLE_PATHS).
There is no JSON-LD: none of the structured data types Google supports for rich results describes a list of updates.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = "/google-updates/"
TITLE = "Google Search updates: is one rolling out now?"
DESC = ("Is a Google core or spam update rolling out now? Every ranking update of the last 12 months, plus Search "
        "documentation changes and Search Central blog posts.")
H1 = "Google Search updates"
SUBLINE = "Core and spam updates, and what Google changed in its Search and Crawling documentation, from Google's own pages."
STALE_DAYS = 3          # the page says so when the data is older than this at build time

TOP_KEYS = {"schema", "data_date", "window", "attribution", "rolling", "ranking", "items"}
RANKING_KEYS = {"name", "type", "started", "ended", "duration_days", "url"}
ROLLING_KEYS = {"name", "type", "started", "url"}
ITEM_KEYS = {"date", "kind", "category", "title", "url", "summary"}
TYPES = {"core", "spam", "discover", "other"}
KINDS = {"docs", "blog"}
CATEGORIES = {"Spam policy", "Ranking update", "Structured data", "Search appearance", "Crawling", "Search Essentials", "Search Console",
              "AI features", "Discover", "Documentation", "Blog"}
URL_RE = re.compile(r"^https://(developers\.google\.com|status\.search\.google\.com)/")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ATTRIBUTION = "Source: Google Search Central, CC BY 4.0"


class DataError(ValueError):
    pass


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise DataError(msg)


def _date(v: object, what: str) -> str:
    _need(isinstance(v, str) and bool(DATE_RE.match(v)), f"{what}: not a YYYY-MM-DD date: {v!r}")
    dt.date.fromisoformat(v)    # type: ignore[arg-type]
    return v    # type: ignore[return-value]


def _text(v: object, what: str, max_len: int) -> str:
    _need(isinstance(v, str) and 0 < len(v.strip()) <= max_len, f"{what}: empty or longer than {max_len}")
    return v    # type: ignore[return-value]


def load(path: Path) -> dict:
    """Read and validate. Raises DataError (a message naming the field) on anything outside the schema."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DataError(f"{path}: {e}") from e
    _need(isinstance(d, dict) and set(d) == TOP_KEYS, f"top-level keys must be exactly {sorted(TOP_KEYS)}, got {sorted(d) if isinstance(d, dict) else type(d).__name__}")
    _need(d["schema"] == 1, f"schema {d['schema']!r} is not 1")
    _date(d["data_date"], "data_date")
    _need(isinstance(d["window"], dict) and set(d["window"]) == {"from", "to"}, "window must be {from, to}")
    _date(d["window"]["from"], "window.from")
    _date(d["window"]["to"], "window.to")
    _need(d["attribution"].get("text") == ATTRIBUTION if isinstance(d["attribution"], dict) else False, f"attribution.text must be {ATTRIBUTION!r}")
    _need(set(d["attribution"]) == {"text", "license_url", "policy_url"}, "attribution keys")
    for kind, rows, keys in (("ranking", d["ranking"], RANKING_KEYS), ("rolling", d["rolling"], ROLLING_KEYS), ("items", d["items"], ITEM_KEYS)):
        _need(isinstance(rows, list), f"{kind} must be a list")
        for i, r in enumerate(rows):
            _need(isinstance(r, dict) and set(r) == keys, f"{kind}[{i}] keys must be exactly {sorted(keys)}, got {sorted(r) if isinstance(r, dict) else type(r).__name__}")
            _need(bool(URL_RE.match(r["url"])) if isinstance(r["url"], str) else False, f"{kind}[{i}].url is not a Google URL")
    for i, r in enumerate(d["ranking"]):
        _text(r["name"], f"ranking[{i}].name", 120)
        _need(r["type"] in TYPES, f"ranking[{i}].type {r['type']!r}")
        s = _date(r["started"], f"ranking[{i}].started")
        if r["ended"] is None:
            _need(r["duration_days"] is None, f"ranking[{i}]: duration without an end date")
        else:
            e = _date(r["ended"], f"ranking[{i}].ended")
            _need(e >= s, f"ranking[{i}] ends before it starts")
            _need(r["duration_days"] == (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days, f"ranking[{i}].duration_days does not match the dates")
    # `rolling` is derived by the exporter (the one place the logic lives); here it is only checked against the ranking list
    expect = [{"name": r["name"], "type": r["type"], "started": r["started"], "url": r["url"]}
              for r in d["ranking"] if r["type"] in ("core", "spam") and r["ended"] is None]
    _need(d["rolling"] == expect, "rolling does not match the ranking entries that started and did not end")
    for i, r in enumerate(d["items"]):
        _date(r["date"], f"items[{i}].date")
        _need(r["kind"] in KINDS, f"items[{i}].kind {r['kind']!r}")
        _need(r["category"] in CATEGORIES, f"items[{i}].category {r['category']!r} is not a public category")
        _text(r["title"], f"items[{i}].title", 240)
        _need(isinstance(r["summary"], str) and len(r["summary"]) <= 260, f"items[{i}].summary too long")
    return d


# ---------- page ----------

e = html.escape


def fmt_days(n: int) -> str:
    return f"{n} day" + ("" if n == 1 else "s")


def banner(d: dict, today: dt.date) -> str:
    rolling = d["rolling"]
    core_spam = [r for r in d["ranking"] if r["type"] in ("core", "spam")]    # newest first
    if rolling:
        parts = []
        for r in rolling:
            parts.append(f'<strong>{e(r["name"])}</strong> started on {e(r["started"])}. Google has not published an end date, so it is still rolling out. '
                         f'<a href="{e(r["url"])}" rel="noopener">Status Dashboard entry</a>')
        answer = '<p class="gu-answer yes"><span class="gu-flag">Yes</span></p><p class="gu-detail">' + "</p><p class=\"gu-detail\">".join(parts) + "</p>"
    else:
        last = core_spam[0] if core_spam else None
        if last:
            detail = (f'The most recent one, the <strong>{e(last["name"])}</strong>, started on {e(last["started"])} and ended on {e(last["ended"])} '
                      f'(after {fmt_days(last["duration_days"])}). <a href="{e(last["url"])}" rel="noopener">Status Dashboard entry</a>')
        else:
            detail = "No core or spam update appears in the last 12 months."
        answer = f'<p class="gu-answer no"><span class="gu-flag">No</span></p><p class="gu-detail">{detail}</p>'
    age = (today - dt.date.fromisoformat(d["data_date"])).days
    stale = (f' <span class="gu-stale">This page was last updated with data from {e(d["data_date"])}, '
             f'{age} days ago, so a newer update may exist.</span>') if age >= STALE_DAYS else ""
    return (f'<section class="gu-banner{" rolling" if rolling else ""}" aria-labelledby="gu-q"><h2 id="gu-q">Is a Google core or spam update rolling out right now?</h2>'
            f'{answer}<p class="gu-meta">Read from the Google Search Status Dashboard on {e(d["data_date"])}. Spam and core updates only; '
            f'other ranking updates are in the table below.{stale}</p></section>')


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _axis(start: dt.date, end: dt.date) -> tuple[int, list[tuple[int, str]]]:
    """Day span of the window and the month starts inside it as (day offset, label). The month the window opens in is labelled at 0 when it
    opens part way through it (no grid line there)."""
    span = (end - start).days + 1
    ticks, y, m = [], start.year, start.month
    while True:
        first = dt.date(y, m, 1)
        if first > end:
            break
        if first >= start:
            ticks.append(((first - start).days, MONTHS[m - 1]))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return span, ticks


def timeline(d: dict, today: dt.date) -> str:
    start = dt.date.fromisoformat(d["window"]["from"])
    end = max(today, dt.date.fromisoformat(d["window"]["to"]))
    span, ticks = _axis(start, end)
    grid = "".join(f'<path d="M{x} 0V10"/>' for x, _ in ticks)
    labels = ([(-2, MONTHS[start.month - 1])] if start.day > 1 else []) + ticks
    axis = ('<svg class="gu-axis" viewBox="0 0 %d 14" role="img" aria-label="Months of the last 12 months">' % span
            + "".join(f'<text x="{x + 2}" y="10">{e(label)}</text>' for x, label in labels) + "</svg>")
    rows = []
    for r in d["ranking"]:
        s = (dt.date.fromisoformat(r["started"]) - start).days
        until = dt.date.fromisoformat(r["ended"]) if r["ended"] else end
        w = max((until - dt.date.fromisoformat(r["started"])).days, 1)
        ended = e(r["ended"]) if r["ended"] else '<span class="gu-open">still rolling</span>'
        dur = fmt_days(r["duration_days"]) if r["ended"] else "ongoing"
        bar = (f'<svg class="gu-bar {r["type"]}" viewBox="0 0 {span} 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
               f'<g class="grid">{grid}</g><rect x="{max(s, 0)}" y="2" width="{w}" height="6"/></svg>')
        rows.append(f'<tr><th scope="row"><a href="{e(r["url"])}" rel="noopener">{e(r["name"])}</a></th><td>{e(r["started"])}</td><td>{ended}</td>'
                    f'<td>{dur}</td><td class="gu-tl">{bar}</td></tr>')
    return ('<section aria-labelledby="gu-rank"><h2 id="gu-rank">Ranking updates, last 12 months</h2>'
            '<p class="gu-note">Each row is one update on the Google Search Status Dashboard: start date, end date and how long the rollout took. '
            'Core and spam updates are the accent and grey bars; the Discover update is lighter.</p>'
            '<div class="tablewrap"><table class="gu-table gu-rank"><caption class="sr">Ranking updates of the last 12 months, newest first</caption>'
            '<thead><tr><th scope="col" class="c-name">Update</th><th scope="col" class="c-d">Started</th><th scope="col" class="c-d">Ended</th>'
            f'<th scope="col" class="c-dur">Duration</th><th scope="col" class="gu-tl">{axis}</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div></section>")


def changes(d: dict) -> str:
    out, month = [], None
    for r in d["items"]:       # newest first
        if r["date"][:7] != month:
            month = r["date"][:7]
            out.append(f'<tr class="gu-month"><th colspan="4" scope="colgroup">{e(month)}</th></tr>')
        kind = "Blog" if r["kind"] == "blog" else "Docs"
        summary = f'<span class="gu-sum">{e(r["summary"])}</span>' if r["summary"] else ""
        out.append(f'<tr><td class="gu-date">{e(r["date"])}</td><td class="gu-main"><a class="gu-title" href="{e(r["url"])}" rel="noopener">{e(r["title"])}</a>{summary}</td>'
                   f'<td class="gu-cat">{e(r["category"])}</td><td class="gu-kind">{kind}</td></tr>')
    return ('<section aria-labelledby="gu-docs"><h2 id="gu-docs">Search and Crawling documentation changes and blog posts</h2>'
            f'<p class="gu-note">{len(d["items"])} changes since {e(d["window"]["from"])}, newest first. Each summary is Google\'s own wording, shortened. '
            'Docs are the Search and Crawling documentation changelogs; Blog is the Search Central blog.</p>'
            '<div class="tablewrap"><table class="gu-table gu-list"><caption class="sr">Search and Crawling documentation changes and Search Central blog posts, newest first</caption>'
            '<thead><tr><th scope="col" class="c-d">Date</th><th scope="col">Change</th><th scope="col" class="c-cat">Category</th><th scope="col" class="c-kind">From</th></tr></thead><tbody>'
            + "".join(out) + "</tbody></table></div></section>")


def attribution(d: dict) -> str:
    a = d["attribution"]
    return ('<section class="gu-attr" aria-labelledby="gu-src"><h2 id="gu-src">Sources and licence</h2>'
            f'<p>{e(a["text"])}. Portions of this page are modifications based on work created and <a href="https://developers.google.com/readme/policies" rel="noopener">shared by Google</a> '
            f'and used according to terms described in the <a href="{e(a["license_url"])}" rel="noopener">Creative Commons 4.0 Attribution License</a> '
            f'(<a href="{e(a["policy_url"])}" rel="noopener">Google developers site policies</a>). Titles and summaries come from the Search documentation changelog, '
            'the Crawling documentation changelog and the Search Central blog; every title links to the original page, which is the version to rely on.</p>'
            '<p>Update names, dates and links come from the <a href="https://status.search.google.com/" rel="noopener">Google Search Status Dashboard</a>. '
            'The dashboard states no licence, so only those facts are used and none of its text is copied.</p>'
            '<p>nicheclock is not affiliated with Google. The same data is available to agents through the <code>search_updates</code> tool of the '
            '<a href="/docs/mcp/#hosted-server">hosted MCP server</a>.</p></section>')


def page_html(d: dict, names: dict[str, str], noindex: bool, today: dt.date, bar_links: str, repo_note: str) -> str:
    """names: hashed asset names from site/trending/build.py stage() ("css", "google-sans-flex.woff2") plus "updates_css" and "beacon"."""
    url = "https://nicheclock.elmrank.com" + PATH
    robots = '<meta name="robots" content="noindex,nofollow">' if noindex else '<meta name="robots" content="index,follow">'
    clock = ('<svg class="mark" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true" focusable="false">'
             '<circle cx="12" cy="12" r="9.5"/><path d="M12 6v6.2l3.8 2.3" stroke-linecap="square"/></svg>')
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n<meta name="color-scheme" content="light dark">\n'
        f'<title>{e(TITLE)}</title>\n<meta name="description" content="{e(DESC)}">\n{robots}\n<link rel="canonical" href="{url}">\n'
        '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f'<meta property="og:site_name" content="nicheclock"><meta property="og:type" content="website"><meta property="og:title" content="{e(TITLE)}">'
        f'<meta property="og:description" content="{e(DESC)}"><meta property="og:url" content="{url}"><meta name="twitter:card" content="summary">\n'
        f'<link rel="preload" href="/assets/{names["google-sans-flex.woff2"]}" as="font" type="font/woff2" crossorigin>\n'
        f'<link rel="stylesheet" href="/assets/{names["css"]}">\n<link rel="stylesheet" href="/assets/{names["updates_css"]}">\n'
        + (f'<script src="/assets/{names["beacon"]}" defer></script>\n' if names.get("beacon") else "")
        + '</head>\n<body>\n<a class="skip" href="#gu">Skip to the content</a>\n'
        f'<header class="bar"><div class="bar-in"><a class="brand" href="/">{clock}<span>nicheclock</span></a>'
        f'<nav class="bar-links" aria-label="Documentation"><a href="/docs/">Docs</a><a href="/docs/mcp/">MCP</a><a href="/docs/sources/">Sources</a>{bar_links}</nav></div></header>\n'
        f'<section class="hero"><div class="hero-in"><div class="hero-text"><h1>{e(H1)}</h1><p class="sub">{e(SUBLINE)}</p></div></div></section>\n'
        f'<main id="gu" tabindex="-1">\n{banner(d, today)}\n{timeline(d, today)}\n{changes(d)}\n{attribution(d)}\n</main>\n'
        f'<footer id="foot"><p>nicheclock is free software under AGPL-3.0. {repo_note} <a href="/privacy/">Privacy</a> · <a href="/terms/">Terms</a></p></footer>\n</body>\n</html>\n'
    )


def stage_css(assets: Path) -> str:
    """Write the page stylesheet with a content hash in its name; returns the name."""
    blob = (HERE / "google-updates.css").read_bytes()
    name = f"google-updates.{hashlib.sha256(blob).hexdigest()[:8]}.css"
    (assets / name).write_bytes(blob)
    return name

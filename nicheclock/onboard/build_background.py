"""Maintainer tool: rebuild `data/background.json.gz`, the generic-English background for TF-IDF.

    python -m nicheclock.onboard.build_background --config nicheclock.yaml [--top 2400 --random 1200]

Sources (both are on the ARCHITECTURE.md allow-list, fetched through the shared client, 1 req/s):
  * Wikimedia REST pageviews "top" lists (en.wikipedia, last ~12 months) -> titles of the most-viewed articles;
  * MediaWiki API `prop=extracts` intros of those articles plus a batch of random articles.
Titles and the first sentences of each intro become "documents"; only aggregate document counts of
1-3-grams (df >= MIN_DF; single words from df >= 1) are written. No article text is shipped. Wikipedia text is CC BY-SA 4.0;
the counts are statistical facts derived from it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from .extract import BACKGROUND_FILE, analyze, tokenize

PAGEVIEWS_TOP = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{y}/{m:02d}/all-days"
WIKI_API = "https://en.wikipedia.org/w/api.php"
MIN_DF = 3
SENTENCES_PER_ARTICLE = 8
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def top_titles(client, months: int, want: int) -> tuple[list[str], list[str]]:
    """Most-viewed article titles over the last `months` published months, best rank first."""
    today = date.today()
    y, m = today.year, today.month
    best: dict[str, int] = {}
    used: list[str] = []
    tries = 0
    while len(used) < months and tries < months + 6:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        tries += 1
        resp = client.get(PAGEVIEWS_TOP.format(y=y, m=m))
        if resp.status_code != 200:
            continue
        used.append(f"{y}-{m:02d}")
        for item in resp.json()["items"][0]["articles"]:
            t = item["article"].replace("_", " ")
            if ":" in t or t == "Main Page":
                continue
            best[t] = min(best.get(t, 10**9), item["rank"])
    titles = sorted(best, key=lambda t: (best[t], t))[:want]
    return titles, used


def _extract_docs(pages: list[dict]) -> list[set[str]]:
    docs: list[set[str]] = []
    for pg in pages:
        title = pg.get("title", "")
        if title:
            docs.append(analyze(tokenize(title))[0])
        for sent in _SENT.split(pg.get("extract") or "")[:SENTENCES_PER_ARTICLE]:
            if len(sent) > 20:
                docs.append(analyze(tokenize(sent))[0])
    return [d for d in docs if d]


def _fetch_pages(client, **params) -> list[dict]:
    params.update(action="query", prop="extracts", exintro="1", explaintext="1", exlimit="20",
                  format="json", formatversion="2")
    # MediaWiki API: documented for automated use (API:Etiquette); robots.txt `Disallow: /w/` targets crawlers.
    resp = client.get(WIKI_API, params=params, robots=False)
    if resp.status_code != 200:
        return []
    return resp.json().get("query", {}).get("pages", [])


def build(client, *, top: int, random_n: int, months: int = 12, out: Path = BACKGROUND_FILE,
          log=print) -> dict:
    titles, used_months = top_titles(client, months, top)
    log(f"top titles: {len(titles)} from months {used_months[-1] if used_months else '?'}..{used_months[0] if used_months else '?'}")
    df: Counter = Counter()
    n_docs = 0
    n_articles = 0

    def add(pages: list[dict]) -> None:
        nonlocal n_docs, n_articles
        n_articles += len(pages)
        for d in _extract_docs(pages):
            n_docs += 1
            df.update(d)

    for i in range(0, len(titles), 20):
        add(_fetch_pages(client, titles="|".join(titles[i : i + 20])))
        if (i // 20) % 20 == 0:
            log(f"  top batch {i // 20 + 1}/{(len(titles) + 19) // 20}, docs {n_docs}")
    for i in range(0, random_n, 20):
        # `rnd` only differs per request so the client's per-URL memo does not replay the same random set.
        add(_fetch_pages(client, generator="random", grnnamespace="0", grnlimit="20", rnd=str(i)))
        if (i // 20) % 20 == 0:
            log(f"  random batch {i // 20 + 1}/{(random_n + 19) // 20}, docs {n_docs}")
    kept = {p: c for p, c in df.items() if c >= MIN_DF or " " not in p}  # every unigram: acts as an English word list
    meta = {
        "source": "en.wikipedia.org: titles + first sentences of intro extracts (MediaWiki API prop=extracts) "
                  "of the most-viewed articles (Wikimedia REST pageviews top) and random articles",
        "licence_of_source": "CC BY-SA 4.0 (Wikipedia text); only aggregate n-gram document frequencies are shipped",
        "terms": "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use, https://wikimedia.org/api/rest_v1/",
        "pageview_months": used_months,
        "articles": n_articles,
        "min_df": f"{MIN_DF} for 2-3-grams, 1 for single words",
        "ngram_max": 3,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tool": "python -m nicheclock.onboard.build_background",
        "format": "docs = number of documents; df = {phrase: number of documents containing it}",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"meta": meta, "docs": n_docs, "df": dict(sorted(kept.items()))},
                         ensure_ascii=False, separators=(",", ":"))
    with gzip.GzipFile(out, "wb", mtime=0) as f:
        f.write(payload.encode("utf-8"))
    log(f"wrote {out}: {n_docs} docs, {len(kept)} phrases, {out.stat().st_size // 1024} KiB")
    return meta


def main(argv: list[str] | None = None) -> int:
    from nicheclock.config import load_config, resolve_config_path
    from nicheclock.http import Client

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", "-c")
    ap.add_argument("--top", type=int, default=2400)
    ap.add_argument("--random", type=int, default=1200)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--out", default=str(BACKGROUND_FILE))
    a = ap.parse_args(argv)
    cfg = load_config(resolve_config_path(a.config))
    build(Client(cfg), top=a.top, random_n=a.random, months=a.months, out=Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

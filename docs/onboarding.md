---
title: How init works
description: What nicheclock init reads from your site, how it builds niches, keywords and entities, finds feeds, and what the preview and edit commands do.
order: 7
publish: true
---

# How init works

`nicheclock init <url> [<url> ...]` reads your own site(s) and writes an editable `nicheclock.yaml`. The default path is rule-based (TF-IDF over your URLs and titles); with a model configured, `--llm` lets it propose niches from your page titles, merged into the rule-based draft.

```
nicheclock init https://example.com --user-agent "mybot/1.0 (+mailto:me@example.com)" --contact-email me@example.com
nicheclock preview                     # what would match / be excluded, from the last 7 days of the store (or a live pull)
nicheclock edit                        # opens $EDITOR on a copy, validates, only then writes the file
nicheclock edit --niche birdwatching --add-keyword "bird feeder" --remove-exclude tips --add-rss https://blog.example/feed
```

## What `init` does

1. **crawl** (`onboard/crawl.py`): `robots.txt` (for `Sitemap:` lines), sitemap index / urlset / `.gz` (children for images, videos, tags, categories, authors are skipped; no sitemap falls back to the homepage's links), then `<title>` and first `<h1>` of at most `--max-pages` (default 50) pages, spread across URL sections. All requests go through the shared client: your identity UA, one request per second per host, robots.txt respected. Only your own site is read.
2. **extract** (`onboard/extract.py`): every URL slug (+ the sampled titles) becomes a document of 1-3-grams. Content-format words (`tier list`, `patch notes`, `guide`, `checklist`, `vs`, `best` ...) end an n-gram and are counted separately as `formats`; filler words (`tips`, `beginner`, `complete`, `hub`, `free` ...) seen on the site become `exclude`; the brand, ids, years and catalogue codes (`xqz 216`) are dropped. Phrases are scored `log(1+df) * log(1+lift)` where lift is their share on your site divided by their share in the bundled background corpus. Phrases on more than half of all pages are dropped as boilerplate. scikit-learn is optional (`pip install 'nicheclock[onboard]'`); it only speeds up document counting and gives identical scores.
3. **entities**: the top phrases are looked up on en.wikipedia.org (MediaWiki API, cached in `~/.cache/nicheclock/wikipedia_titles.json`). A phrase that has an article becomes an `entity` with the canonical title; when the title reads differently than the phrase (`bird treaty act` -> `Migratory Bird Treaty Act`) the phrase also stays a keyword so text matching still works. Single words only count when the article's short description names a thing (species, company, law, person, place ...), so `vendor` stays a plain keyword. `--no-entities` skips this step.
4. **niches** (`onboard/niches.py`): 1-4 niches per site. Each topical top-level URL section with enough pages (`/owls/`, `/gear/`; generic ones like `/blog/`, `/glossary/` do not count) becomes a niche ranked on its own pages, the remaining pages form a general niche. A site without topical sections is split by co-occurrence (same pages, same sections, shared words); groups with no real co-occurrence are merged, so a site without structure gets one niche. A niche is named in Title Case after its top entity, else its top phrase. Catalogue tokens are dropped: numbers, short codes that are neither dictionary words nor known acronyms, and page-type boilerplate that sits in many `<title>`s but in no URL ("Field Guide, Photos & Range Map"). Keywords are ordered most specific first (multi-word phrases), because each keyword becomes a live query. Each niche gets a kind by word lists (`games`, `dev_ai`, `apps`, `policy`, `default`) and sources: always google_trends, wikipedia, hn, gdelt, plus github for dev/AI niches, apple for apps, federal_register for policy/law, and `rss` when feeds were found. The suggested upcoming-event kinds are printed for each niche.

   **Broad entities:** an entity whose Wikipedia short description is a place or a generic concept ("U.S. state", "Branch of ...") or whose article has more than 5,000 median daily views (last 30 days, Wikimedia pageviews, cached 30 days) goes to the niche's `context:` list instead of `entities:`. `run` counts `context` hits only together with a niche keyword. Common-noun phrases are demoted to `context` too ("Bird feeder", "Field guide"): the article's short description names a kind of thing, its lead sentences use the title in lowercase mid-sentence or in the plural (one batched MediaWiki extract request, cached). Specific multi-word entities (laws, species, products, events) are listed first.
5. **feeds** (`onboard/feeds.py`): the outbound links on the sampled pages are counted per external domain; for the top 15 (minus a denylist: every site on the [list of sources nicheclock never uses](sources.md#sources-nicheclock-does-not-use), social networks, shorteners, stores) the homepage's `<link rel="alternate" type="application/rss+xml|atom+xml">` is read and every candidate feed is fetched and must parse and have an item from the last year. Sites that advertise no feed are probed at `/feed`, `/rss`, `/feed.xml`, `/rss.xml`, `/atom.xml`, `/index.xml` (robots and 1 req/s/host as always). Giveaway / deal / coupon feeds (by URL, channel title or mostly deal headlines) and the user's own site's feeds are never added. A feed must have at least 10% topical overlap (share of its latest 30 titles containing a niche keyword or entity); the top 5 per niche (best overlap first, one per domain before a second) are written to `rss:`; feeds with 5-10% overlap are suggested in that niche's `watch:` (lower floor in the pipeline) under a comment that **you are responsible for each feed's terms**. `preview` and `edit` list them; `edit --remove-rss URL` drops one; `--no-feeds` skips discovery.

`--watch URL [URL ...]` (put it last) names competitor or industry sites. Their feeds are found the same way (deal feeds and low overlap are kept, only never-ship domains and your own site are refused) and written to every niche's `watch:` list, with `rss` added to the niche's sources. `edit --add-watch/--remove-watch URL` manage them. All fetches follow robots.txt and 1 req/s/host.

Same crawl, same output: the whole pipeline is deterministic.

## Options

`--project NAME` puts all URLs into one project (default: one project per site). `--force` replaces a project of the same name, `--dry-run` prints the config instead of writing it, `--preview` shows a live preview before saving, `--max-niches 1-4`, `--no-feeds`, `--max-feeds`, `--max-pages`, `--entity-limit`, `--background FILE`.

## Your own model (optional, never default)

`nicheclock init URL --llm` asks *your* model to propose niches from up to 50 page titles with their URL slugs and the site's host name. It runs only when the config already has an `llm:` section (see [Models](models.md)); with a fresh site, write the section first and re-run `init --force`, or pass `--dry-run` to look at the result. No page text is sent. The model's niches are merged into the rule-based drafts: a niche on the same topic gains its name and extra keywords, formats and exclusions, a new topic becomes a new niche. You still edit the YAML before anything runs.

```yaml top
llm: {provider: ollama, model: llama3.2, base_url: "http://localhost:11434"}
```

## Background corpus

`onboard/data/background.json.gz` holds document counts of 1-3-grams (single words from df >= 1, longer phrases from df >= 3) and its provenance in the `meta` key. It was built with `python -m nicheclock.onboard.build_background --top 3000 --random 4000` from en.wikipedia.org (CC BY-SA 4.0): titles and the first sentences of the intro of 7000 articles (the most-viewed ones of the last 12 months from the Wikimedia pageviews "top" lists, plus random articles), through the MediaWiki API and the Wikimedia REST API. Only aggregate counts are shipped, no article text. It is a general-English baseline, not a model of the web: it demotes words that are common in encyclopedic text and is neutral for niche vocabulary.

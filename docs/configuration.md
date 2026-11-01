---
title: Configuration
description: Every field of nicheclock.yaml, the per-source options, how a topic gets into the feed, notifications and the files a run writes.
order: 3
publish: true
---

# Configuration

nicheclock reads one YAML file. `init` writes it, you edit it. Paths in it are resolved against the folder the file is in. Pick the file with `--config` (or `-c`), the `NICHECLOCK_CONFIG` environment variable, or `./nicheclock.yaml`, in that order.

Secrets never go into the file. Fields that need one name an environment variable (`token_env: TG_TOKEN`) or a file (`token_file: path`) instead.

## The whole file

```yaml
identity:
  user_agent: "mysite-radar/1.0 (+mailto:you@example.com)"   # required before any fetch
  contact_email: you@example.com                              # required before any fetch
data_dir: ./data              # SQLite database and caches
out_dir: ./out                # reports: <out_dir>/<project>/{feed.json,feed.md,index.html,feed.xml}
default_lag_days: 3           # index lag used for publish_by until Search Console gives a measured one
projects:
  - name: example
    urls: [https://example.com]
    schedule: daily           # recorded for your scheduler; `run` does not read it (see Limitations)
    regions: [global]         # or country codes such as [US, GB]; see Regions
    niches:
      - name: birdwatching
        keywords: [birdwatching, bird watching]    # phrases that make an item relevant
        entities: [Snowy owl]                    # specific things, as Wikipedia titles where possible
        context: [Bird]                    # broad terms: count only next to a keyword or entity
        formats: [field guide, checklist]  # content formats, shown in the report, never matched as topics
        exclude: [tips, beginner]          # an item containing one of these is dropped
        sources: [google_trends, wikipedia, hn, github, gdelt]
        rss: []                            # feeds you supply (titles, links and dates only are kept)
        watch: []                          # feeds where any keyword hit is enough
        ics: []                            # calendars for the Upcoming table
        upcoming: true
        options: {geo: [US], hn_min_points: 50}
llm: null                     # your model, see Models; null means no model call, ever
```

Validate it any time with `nicheclock doctor` (add `--offline` to skip the network checks).

## Fields

| Field | What it does |
|---|---|
| `identity.user_agent`, `identity.contact_email` | Sent with every request. Required. There is no default. |
| `data_dir`, `out_dir` | Where the database and the reports go. |
| `default_lag_days` | Days between publishing and appearing in search, used for `publish_by` until Search Console measures yours. |
| `projects[].urls` | The site or sites the project describes. Only your own site is crawled by `init`. |
| `niches[].keywords` | Multi-word phrases beat single words. Generic phrases such as "small teams" are refused by the relevance floor. |
| `niches[].entities` | Specific things. Wikipedia article titles work best because pageview signals are keyed by title. |
| `niches[].context` | Broad terms (places, big companies, generic concepts). They count only beside a keyword or entity in the same item and never pass alone. |
| `niches[].exclude` | Phrases that drop an item. |
| `niches[].sources` | Which sources run for this niche. Remove a name to switch the source off (see [Sources](sources.md)). |
| `niches[].rss`, `ics`, `watch` | Your own feeds and calendars. `watch` marks feeds where any keyword hit is enough because you chose the feed. |

`nicheclock edit` changes any of the list fields without opening an editor: `--add-keyword`, `--remove-keyword`, and the same for `entity`, `format`, `exclude`, `source`, `rss`, `watch`, `ics`. It also has `--add-niche`, `--rename-niche`, `--remove-niche`, `--add-url`, `--remove-url`, `--add-notify` and `--preview`. Run `nicheclock edit --help` for the full list.

## Per-source options

Under `niches[].options`:

| Option | Default | Source | Meaning |
|---|---|---|---|
| `geo` | `[US]` | Google Trends | Country codes of the trending lists to read. |
| `wiki_lang` | `en` | Wikipedia | Language edition for pageviews. |
| `wiki_max_entities` | 15 | Wikipedia | How many entities to look up per run. |
| `wiki_min_ratio` | 1.5 | Wikipedia | A rising article must beat this multiple of its 14-day median views. |
| `hn_days`, `hn_min_points`, `hn_max_queries` | 7, 10, 8 | Hacker News | Look-back window, minimum points, queries per run. |
| `github_days`, `github_max_queries`, `github_token_env` | 30, 5, none | GitHub | Window, queries, and the name of the environment variable that holds your own token. |
| `gdelt_max_queries`, `gdelt_min_ratio` | 2, 2.0 | GDELT | Queries per run and the news-volume ratio to report. |
| `fr_days`, `fr_max_queries` | 7, 6 | Federal Register | Window and queries. |
| `rss_days` | 7 | RSS/Atom | How old an item may be. |
| `ics_horizon_days` | 365 | ICS | How far ahead events are read. |
| `apple_countries`, `apple_charts` | `[us]`, `[top-free]` | Apple | Storefronts and charts. |

## How a topic gets into the feed

Keywords and entities decide relevance. Fuzzy full-text searches (Hacker News, GitHub, GDELT, Federal Register) must contain a keyword or entity in the title. A topic then needs to clear a floor. Any one of these is enough:

- a specific entity;
- a keyword plus strength: Hacker News 100 points or more, Google Trends top 10, a Wikipedia riser at 2x, GDELT news volume at 2x the four-week mean, a Federal Register title naming the keyword, or a specific multi-word keyword in a headline;
- any keyword in an item of a feed under `watch:`;
- two distinct hits;
- one keyword seen in two source families.

Abstract-only Federal Register matches go to `low_confidence` in `feed.json`. Kept signals are stored in SQLite at `<data_dir>/nicheclock.db`.

GDELT is one global queue: ten seconds between requests, answers cached 24 hours. When it rate-limits you, the rest of the queue is saved to `<data_dir>/cache/gdelt_queue.json` and goes first in the next run.

## What `run` writes

For each project, under `<out_dir>/<project>/`:

| File | Contents |
|---|---|
| `feed.json` | Items (`project`, `niche`, `kind`, `title`, `url`, `why`, `score`, `at`; with a model also `rule_score`, `llm_fit`, `llm_reason`; topics also `evidence`, the signals behind them), the upcoming events, Search Console gaps, the niche definitions and, with a model, an `llm` usage summary. For scripts. |
| `feed.md` | The same as Markdown, with an Upcoming table. |
| `index.html` | The report page: niche tabs, a Trends table, an Upcoming table, Gaps, and the evidence and attribution behind each row when you click it. The data is embedded in the file, so it also opens from disk; the page needs JavaScript. The stylesheet, script and font are shared in `<out_dir>/assets/`. |
| `feed.xml` | RSS for a feed reader. |

`<out_dir>/index.html` links all projects. Exit status: 0 when everything worked, 1 when one or more sources failed (the files are still written), 2 for a configuration problem such as a missing identity.

## Notifications

`run` sends each channel the top `top_n` (default 5) new items per niche, with the `why` and a link. Items already sent to a channel are skipped; the log lives at `<data_dir>/<project>/notify_sent.json` and entries expire after 90 days. A failing channel is reported and retried next run; it never blocks the others. `--no-notify` skips them for one run.

```yaml project
notify:
  - {type: telegram, token_env: TG_TOKEN, chat_id: "-100123", top_n: 5}
  - {type: discord, webhook_env: DISCORD_WEBHOOK, kinds: [topic, event]}
  - type: email
    host: smtp.example.com
    port: 587                    # 587 STARTTLS (default), 465 with security: ssl
    security: starttls           # starttls | ssl | none
    username: me@example.com
    password_env: SMTP_PASSWORD  # or password_file
    from: me@example.com
    to: [me@example.com]
```

Optional on every channel: `top_n`, `kinds: [topic|event|gap]`, `min_score`. Telegram also takes `api_base` and `message_thread_id`; email takes `ca_file` for a private CA.

## Project-level fields

`regions` and `region_mode` (per project, overridable per niche) choose which countries are read and whether each gets its own topics; see [Regions](regions.md).

`schedule` is stored for you and for your scheduler; nothing inside nicheclock reads it yet. `gsc` connects [Search Console](search-console.md). `profile` (`title`, `description`) tells a model who the site is. `llm` configures [your model](models.md).

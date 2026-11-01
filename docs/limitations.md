---
title: Limitations
description: What nicheclock cannot do yet: coverage, languages, scheduling, accuracy, models, platforms and the gaps in the Docker and hosted setup.
order: 13
publish: true
---

# Limitations

What nicheclock cannot do yet, so you can decide before you invest time.

## What it is not

- **Not a keyword tool.** It reports that something is trending and why. It does not give search volume, keyword difficulty or rank tracking. Google Trends contributes an approximate traffic bucket for a trending search, nothing more.
- **Not a writer.** It tells you what to cover and by when. It does not draft articles. A model, when configured, scores and explains; it does not generate content for your site.
- **Not a promise of traffic.** A trending topic with a publish-by date is a lead, not a guarantee.

## Coverage

- **English and the US first.** Keyword extraction, the bundled background corpus, the interface and the reports are English. Google Trends defaults to `US`, the Federal Register is American, and the Apple charts default to the US store. Per-country reading works in a basic form ([Regions](regions.md)): only Google Trends and Apple are exact per country, Wikipedia and GDELT are approximate, and Hacker News, GitHub and your own feeds have no country at all. Country-specific keywords and region-aware Search Console gaps are planned ([roadmap](roadmap.md)).
- **A fixed set of sources.** Only sources whose terms allow it ([Sources](sources.md)). Reddit, Product Hunt and similar are not read. You can add your own feeds.
- **Small niches stay quiet.** A niche with narrow keywords may produce nothing for days. That is the relevance floor working.
- **Upcoming events come from limited places:** Federal Register dates, feeds and calendars you supply, and Wikipedia early risers. ICS support covers simple recurrence rules only.
- **Index lag needs data.** `publish_by` uses `default_lag_days` until Search Console gives at least three recently published pages to measure.

## Running it

- **`schedule:` is not read.** The field is stored in the config. The Docker image runs on an interval, and outside Docker you use cron or a systemd timer.
- **No built-in login on the report server.** `nicheclock serve` serves static files with Python's HTTP server. Use a reverse proxy for authentication and TLS.
- **Not on PyPI, no prebuilt image.** Install from a source checkout and build the image yourself until launch.
- **Tested on Linux.** The test suite and CI run on Linux. macOS and Windows may work and are untested.
- **The report is plain.** A page of niche tabs and tables with no search or filtering. `feed.json` is the interface for anything richer.
- **SQLite, single process.** One store per config. Running two `run` processes against the same data directory at once is not supported.

## Search Console

- One Search Console property per project.
- The OAuth consent has to be done on a machine with a browser; it cannot be completed inside the container.
- With the consent screen in *Testing* status Google expires the token weekly ([details](search-console.md)).

## Models

- Gemini and Codex providers are unit-tested but not yet run against the real services.
- A small local model judges less reliably than a large hosted one. Compare with `nicheclock eval export` before you rely on the ranking.
- Model output is not reproducible: the same input can be judged a little differently on another day. Judgements are cached for a week to keep rankings steady.
- Cost figures are estimates from a price table with a date; your provider's invoice is authoritative.
- Items from publishers whose terms forbid AI use are never sent, so they keep their rule-based score only.

## Hosted service

- Private pilot, not open to outsiders. See [API](api.md).
- It serves only data licensed for redistribution, so it has fewer sources than a self-hosted install.
- No Search Console data, no bring-your-own-model.
- Custom niches from a URL are built by a daily batch, not live.

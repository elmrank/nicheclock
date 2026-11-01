---
title: Regions and per-country trends
description: Set regions per project or niche, pick per country or combined topics, and see which sources give exact, approximate or no country data.
order: 9
publish: true
---

# Regions and per-country trends

> **Status.** Released in a basic form. The config keys and JSON field names below are the ones the code uses.

nicheclock can read a niche for one country, several countries, or globally. Every signal, topic, event and feed item carries a `region` (an ISO country code, `global`, or `multi`) and a `region_precision` that says how far to trust that tag.

## Configure

```yaml
projects:
  - name: mysite
    regions: [US, GB, VN]          # default: [global]
    region_mode: per_country       # per_country | combined (default: combined)
    niches:
      - name: birdwatching
        keywords: [birdwatching, bird watching]
        regions: [US]              # optional: overrides the project for this niche
        region_mode: combined
```

* `regions`: `global`, or two-letter country codes. Lowercase is accepted. A niche without `regions` uses its project's.
* `region_mode: per_country` clusters each country on its own, so a topic belongs to exactly one region and the report has a section per country. `combined` clusters everything together, so the same story seen in two countries is one topic with `region: multi` and `regions: [GB, US]`.
* `nicheclock run --region US` reads only that country (global sources still run).
* `nicheclock doctor` prints what each source does with your regions.

## What each source does

| Source | `REGION_SUPPORT` | What a country means | Precision |
|---|---|---|---|
| Google Trends RSS | `country` | one feed per country (`geo=XX`) | exact |
| Apple marketing RSS | `country` | one storefront chart per country | exact |
| Wikipedia pageviews | `language` | the country's main language edition (US and GB share `en`) | approx |
| GDELT | `source_country` | publishers located in the country (`sourcecountry:`) | approx |
| Hacker News, GitHub | `global` | no country concept | global |
| Federal Register | `global` | US federal documents, always tagged `US` | exact |
| Your RSS, watch and ICS feeds | `global` | no country concept | global |

With `regions: [global]` and no country asked for, Google Trends and Apple still need a country, so they read `options.geo` / `options.apple_countries` (default US) and tag what they read. Wikipedia reads `wiki_lang` (default `en`) and tags `global`. GDELT reads English-language sources and tags `global`.

## Output

* `feed.json` has top-level `regions` and `region_mode`, each niche has `regions` and `region_mode`, and every item has `region`, `region_precision` and `regions` (the countries seen, sorted). Events have `region` and `region_precision`.
* A region filter keeps that country's items plus the `global` ones. A `multi` item is kept when the country is in its `regions`.
* When a feed spans two or more countries, `out/<project>/regions/<CC>/` holds a filtered `feed.json`, `feed.md`, `index.html` and `feed.xml` for each one. In `per_country` mode an item seen in two countries is listed once per country, and the report names the countries on those rows.
* The MCP tools `trends_for_niche` and `upcoming` take an optional `region` argument (`US`, or `global` for no filter). Results carry the region fields.
* `Store.topics(...)`, `Store.events(...)` and `Store.recent_signals(...)` take `region=`.

## What works now and what is planned

Works now: the config and validation above, region tags on every source, per country and combined topics, per-country output folders, the MCP `region` argument, the store filter, and `doctor` and `run --region`.

Limits and planned work:

* GDELT reads at most two countries per niche (`options.gdelt_max_regions`) because every country adds two requests at 10 s spacing. Countries without a known GDELT code are skipped and reported.
* Wikipedia entity growth uses the MediaWiki API, which skips the robots.txt check only on `en.wikipedia.org`. Other language editions use the most-read list and the series of matched articles, not entity growth.
* Wikipedia and GDELT are approximate: a language is not a country and a publisher's country is not the story's.
* Planned: more per-country sources (each needs its own terms check), country-specific keyword lists, region-aware Search Console gaps.

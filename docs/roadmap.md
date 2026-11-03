---
title: Roadmap
description: What is planned next for nicheclock, what per-country trends can do today, and what is explicitly not decided yet. Planned means not built.
order: 14
publish: true
---

# Roadmap

Items under Planned are not built. Dates are not promised, and names of settings may change before anything ships.

## Per-country trends: available in basic form

The first version is released: `regions` and `region_mode` in the config, a region label and a precision on every trend, one section or folder per country, `nicheclock run --region`, and a `region` argument on the MCP tools. Each source says how well it can answer for a country: exact (Google Trends, Apple), approximate (Wikipedia language editions, GDELT publisher country) or global only (Hacker News, GitHub, your own feeds). Read how it works and what it cannot do yet in [Regions](regions.md).

Still planned:

- More per-country sources, each after a check of its terms.
- Country-specific keyword lists.
- Search Console gaps that know about regions.
- A `region` filter on the hosted API, once the hosted service supports it ([API](api.md)).

## Planned

- A published package on PyPI and a prebuilt container image.
- Translations of the interface and the docs. English only for now; translations are welcome later, and the interface strings are being moved into one file to make that possible.
- Reading the per-project `schedule:` field in the Docker scheduler.

## Not decided

- Windows and macOS as supported platforms.
- More sources. A source is added only when its terms allow automated use.
- A richer report with filters.

Want something here sooner, or something that is missing? See [Contributing](contributing.md).

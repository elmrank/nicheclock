# R2 data schema (`schema: 1`)

The operator's machine writes these JSON objects (`python -m nicheclock.hosted_export`, see README) and the
Worker only reads them. The Worker **re-validates on every read** and builds its output field by field,
so unknown fields never reach clients. A bucket key is `<base>/<file>`:

```
catalog/niches.json                          list of catalog niches
catalog/<niche>/trends.json                  scored topics
catalog/<niche>/evidence.json                raw signals behind each topic
catalog/<niche>/upcoming.json                dated events with publish_by
custom/<key_id>/niches.json                  list of that key's custom niches
custom/<key_id>/<niche>/{trends,evidence,upcoming}.json
```

* `<niche>`: `[a-z0-9][a-z0-9-]{0,63}`. Catalog ids must **not** start with `custom-`; custom ids must
  (`custom-<host-slug>`, derived from the URL by the Worker when the job is created).
* `<key_id>`: `k_` + 16 hex. A key can only ever read its own `custom/<key_id>/` prefix.
* Dates: `at`, `generated_at`, `updated_at`, `seen_at` are ISO-8601 UTC; `date`, `publish_by` are `YYYY-MM-DD`.
* Keep `trends.json` under ~200 KB (exporter caps 100 items per niche): the Worker parses it within 10 ms CPU.

## Provenance (every item)

Every item, and every evidence entry, carries:

| field | rule |
|---|---|
| `source` | one of the hosted source ids below; otherwise the item is dropped when served |
| `source_url` | `http(s)` URL of the item itself, or the source's homepage when the signal has none |
| `license` **or** `attribution` | at least one non-empty string |

An item missing any of the three is dropped, never "repaired".

### Hosted source ids (allow-list, enforced at serving time)

| id | scope | terms | local nicheclock source it comes from |
|---|---|---|---|
| `wikimedia_pageviews` | everyone | CC0 | `wikipedia` (pageviews) |
| `wikipedia` | everyone | facts only: title, date, link (text is CC BY-SA, never returned) | `mediawiki` |
| `wikidata` | everyone | CC0 | `wikidata` |
| `gdelt` | everyone | free use, must cite (`attribution: GDELT Project`) | `gdelt` |
| `hn` | everyone | Hacker News API | `hn` |
| `federal_register` | everyone | US government work, 17 U.S.C. 105 | `federal_register` |
| `github` | everyone | official GitHub REST API (read-only, public repo metadata) + our own daily star/fork snapshots; terms quotes in `nicheclock/catalog/data/sources.json` | `catalog/github-rank.json`, catalog GitHub rows |
| `google_trends`, `apple` | `internal` and `admin` keys only | internal use, not redistribution | `google_trends`, `apple` (`--include-internal`) |
| `nager` | `internal` and `admin` keys only | internal use, not redistribution | (not shipped in nicheclock; `--include-internal`) |

Never served to anyone, even if present in R2: `rss`, `ics`, `google_news`, `reddit`, anything else. Free text (`why`)
that names a source the caller may not see is blanked.

### `scope: "internal"` tag

The exporter adds `"scope": "internal"` to every item, evidence entry, evidence record and event that touches an
internal-only source, **including a topic whose own `source` is public but whose score/why also used an internal
signal**. The Worker hides any tagged object from `external` keys in addition to the source check. The exporter writes
internal-scope data only with `--include-internal`, which requires `--custom-key` (a key id of an `internal`/`admin`
key): it refuses to put any of it under `catalog/`, which every key can read. Operator duty: never pass an external
key's id together with `--include-internal`.

## Regions (`region`, `region_precision`, `regions`)

From nicheclock's regions feature (`docs/regions.md`). Every trend item, evidence entry and event carries:

| field | values |
|---|---|
| `region` | two-letter ISO country code, `global`, or `multi` (a combined topic seen in several countries) |
| `region_precision` | `exact` (source is per country), `approx` (language edition / publisher country), `global` |
| `regions` | trend items only: sorted countries seen (`[]` for global) |

Missing or malformed values are served as `region: "global"`, `region_precision: "global"`, `regions: []`, so older exports keep working.
`GET .../trends?region=XX` and `GET .../upcoming?region=XX` (and the MCP `region` argument): `XX` is two letters, case-insensitive;
`global`/empty means no filter; anything else is `400 invalid_params`. The filter keeps items whose `region` is `XX` or `global`, plus
`multi` items whose `regions` contains `XX`; events (no `regions` list) keep `XX` and `global`. The response echoes `region` (null when unfiltered).
The exporter recomputes `region`/`regions` of a topic after dropping signals of sources it may not export.

## `niches.json`

```json
{ "schema": 1, "generated_at": "2026-10-31T05:00:00Z",
  "niches": [ { "id": "birdwatching", "name": "birdwatching", "description": "optional",
                "sources": ["hn", "wikimedia_pageviews"], "updated_at": "…",
                "counts": {"trends": 12, "upcoming": 3},
                "url": "custom niches only: the site the niche was built from" } ] }
```
A niche whose declared `sources` are all non-servable for the caller is hidden; `sources` is filtered to the caller's scope.

## `trends.json`

```json
{ "schema": 1, "niche": "birdwatching", "generated_at": "…",
  "items": [ { "id": "birdwatching~a1b2c3d4e5f6", "title": "Snow Goose count results", "score": 71.4,
               "why": "hn 120 pts, 40 comments", "url": "https://…" , "at": "2026-10-31T03:10:00Z",
               "source": "hn", "source_url": "https://news.ycombinator.com/item?id=9",
               "attribution": "Hacker News (Y Combinator) via Algolia HN Search API",
               "sources": ["hn"], "evidence_count": 1 } ] }
```
* `id` = `<niche>~<6-40 chars [a-z0-9]>` and must start with this file's niche id.
* `why` must be derived only from the item's own allowed signals (the exporter re-scores topics after dropping signals).
* `GET .../trends?days=N` keeps items with `at >= now - N days`, best `score` first.

## `evidence.json`

```json
{ "schema": 1, "niche": "birdwatching", "generated_at": "…",
  "items": { "birdwatching~a1b2c3d4e5f6": {
      "title": "…", "source": "hn", "source_url": "…", "attribution": "…",
      "evidence": [ { "source": "hn", "source_url": "…", "attribution": "…", "term": "snow goose",
                      "seen_at": "…", "score": 120, "rank": 4 } ] } } }
```
`GET /v1/evidence/{item_id}` → `{id, niche, title, source, source_url, license?, attribution?, evidence: [...]}`.
If the item's own `source` is not servable to the caller the answer is 404, as if it did not exist.

## `upcoming.json`

```json
{ "schema": 1, "niche": "birdwatching", "generated_at": "…",
  "items": [ { "id": "birdwatching~e0123456789a", "kind": "release", "title": "…", "date": "2026-11-09",
               "date_precision": "day", "publish_by": "2026-11-06", "url": "https://…",
               "source": "federal_register", "source_url": "https://…", "license": "Public domain (17 U.S.C. 105)" } ] }
```
`GET .../upcoming?days=N` keeps `today <= date <= today + N` (UTC), soonest first, and adds `days_until` and `publish_by_passed`.

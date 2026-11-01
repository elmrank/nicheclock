# catalog-data (`schema: 2`)

The public trend catalog: topics are Wikidata entities, grouped under level-1 categories, with a multi-source
"trending now" list per region and an "upcoming" calendar. Written once a day by
`python -m nicheclock.catalog build` (nicheclock/catalog/), read by the static site. Draft: follows the best
plan, waiting for the user's approval.

```
meta.json            counts, categories, REGIONS (with source availability), sources ledger, source filter
trending.json        "Trending now", Worldwide (region "global")
trending-IN.json     the same schema for each of the ten countries: IN CN US ID PK NG BR BD RU ET
upcoming.json        dated events with publish_by (all regions; filter client-side)
```

Dates are `YYYY-MM-DD` (UTC); `generated_at` is `YYYY-MM-DDTHH:MMZ`.

**There is no search volume anywhere.** Each channel has its own number and unit and is never summed or
converted into another. `ratio` compares a channel with *its own* median of the 14 days before the window.
**No data is null/absent, never 0**: a source with no data for a region is missing from that region's rows
(`channels`) and from `meta.regions[].sources`.

## Regions

Worldwide plus the ten most populous countries on 1 July 2026 by UN World Population Prospects 2024, medium variant
(`meta.population_source`; Ethiopia 138.9 M is 10th, Mexico 133.0 M 11th, so Mexico is out). `meta.regions[]` (display
order):

```
{ id: "global"|"IN"|..., label, native_label (null when same), rank, population_2026, languages[], timezone (IANA),
  limited: bool, limited_note: string|null, sources: [channel keys with topics here], topics: N, date, file,
  source_status: { wikipedia|news|hn|youtube: {status: ok|limited|no_data|pending_key, detail, topics} } }
```

* `limited: true` shows "limited data for this region": Wikimedia publishes no country data for China and Russia and
  only a handful of pages for Pakistan, Nigeria, Bangladesh and Ethiopia, so their Wikipedia channel is the national
  language edition (read worldwide, `scope: "language"`) and `limited_note` says why. GDELT adds articles that mention
  places in the country.
* `meta.source_filter` = `[{key, label, family}]` (wikipedia, news, hn, youtube): the values of the source filter;
  a key is also the channel key in a topic's `channels`.
* `sources` of a region only lists keys that have at least one topic there.

## Sources ledger (`meta.sources`, data in nicheclock/catalog/data/sources.json)

Every source with `status` (`used`, `pending_key`, `excluded`, `planned`), `license`, `url`, `terms_url`,
`terms_quote` (the sentence the decision rests on), `decision`, `unit`, and for this build `ok` + `detail`. Used:
`wikimedia_pageviews` (CC0), `wikimedia_country` (CC0, differentially private), `wikidata` (CC0), `gdelt` (free use,
cite the GDELT Project), `hn`, `youtube` (official API, chart presence and raw views only), `holidays` (MIT),
`federal_register`. Excluded with the reason: Nager.Date (commercial use needs sponsorship), Apple Marketing Tools RSS
(affiliate-programme tool, no redistribution statement), TMDB, Spotify charts, Google Play, Google Trends RPC, Baidu,
Weibo. Planned for M2: GitHub, TVmaze, GDACS, ReliefWeb, Mastodon, Stack Exchange, CoinGecko, Open Library, npm/PyPI,
Bluesky. `holidays` and `wikimedia_country` are new source ids: add them to `hosted/src/sources.ts` before the Worker
serves these files.

Every topic, evidence row and event carries `source` (id), `source_url` (the item itself, or the source's homepage)
and `license`. A topic's own `source` is its strongest channel; all of them are in `sources`.

## YouTube sidecar files (not in git)

`youtube[-ID].json` = `{schema, region, date, topics: {qid: {channel, evidence}}, extra_topics: [complete rows]}`.
`topics` is merged into the rows of the tracked file by qid; `extra_topics` are rows that exist only because of the
YouTube chart (no other channel) and are appended. `coverage`, `meta.regions[].topics` and COVERAGE.md count only the public rows
(what a fresh clone serves); `sidecar_rows` in the tracked file is the number of extras that exist only in the local sidecar, not counted.

## Categories: at least 10 rows each, nothing invented

`coverage` (also in `meta.coverage[region]` and written as `COVERAGE.md`, a region x category table) counts, for the
rows of the tracked file that the page lists under its default 7d window (a row without a 7d window is not counted), per
category `rising` and `steady` rows, `total`, `limited` (`total < 10`: show the real count with "limited data") and
`by_window[24h|48h|7d] = {rising, total}` where total adds every steady row (steady rows are listed under every window).
The page computes its own "limited data" flag from the same rows, so both agree by construction (test.js checks it).
The pool is the top 1,000 titles of each edition (Worldwide, limited regions) or the biggest 1,200 plus the best risers
of the per-country dataset (IN US ID BR), classified by Wikidata class, so a category is filled from its own pages.
The rising bar is relative to the category: strong risers first, mild risers only if the category is short, steady
top items last; the volume floor applies to all of them. `meta.regions[].categories_below_10` counts the limited ones.

## Running it

```
export NICHECLOCK_USER_AGENT="nicheclock-catalog/0.1 (+mailto:you@example.com)" NICHECLOCK_CONTACT_EMAIL=you@example.com   # or --config nicheclock.yaml
uv run --with requests --with pyyaml --with holidays python -m nicheclock.catalog build      # everything, once a day
                                                                                              # --regions global IN   --only trending|upcoming
python -m nicheclock.catalog resolve "home espresso"            # text, QID or Wikipedia URL -> QID + category
python -m nicheclock.catalog map                                 # the category mapping with live labels
```

State (cache, daily GDELT samples, daily per-country Wikimedia files, `first_seen`) lives in `~/.nicheclock-catalog`
(`--state`). The first run samples 17 days of GDELT (about 100 files) and 22 per-country days (about 400 MB); later runs
one day each. The YouTube key is read from `YOUTUBE_API_KEY` (or the one-line env file named by `YOUTUBE_KEY_FILE`) and never printed
(11 regions = 10 quota units a day). A source that fails is reported in `meta.json` and its channel is simply absent.

## trending.json

```
{ "schema": 2, "region": "global", "date": "2026-10-30",    the last complete pageviews day the channels end on
  "generated_at": "...", "window_note": "...",
  "coverage": {category id: {rising, steady, total, limited, by_window}}, "topics": [ ... ] }
                                             rising rows by rank_score, then steady rows by volume
```

Topic:

| field | meaning |
|---|---|
| `id`, `qid` | Wikidata QID (`Q1968`); `label`, `description` are Wikidata's English ones |
| `category`, `category_label` | level-1 id (`sports`) and Google's label (`Sports`); list in `meta.json` |
| `category_via` | why: `{kind: property\|class\|occupation\|taxon\|fallback, class: QID, label}`; `label` is the class's Wikidata label (see CATEGORIES below) |
| `region` | `"global"` or the country id of the file |
| `native_label` | the topic's Wikidata label in the country's language (null when none or same as `label`) |
| `trend` | `"rising"` or `"steady"` (shown as "Steady / Top"). Every category gets at least 10 rows when the data has them: all its strong risers first (best 25), then mild risers, then steady top items by volume |
| `rise` | `"strong"` (>= 1.5x its 14-day median in the 24 h or 48 h window and above the volume floor), `"mild"` (>= 1.2x in some window, above the floor; only used to fill a category that has fewer than 10 strong risers), `null` for steady rows |
| `status` | steady rows: `"steady"`. Rising rows: `active`: a channel is rising now (24 h or 48 h). `ended`: it rose within 7 days and is back near baseline; candidates include the English top lists of the 5 days before, so a topic that peaked and fell back still appears |
| `ratio` | the "% increase" column as a multiple: largest 24 h/48 h ratio among the rising channels (steady rows: their real, small ratio). Show "+1,000%+" from 10x |
| `volume`, `volume_unit` | the Wikipedia channel's last-day value, else the news channel's; null for chart/HN-only rows. Sort "By volume" on it |
| `rank_score` | default sort of rising rows: `log10(volume)^2 * log10(min(ratio, 10))`, +25% per extra rising family; 0 for steady rows (sorted by volume) |
| `score` | `min(100, 2.5 x rank_score)`; not a search volume. YouTube adds nothing to it (its terms forbid derived metrics) |
| `source_families` | rising families: `wiki`, `news`, `community`, `video` |
| `multi_source` | at least two families rising at once (the badge) |
| `rising_channels` | channel keys that are rising: `wikipedia`, `news`, `hn`, `youtube` |
| `seasonal` | `seasonal` (this calendar month peaked in at least 2 earlier years), `new` (>= 2x this year's typical month, no earlier peak), `unknown` (less than 2 full earlier years, or nothing notable); `seasonal_detail` has `years_checked`, `years_peaking`, `peak_months`. Computed for the best 150 topics from monthly pageviews since 2015-07; others stay `unknown` |
| `first_seen` | first catalog day this topic was listed |
| `series_end` | the date the last point of every `series` belongs to |
| `windows` | `{"24h"\|"48h"\|"7d": {ratio, active}}`: best ratio across channels; `active` at >= 1.5. Present on every row, steady ones included (`active` false) |
| `channels` | per-channel numbers, below |
| `evidence` | `[{source, source_url, license, title, value, unit, comments?}]`: the Wikipedia article, up to 3 news articles (title + link), up to 3 HN stories, up to 3 chart videos (`rank`) |
| `source`, `source_url`, `license`, `sources` | provenance as above |
| `aliases` | Wikidata English aliases, at most 8 |
| `breakdown` | other names ("Trend breakdown" column), at most 8: Wikidata English aliases, the article titles in the other wikis, the names GDELT counted. These are names, not search queries |
| `wikipedia_url` | English article, if there is one |

`channels` (any subset):

```
wikipedia: { value, unit: "pageviews/day", baseline (median of the 14 days before), ratio, windows, series[14],
             scope: "global"|"language"|"country", wiki: "en", title, by_wiki: {en: {title, value, ratio}, ...},
             censored? (country scope), source, source_url, license }
news:      { value, unit: "articles/day (estimated from sampled 15-min GDELT files)", baseline, ratio, windows,
             series[<=14], source, source_url, license }                       window "7d" only after 21 days of samples
hn:        { value: summed points, unit: "HN points (sum over front-page and 48 h stories)", stories, source, source_url, license }   Worldwide only
youtube:   { chart: true, rank (best on the country's mostPopular chart), videos, value (raw views YouTube reports, or null), unit, source, source_url, license }
```

* `wikipedia.value` is the last complete day, user agents only (no spiders); the wiki shown is the one where the
  topic is biggest, `by_wiki` lists every wiki that was read for it. Worldwide reads the editions en es hi zh id ur pt bn ru
  am ha yo. `scope: "country"`: Wikimedia's per-country dataset (a page's views from that country; pages below the
  release threshold are not published, so a missing day counts as that day's smallest published value and
  `censored: true` marks the ratio as a lower bound; counts are noisy by design). `scope: "language"`: the country's
  own language edition, read worldwide (limited regions).
* `news` for a country counts GDELT articles that name the person or organisation AND mention a place in that country.
* `youtube` is YouTube's own most-popular chart for the country at build time: presence and raw views only, no ratio,
  no windows, nothing derived. A chart video is attached to a topic like a Hacker News story.
* `news` counts distinct GDELT articles naming the person or organisation, from 6 sampled files per day (every
  4 hours) scaled to 96 files: an estimate, same scheme every day.
* `hn` is a snapshot at `generated_at` (current front page + stories of the last 48 h with >= 100 points).
  A story is attached to a topic by a capitalised phrase that resolves to the topic, or by the topic's label in the
  title. It has no baseline, so it never has a ratio: it counts as rising at >= 50 points.

## upcoming.json

```
{ "schema": 1, "today": "2026-11-01", "lag_note": "...", "events": [ ... ] }      sorted by date, then popularity
```

| field | meaning |
|---|---|
| `title`, `date`, `date_precision` | always `"day"`; `dates` (Wikidata rows) lists every date the item has, earliest is `date` |
| `kind` | `film release`, `game release`, `album release`, `single release`, `book release`, `software release`, `tv premiere`, `release`, `sports event`, `election`, `festival`, `award ceremony`, `space mission`, `event`, `holiday`, `comment deadline`, `effective date` |
| `publish_by`, `lag_days` | `date - lag_days`; lag by kind (below) |
| `category`, `category_label` | level-1 category: Wikidata events by the item's class, Federal Register by agency, holidays `other` |
| `region`, `regions` | `global`, `multi` or a country id; `regions` lists the country ids the event applies to (holidays: of IN CN US ID PK NG BR BD RU ET; Federal Register: `US`). An event with a `regions` list (holidays, Federal Register) belongs to exactly those regions; an event without one (Wikidata) belongs to every region and Worldwide. Worldwide shows all. |
| `qid`, `popularity` | Wikidata events: the item and its sitelink count (items with >= 6 are listed) |
| `agencies` | Federal Register agency names |
| `source`, `source_url`, `license` | provenance |

Sources: **Wikidata** items with a day-precision future value of P585 (point in time), P580 (start time) or P577
(publication date) in the next 365 days, queried by exact date; **holidays** (the `holidays` package, 10 regions' countries,
same name and date across countries is one row); **Federal Register** significant documents with a comment
deadline within 180 days or an effective date within 180 days.

`publish_by` lags (days before the event; defaults per type, not measured: connect Search Console for a measured index
lag in the self-hosted engine): election 21, festival 28, holiday 28, effective date 21, film/game/book/software release 14, tv premiere 14, award ceremony 14, space mission 14, comment deadline 14, generic event
14, sports event 7, album/single release 7. Source of truth: `LAG_DAYS` in `nicheclock/catalog/upcoming.py`.

## meta.json

`counts` (`topics`, `multi_source`, `events`, `categories_with_topics`, `categories_with_events`, `categories`, all for
Worldwide; `regions`, `topics_all_regions`), `categories[{id, label, topics, multi_source, events}]` for all 19 (Worldwide
topics), `regions`, `sources`, `source_filter` (above), `category_source` (where the list came from),
`population_source`, `symmetry` and `units`.

## CATEGORIES

Level 1 is Google Trends' "Trending now" category list, read from trends.google.com on 2026-11-01 (United States, past
7 days, the "All categories" dropdown): Autos and Vehicles, Beauty and Fashion, Business and Finance, Climate,
Entertainment, Food and Drink, Games, Health, Hobbies and Leisure, Jobs and Education, Law and Government, Other, Pets
and Animals, Politics, Science, Shopping, Sports, Technology, Travel and Transportation (19). The page and its
per-category row counts of that day are in `nicheclock/catalog/data/categories.json`.

A topic is placed by Wikidata classes: `nicheclock/catalog/data/category_map.yaml` (documented there). Order of checks:
a sport statement (P641) means sports; humans by the category most of their occupations (P106) vote for; taxa by
whether the parent-taxon chain reaches Animalia; everything else by the nearest rule class on the "instance of" /
"subclass of" ladder (6 steps), ties to the more specific category; nothing matched is `other`. Rules are classes, never
single topics, and every category has between 3 and 24 of them (checked at build time and in tests), so no branch is
seeded deeper than another.

The mapping file is the only hand-written part of the catalog. Topics, their numbers and their categories are
organic: the build does not hide, boost or hand-pick any topic.

## github-rank.json

The GitHub list with the columns and windows of github.com/trending, built from open data (not copied from that page).

```
{ schema: 1, date, generated_at, note, windows: {daily:1, weekly:7, monthly:30}, minimum_stars: {daily:50, weekly:150, monthly:400},
  unit: "stars/day", languages: [{language, repos}], ranks: {daily|weekly|monthly: [repo names in rank order]}, repos: [row] }
row: { repo, url, description, language, topics[], total_stars, total_forks, created_at, new_repo,
       stars: {daily, weekly, monthly}, forks: {daily, weekly, monthly}, stars_per_day: {daily, weekly, monthly},
       series[14] (stars/day), baseline, source, source_url, license }
```

Stars and forks are counts of public WatchEvent / ForkEvent in GH Archive for the UTC days up to `date`; description,
language, topics and totals come from the GitHub REST API. The MCP tool and REST route `github_rising(language?, window?, limit?)`
read this file. Worldwide rows of `trending.json` also carry the rising repos as Technology topics (`id` `gh:owner/name`, `qid` null,
`channels.github` with unit `stars/day`).

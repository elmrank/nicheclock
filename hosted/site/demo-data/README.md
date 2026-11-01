# demo-data

`demo.json` feeds the `/demo/` page (and the mocks). It is a real `nicheclock run` over three neutral niches, exported once a day.
It is overwritten by the operator's daily job; do not edit it by hand.
`build.py` copies this directory as it is to `/demo-data/`.

| | |
|---|---|
| Niches | `birdwatching`, `chess`, `weather` ("Weather and storms"), picked by the numbers: of 11 candidates these had entities rising past 1.5x their 14-day median and events in the next weeks |
| Sources | Only sources whose terms allow it: Wikimedia pageviews (CC0; 60-80 articles per niche: seasons, events, people, species, equipment), Hacker News via the Algolia API, GDELT (fetched from a second host because its API throttles by address) and the Federal Register, and event calendars compiled by hand from organisers' pages (name, date and link only; each event links its page). No publisher feeds: their terms either forbid reuse or say nothing, so none is in the demo |
| Model | Claude Haiku through the Claude Code sign-in: a 0-100 `fit` score and a one-sentence `reason` per topic. Every topic has a `fit` (the field stays nullable for self-hosted use) |
| Not in the file | GitHub and Google Trends (internal-use sources), Reddit, store pages, anything not in nicheclock's allowed set |
| Gaps | **Sample data.** Search Console rows are invented for three fake sites on `example.com`; the gap logic and the model's angles are the product's real ones |
| Regions | `US`, `GB` (project regions), `multi` (seen in several), `global` (feeds and calendars carry no country) |

## Schema (`"schema": 1`)

```
{
  "schema": 1,
  "generated_at": "2026-10-31T13:02Z",   UTC
  "today": "2026-10-31",                 UTC date of the run; countdown = publish_by - today (compute in the browser from the real date)
  "lag_days": 3,                         index lag used for publish_by (date - lag_days)
  "niches": [{ "id", "name", "blurb", "regions": ["global","US","GB"], "topic_count", "upcoming_count", "site" }],
  "topics": [{                           sorted by score, descending
      "id", "niche": <niches[].id>, "title" (short display title; the full headline is in sources[].title),
      "score": 0-100,                    blend: 0.4 rule score + 0.6 model fit (nicheclock default)
      "rule_score": 0-100 | null,
      "fit": 0-100 | null,               model's niche-fit-times-timely-angle score; null = not sent to the model
      "reason": "one sentence",          the model's angle, or why it was not scored
      "region": "global"|"US"|"GB"|"multi", "regions": ["GB","US"],
      "first_seen", "last_seen": "YYYY-MM-DD",
      "rise": 3.48 | null,                pageviews of the topic's article, last 3 days vs its 14-day median (>= 1.5 = rising); null for topics without an article
      "series": { "start": "YYYY-MM-DD", "values": [daily Wikipedia pageviews, 30 points], "label": "Wikipedia pageviews: Bald eagle", "url" } | null,
                                         the series exists only when the topic IS a Wikipedia article (sources[0].id = wikimedia_pageviews); headline topics (Hacker News, GDELT) have null; show `label` next to the sparkline
      "sources": [{                      1-8 evidence entries, each with a link
          "id": "wikimedia_pageviews"|"gdelt"|"hn"|"federal_register",
          "name": "Wikimedia pageviews" | "Hacker News" | ..., "title": "item title as published", "url", "at": "2026-10-30T18:29Z",
          "note": "3.5x its 14-day median (2,102 vs 604 views a day)" | "12 points on Hacker News" | null,   (ratios near 1.0x mean attention is flat: the entity is tracked, not rising)
          "attribution": "line to print with the item", "license": "CC0-1.0" | null }]
  }],
  "upcoming": [{                         sorted by date; dates >= today
      "id", "niche", "title", "date": "YYYY-MM-DD", "publish_by": "YYYY-MM-DD",
      "kind": "event"      a dated event (contest, show, match, season end, count window)
            | "deadline",  a Federal Register comment deadline or effective date; rising articles are NOT here, they are topics (see `rise`)
      "region", "url", "source": <sources[].id>, "attribution" }],
  "gaps": {
      "sample": true, "label": "Sample data", "note": "...",
      "sites": [{ "niche", "name": "birds.example.com", "domain", "sample": true, "label": "Sample data" }],
      "rows": [{ "niche", "query", "page": url of the best-ranking page | the site root,
                 "clicks", "impressions", "position", "status": "missing" (no page covers it) | "weak" (ranks past position 20),
                 "why": "690 impressions (+360% vs prior 28d), best position 17.8: ...",
                 "angle": "model's suggested angle" | null, "questions": [..] | null, "score" }]   sorted by score
  },
  "llm": { "provider", "model", "note" }
}
```

Fields are never omitted; unknown values are `null`. Strings are plain text (escape on output). The file is about 60 KB.

## How it is produced

The operator keeps a private directory with `nicheclock.yaml` (hand-written keywords, entities, feeds), a script that seeds the invented
Search Console rows, and a script that exports this file. A daily job runs `nicheclock run`, exports, rebuilds the site and deploys it.
The producer scripts are not part of this repository; the schema above is the contract.

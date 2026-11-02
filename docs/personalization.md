---
title: Your niche
description: The optional Your niche page on the website: what a profile holds, what stays in your browser, how a page is matched to your topics, and what it cannot do yet.
order: 15
publish: false
---

# Your niche

On the website, the **Your niche** link in the top bar lets you say what you write about and see the rising Wikipedia pages and the dated events that fit it, each with the reason. It is optional, it needs no account, and your profile is stored in your browser only. This page describes what is built today (steps 1 and 2 of the design: the profile, the matching, and the "also cover" suggestions).

> **Status.** Built and live, not announced: the link is quiet and the home page does not promote it. It works well for topics that Wikidata describes in detail (sports, games, film, music, species) and less well for hobbies and ingredients; see "What it cannot do" below. Suggestions from your own site or YouTube channel, and profile tools for AI agents over MCP, are planned, not built.

## What you do

1. Open **Your niche**. Type a topic, pick the meaning you intend from the list (the grey line is the Wikidata description), repeat for up to 8 topics. The first is the main topic; the others count 0.8.
2. Pick a country. Only the United States, India, Brazil and Indonesia have enough pages in Wikimedia's per-country pageview files (at least 1,000 published rows a day) for a pool.
3. Press **Show my niche**. Trending now and Upcoming switch to **For your niche**.

A visitor can also press a sample (chess, birdwatching, sourdough) to see the result with no typing.

Optional fields: a website and channel handles (stored as typed, never fetched), what you publish (blog, video, short, newsletter), posts per week and your usual days. Usual days add a **Your slot** column to Upcoming. **Export JSON** gives you a file to keep, **Import JSON** reads it back, **Delete this profile** removes it. Up to 5 profiles.

## What stays in your browser

Everything about the profile. It is in `localStorage` under the key `nicheclock.profiles.v1`, never in a cookie, IndexedDB, `sessionStorage` or the URL hash, so a pasted link cannot carry it and the page counter cannot see it. The page downloads public data files that hold no profile (the topic list and one list of pages for your country, without a query string) and does the matching on your device. The pool is downloaded only when you open Your niche or For your niche, not on the first page load.

`hosted/site/trending/test-privacy.js` proves it: it drives the whole flow with sentinel strings that appear nowhere else and asserts that no request, header, body or WebSocket frame holds one, that every request is a GET to the page's own origin, and that no cookie is set. It saves a HAR file as the artefact.

## The profile file (`nicheclock.profile/1`)

```json
{
  "schema": "nicheclock.profile/1",
  "id": "p_k3f9a2",
  "name": "Chess notes",
  "updated": "2026-11-02T10:12:00Z",
  "topics": [
    {"qid": "Q718", "label": "chess", "role": "main", "source": "typed"},
    {"qid": "Q103632", "label": "chess opening", "role": "also", "source": "typed"}
  ],
  "avoid": [{"qid": "Q11410", "label": "game"}],
  "region": "US",
  "language": "en",
  "sites": [{"url": "https://example.org"}],
  "channels": [{"platform": "x", "handle": "@examplechess"}],
  "formats": ["blog", "video"],
  "cadence": {"per_week": 2, "days": ["tuesday", "friday"]}
}
```

`topics` holds 1 to 8 Wikidata ids (`Q` plus digits); `role` is `main` (weight 1.0) or `also` (0.8). `avoid` holds up to 10 ids whose rows are dropped; the **Not what I mean** button on an opened row adds to it. `region` is one of the pool countries. `sites` takes `https` addresses only. On import unknown keys are dropped, control characters are removed, sizes are capped (8 KB in all) and a file with another schema version is refused.

## How a page is matched

The pool for a country is every Wikipedia page that was **rising** (at least 120 views on the data day and at least 1.25 times its own median of the 14 days before) or **steady** (at least 200 views), plus the catalog's own topics. Each page carries a few Wikidata ties ("handles"): its classes (instance of, subclass of, up to 3 steps), its occupations, its subjects (sport, main subject, part of, series, franchise, genre, notable work) and its taxon parents. A profile topic fits a page in up to seven ways, each with a fixed score:

| Tie | Score |
|---|---|
| the page is the topic itself | 1.00 |
| the topic is a class of the page (1 to 3 steps) | 0.84 to 0.70 |
| the topic is the page's occupation | 0.85, divided by 1 + 0.15 for each occupation beyond the first |
| a class or occupation of the page has the topic as a whole word in its name ("chess player", "chess opening") | 0.85 for an occupation, 0.60 for a class |
| the page's sport, subject, part-of, series, franchise or genre is the topic | 0.80 (0.45 for a person) |
| the topic is a taxon parent of the page | 0.90 down to 0.55 by distance; half for a group above genus |
| the page title contains the topic as a whole word | 0.40 |

Ties combine as one minus the product of what is left, a secondary topic counts 0.8, and across topics the same rule applies. Guards cap a person with six or more occupations tied by one statement at 0.49 and a fictional thing at 0.30 (unless the topic is fictional). Fit at least 0.50 is listed, 0.35 to 0.49 sits under **Weaker fits**, below 0.35 is not shown. The default order is fit times a heat factor (how far above its own median the page is); steady rows have no heat and come after rising ones of the same fit. There is no model and no search volume; the same data gives the same list, and the weights are frozen in `match.js` and its tests.

Every row shows one **why it fits** line built from the strongest tie. Opening a row lists every tie with its score and links the Wikidata item.

## Events

Upcoming events that carry a Wikidata id carry the same handles, so a chess profile finds a chess championship by its sport. Yearly observances (Wikidata property P837, "day in year for periodic occurrence") are added as events of kind `observance`: their rule ("July 20", "second Saturday in May") is read and the next date within a year is computed; any other rule is skipped, never guessed. Observances below the catalog's 6-sitelink floor appear only in For your niche. Public holidays and Federal Register notices have no Wikidata id, so they are never matched.

## Also cover (bridges)

Wikidata does not tie a hobby to what it is about: `birdwatching` is a hobby and a kind of wildlife observation, with no link to birds. For hobbies, sports and fields of study the build reads the links of their English Wikipedia article and keeps the topics that are also ties of pool rows. The page offers them as chips ("People who write about birdwatching also cover: bird ...") under the topic field and when fewer than 10 pages fit. Nothing is added without a click, and matching never uses them.

## What it cannot do

- **Hobbies and ingredients are weak.** See "Measured results" below: the sourdough sample does not reach 70 percent on topic in any country, and birdwatching reaches exactly 70 percent in the United States only after two changes made once the first lists had been judged. The profile page says so.
- **Four countries.** Worldwide and every other country (the files of Pakistan, Nigeria, Bangladesh and Ethiopia are too small, China and Russia have none) have no pool.
- **Small niches have few rows.** Below 10 pages the page says how many fit and does not pad the list.
- **No search volume.** The numbers are Wikipedia views per day.
- **Not built yet:** reading your site or YouTube channel to fill in topics, and MCP or REST tools that take a profile.

## Measured results (2 November 2026)

The check asks, for each row of the top 20 of a profile: *would a site about this niche plausibly publish a page about this topic for its readers? Borderline counts as no.* A niche passes with at least 70 percent yes and at least 10 rows shown (a shorter list is not a niche feed). It was run by the agent that built the matcher, as a separate pass that saw only the label, the Wikidata description and the niche of each row, because an independent judge was not available; the raw answers and every list are in `docs/personalization/judge-2026-11-02.json`. Pageviews are those of 1 November 2026.

| Profile | Country | Rows (N) | On niche | Share | Met |
|---|---|---|---|---|---|
| chess | United States | 20 | 19 | 95% | yes |
| chess | India | 8 | 6 | 75% | no, fewer than 10 rows |
| chess | Brazil | 2 | 2 | 100% | no, fewer than 10 rows |
| birdwatching | United States | 20 | 14 | 70% | yes, at the threshold |
| birdwatching | India | 2 | 2 | 100% | no, fewer than 10 rows |
| birdwatching | Brazil | 2 | 0 | 0% | no, fewer than 10 rows |
| sourdough | United States | 9 | 2 | 22% | no |
| sourdough | India | 1 | 0 | 0% | no |
| sourdough | Brazil | 0 | 0 | none | no |

With the weights of the design note the birdwatching list in the United States was 13 of 20 (65%, not met). Two changes were made after those lists were seen: the half weight for a page about a taxon above genus now applies to every tie but the topic itself (the first lists were full of families and orders), and a class whose name starts with legendary, mythical or mythological joins the fictional guard. No threshold was changed. The 70 percent is one row from failing and depends on the rule that a species or a common-name group counts and a genus, family or order shown by its scientific name does not.

The profiles were chess = chess + chess opening, birdwatching = birdwatching + bird, sourdough = sourdough + sourdough bread + bread. Every row had a non-empty reason. In India and Brazil the country files hold few pages (8,825 and 2,274 rows in the pools), so a small niche has fewer than 10 rows there; the thresholds were not loosened.

## Data and files

`nicheclock catalog match` (run after `build` by the daily job) writes `hosted/site/catalog-data/match/`: `pool-<REGION>.json` (one per country, at most 2.5 MB gzip for the United States and 0.5 MB for the others), `concepts.json` (the topic list), `bridges.json` and `manifest.json`, and adds handles and observances to `upcoming.json`. Wikidata is read in batches of 50 by at most 5 threads with an identity User-Agent, `maxlag` and `Retry-After` honoured; a page read in the last weeks is not read again. Wikidata statements are CC0; pageviews are CC0 (Wikimedia Analytics datasets); the text of Wikipedia articles is not copied.

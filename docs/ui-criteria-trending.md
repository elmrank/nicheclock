# Trending now page: pass criteria

**Status: nháp, chờ user duyệt.** Draft written 2026-11-01 and built against without waiting for sign-off. Internal design note, not a published doc (no front matter, so `docs/` tests and `build.py` skip it).

The hosted homepage is a multi-source "Trending now" table in the style of Google Trends, plus an Upcoming tab. This file says what "done" means, as checks anyone can run.

## References

| | Site | What we take | What we do not take |
|---|---|---|---|
| Primary | **Google Trends, Trending now** (`trends.google.com/trending`, studied 2026-11-01, US, past 7 days) | One row per trend. Filters in one strip: country, period (4h / 24h / 48h / 7d), category (19 entries incl. Other, each with a count), "Show active trends only". Sort by title, volume, recency, relevance. Columns: trend, volume bucket with arrow and %, Started with an Active chip, Trend breakdown chips, 7-day sparkline. Row expand-in-place is absent there; chips + "+N more" are the only detail. 25 rows per page, 3,377 trends, about 72 px per row, 10 rows in a 1440 x 1000 viewport. | Search volume buckets (we have no search data), green accent, rounded cards, pagination of 25 |
| 2 | **Exploding Topics** (`explodingtopics.com`) | Every growth number is followed by a plain sentence naming the unit and the period ("grew from X to Y monthly searches between ..."). | Card grid (one topic per card, 3 topics per screen), paywalled list |
| 3 | **Hacker News front page** (`news.ycombinator.com`) | Density: a rank list readable at a glance, no chrome, about 30 rows per screen. Meta line under each item (points, age, source). | Absence of filters |

Google's category names and counts, read from the live menu: Autos and Vehicles, Beauty and Fashion, Business and Finance, Climate, Entertainment, Food and Drink, Games, Health, Hobbies and Leisure, Jobs and Education, Law and Government, Other, Pets and Animals, Politics, Science, Shopping, Sports, Technology, Travel and Transportation. This matches the list in the 31/10 brief; the only difference is "and" where the brief wrote "&".

## Pass criteria

Each is checked at 1440 x 900 and 390 x 844, light and dark, against the fixture (`hosted/site/trending/fixture/`) and, once it lands, the real `catalog-data`.

1. **Density.** A collapsed row is at most 56 px tall at 1440. At least 11 topic rows are visible in the first viewport at 1440 x 900 (Google Trends: 9 at the same size). Check: `document.querySelectorAll('tr.row')` bounding boxes with the tab open on `#/trending`.
2. **Row anatomy, no fake unit.** Every row shows: topic and category, rise %, volume chips per channel, a sparkline, Sources chips, Active or Ended with how long ago it started, and "For which channel". 100% of volume chips carry a unit (`views/day`, `articles/day`, `points`, ...); the page never shows a bare number for volume, never sums channels, and "search volume" appears only in the sentence saying none of the numbers is search volume. Check: regex over all `.vol` chip text; count of "search volume" in `body.innerText` is at most 1.
3. **Sparkline is data.** Each sparkline is drawn from the channel's real series and has an accessible text with first value, last value, peak and unit. Expanded rows list the daily values. Check: every `svg.spark` has an `aria-label` matching `/from [\d.,KM]+ to [\d.,KM]+, peak/` and `<path>` points equal series length.
4. **Filters work and are shareable.** Region, window (24h / 48h / 7d), category (19 plus All, with counts), active only, multi-source only, seasonal / new, and a title search each change the row count on the fixture, and the state is in the URL hash so a link reproduces the view. Check: scripted toggle of each control, count differs, reload keeps it.
5. **Sort.** Rise and recency (plus number of rising channels) sort correctly in both directions; the sorted header has `aria-sort`. Check: read the rise column and the Started column after each sort and compare to a sorted copy.
6. **Evidence with licence.** Opening a row (click or Enter) shows per-channel baseline, latest and rise with units, the rule that picked the "for which channel" label, and an evidence list. 100% of evidence entries show source, a link to the source, and a licence; an entry with no licence is rendered as "licence unknown" and counts as a failure in the fixture test. Check: count `li.ev` against entries with `a[href]` plus `.lic` text.
7. **"For which channel" is a stated rule.** The label comes from `rules.js` (pure function of the topic's source mix, age, seasonality) and the rule text is on the page ("How the channel label is chosen") and in the expanded row. Same input, same output. Check: unit tests in `hosted/site/trending/test.js`, and that the on-page text names every rule.
8. **Reference type and colour, accessible.** Fonts and colours are the ones measured on Google Trends (Roboto and Google Sans Flex, both OFL, self-hosted; one accent `#0B57D0`; values and dates in `docs/ui-reference-measurements.md`), no banned font, red only for warnings. Text contrast at least 4.5:1 in both themes. Every control is reachable and operable by keyboard. No horizontal scroll at 390. No request leaves the origin. Zero console errors. 500 rows render in under 300 ms. Check: computed-style colour scan (only grey, the page tint, the accent and the warning red), contrast calculation, `scrollWidth <= innerWidth`, `browser.console()`, `performance.now()` around render.
9. **Regions and sources never fake data.** The Region select is Worldwide plus the countries in `meta.regions`; each country loads its own file. A channel with no data for a country is absent from rows and greyed out ("no data here") in the Source filter, never shown as 0; a `limited` region shows a "Limited data for this region" note listing its sources. The Source filter keeps only topics rising on that source. Check: switch to a limited region in the fixture; no `.vol` or `.src` chip for a source missing from `meta.regions[].sources`.
10. **Honest labels on real data.** 4h is disabled with the reason (no hourly source); "Sample data" shows only when the data says so; every estimated or derived number (news estimate, HN snapshot, "since" date, seasonal for the best 150) is labelled on the page under "How to read the numbers".

Also required: examples use neutral topics (birdwatching, home espresso, Formula 1), and the fixture is marked sample data on the page.

## Run it

```
python3 hosted/site/trending/build.py                # writes hosted/site/trending/dist (gitignored); data = site/catalog-data if present, else the sample fixture
python3 -m http.server 8788 -d hosted/site/trending/dist    # open http://localhost:8788/
node hosted/site/trending/test.js                    # rules, filters, sort, URL state, fixture invariants
uv run -q --with markdown-it-py python hosted/site/build.py --home trending   # full site with this page as the homepage, the old report at /demo/
```

`build.py --home trending` rewrites the tracked `hosted/site/public/`; the default (`--home report`) is unchanged. Do not commit public/ built that way unless the homepage switch is intended.

## Not criteria (on purpose)

- A volume number that looks like search volume. We have none; Search appears only as a channel when an official source allows it.
- Per-topic "follower" counts or "popular watchlists" (decision in the 31/10 analysis, section 7).
- Server-side anything: the page is static HTML and JS reading JSON.

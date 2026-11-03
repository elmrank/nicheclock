---
title: Your niche
description: The optional Your niche page on the website: what a profile holds, what stays in your browser, how a page is matched to your topics, and what it cannot do yet.
order: 15
publish: false
---

# Your niche

On the website, the **Your niche** link in the top bar lets you say what you write about and see the rising Wikipedia pages and the dated events that fit it, each with the reason. It is optional, it needs no account, and your profile is stored in your browser only. This page describes what is built today: the profile, the matching, the "also cover" suggestions, three buttons that fill in topics from a word, your website or your YouTube channel, and profile tools for AI agents over MCP and REST.

> **Status.** Built and live, not announced: the link is quiet and the home page does not promote it. It works well for topics that Wikidata describes in detail (sports, games, film, music, species) and less well for hobbies and ingredients; see "What it cannot do" below. The three buttons and the agent tools are live on the free plan; Read my channel stays hidden until the operator adds a YouTube API key.

## What you do

1. Open **Your niche**. Type a topic, pick the meaning you intend from the list (the grey line is the Wikidata description), repeat for up to 8 topics. The first is the main topic; the others count 0.8.
2. Pick a country. Only the United States, India, Brazil and Indonesia have enough pages in Wikimedia's per-country pageview files (at least 1,000 published rows a day) for a pool.
3. Press **Show my niche**. Trending now and Upcoming switch to **For your niche**.

A visitor can also press a sample (chess, birdwatching, sourdough) to see the result with no typing.

Optional fields: a website and channel handles (stored as typed; the Read my site and Read my channel buttons read them only when you press them, see "What each button sends"), what you publish (blog, video, short, newsletter), posts per week and your usual days. Usual days add a **Your slot** column to Upcoming. **Export JSON** gives you a file to keep, **Import JSON** reads it back, **Delete this profile** removes it. Up to 5 profiles.

## What stays in your browser

Everything about the profile. It is in `localStorage` under the key `nicheclock.profiles.v1`, never in a cookie, IndexedDB, `sessionStorage` or the URL hash, so a pasted link cannot carry it and the page counter cannot see it. The page downloads public data files that hold no profile (the topic list and one list of pages for your country, without a query string) and does the matching on your device. The pool is downloaded only when you open Your niche or For your niche, not on the first page load.

`hosted/site/trending/test-privacy.js` proves it: it drives the whole flow with sentinel strings that appear nowhere else and asserts that no request, header, body or WebSocket frame holds one, that every request is a GET to the page's own origin, and that no cookie is set. It saves a HAR file as the artefact.

## What each button sends

Three buttons on the profile page send something to nicheclock, only when pressed, and each sends only its own field in a `POST` to the page's own origin (no cookie, no query string). Nothing else on the page sends anything. Until one is pressed, the privacy test above holds as written.

| Button | Sends | Then |
|---|---|---|
| Look up a word (sends this word to nicheclock) | `POST /v1/resolve {"text": "..."}` | the topic list is read first; a word that is not in it goes to Wikidata (`wbsearchentities`, then `wbgetentities` for the English Wikipedia link of each candidate): the word only |
| Read my site (sends this address to nicheclock) | `POST /v1/suggest/site {"url": "..."}` | the Worker reads the site (below). The topic words it finds are matched to the topic list in your browser, so they go nowhere else; each one you look up is a Look up |
| Read my channel (sends this handle to nicheclock) | `POST /v1/suggest/youtube {"channel": "..."}` | shown only when channel reading is switched on. The Worker asks the YouTube Data API for your latest 50 uploads and their tags: 3 units |

Nothing is stored or logged. The address, word, handle, pages read and replies are not written anywhere: the handlers (`hosted/src/suggest*.ts`, `profile-tools.ts`, `shards.ts`, `robotstxt.ts`) contain no `console` call (`hosted/test/privacy.test.ts` greps for it and fails the build), every failure is a fixed code and message that never holds the input, and an unexpected error is turned into a fixed 500 before it can reach a generic logger. The tests call each handler with a sentinel string and assert that no `console` method ran, that the reply does not hold the input and that no D1 row does. `test-privacy.js` presses the three buttons against a stubbed Worker and asserts exactly three POSTs, each with only its own field and none holding a sentinel that belongs to another field; run against the live site after a deploy, it checks the same of the real page. The only thing kept is the rate-limit counters described under "Limits of the free plan".

### Read my site

What it reads, in order, with the user agent `nicheclock-suggest/1.0 (+https://nicheclock.elmrank.com/bot; oss@elmrank.com)` (the `/bot` page says what the bot does and how to block it; it is noindex and not in the sitemap):

1. **The address.** `http` or `https` only; the host must be public. Refused: IP addresses in every notation (the URL parser first turns `0x7f.1` or `2130706433` into `127.0.0.1`; private, loopback, link-local, CGNAT, benchmark and reserved ranges are named in the message, and any other IP address is refused too), single-label names, internal names (`.local`, `.internal`, `.lan`, `.home`, `.corp`, `.intranet`, `.localhost`, `.test`, `.invalid`, `.example`, `home.arpa`, `.onion`), the public wildcard-DNS services that turn a name into any address (`nip.io`, `sslip.io`, `xip.io`, `localtest.me`, `lvh.me` and a few more), addresses with a user name or password, and ports other than 80 and 443. **The limit:** a Worker cannot resolve DNS, so a public-looking name that points to a private address is not caught by name. What the call can reach is what a Cloudflare Worker's `fetch` can reach; it never follows a redirect to another host and reads nothing but robots.txt, one sitemap and page heads. nicheclock did not test a name that resolves to a private address.
2. **robots.txt**, RFC 9309. `2xx` is parsed (the first 500 KiB); `4xx` means no rules; `401`, `403`, `429`, `5xx`, an unreachable server or a redirect to another host means refuse, and nothing else is fetched ("robots.txt could not be read, so nothing was fetched"). The group that names `nicheclock-suggest` (or `nicheclock`) is used, else `*`; the longest matching rule wins and Allow wins a tie; `*` and `$` work. If the home page is disallowed the call stops with a plain message; every later address is checked against the same rules first.
3. **One sitemap**, at most 300 KB: the `Sitemap:` line of robots.txt (same host), else `/sitemap.xml`, `/sitemap_index.xml` or `/wp-sitemap.xml` (at most 3 tried). A sitemap index is followed to one child (not an image, video, tag, category or author one) within the same 300 KB budget; `.gz` is unpacked with an output cap.
4. **At most 8 page titles**, each the first 64 KB of a page, one after the other, only pages robots.txt allows. Forty addresses are sampled evenly through the sitemap; the words of their last path segment, and for 8 of them the title (the site name after `|`, a dash or a colon is dropped when most titles share it), are the text.
5. **Redirects:** at most one, to the same host and port. A `403` or `429` answer from the site ends the call and is never retried.

That is at most 26 subrequests (the free plan allows 50) and 15 seconds. The reply is `{ok, pages_in_sitemap, pages_read, titles_read, phrases: [{text, pages_with}]}`: 1 to 3-word phrases found in at least 2 pages, at most 20, no address, no title and no HTML. Phrases are not looked up there.

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

## Profile tools for AI agents (MCP and REST)

The hosted Worker serves three tools that take a profile in the call and keep nothing: `trends_for_profile`, `upcoming_for_profile` and `resolve_topic`. They are MCP tools on `POST /mcp` and REST routes `POST /v1/profile/trends`, `/v1/profile/upcoming` and `/v1/profile/resolve`, with the same Bearer key as the other hosted endpoints (keys are not open to the public yet, see the hosted README). The matcher is the page's own `match.js`, so an agent and a browser get the same list from the same rules.

```bash
curl -s https://nicheclock.elmrank.com/v1/profile/trends -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"profile": {"topics": [{"text": "chess"}, {"qid": "Q103632", "label": "chess opening", "role": "also"}], "region": "US"}, "limit": 5}'
```

| Tool | Arguments | Returns |
|---|---|---|
| `trends_for_profile` | `profile`; `limit` 1 to 50 (20); `min_fit` 0.35 to 1 (0.5) | rows, deduplicated by Wikidata id, best fit first: `qid`, `label`, `fit`, `why`, `relations[]` (`kind`, `via`, `via_label`, `score`), `trend` (`rising` or `steady`), `rise_ratio` (last day against the median of the 14 days before, rising rows only), `volume {value, unit: "pageviews/day"}`, `region`, `source`, `source_url`, `license`; and `region`, `data_date`, `matched` (rows at or above `min_fit` before the limit), `returned`, `unresolved[]`, `note` |
| `upcoming_for_profile` | `profile`; `days` 1 to 365 (30); `limit` 1 to 50 (20) | events and yearly observances whose date is in the window, soonest first: `title`, `kind`, `date`, `days_until`, `publish_by`, `publish_window_open`, `your_slot` (only when the profile has `cadence.days`: the latest usual day before `publish_by`; `late` when it has passed), `fit`, `why`, `relations[]`, `recurrence` and `rule` for observances, `source`, `source_url`, `license` |
| `resolve_topic` | `text`; optional `context` (words about the site, to prefer the matching meaning) | `{qid, label, description, confidence, ambiguous, alternatives[], source}`, from the topic list or from Wikidata. The only tool that calls Wikidata |

`profile` is the file above (`nicheclock.profile/1`). Two differences from the page: a topic may be `{"text": "chess"}` instead of a `qid`; the Worker looks it up in the topic list only (never on Wikidata) and returns the texts it could not find in `unresolved`, so pass a `qid` or call `resolve_topic` for those. And `schema` may be left out. A `label` given in the call is used as given (the "named" tie reads it, as on the page, where it is the topic list's label); a topic with a `qid` and no label gets the topic list's label. There is no `window` argument: the pool holds one data day (the last full day against the median of the 14 days before). `region` must be one of the pool countries (US, IN, BR, ID); another region is an error that says so.

How a call keeps its processor time down: it reads the shards, not the pool. For each topic it parses the shards that hold `q<qid>` and `w<longest word of the topic's name>` (usually 2 to 6 files of about 25 KB each), builds the candidate rows, runs `match.js` on them only and writes at most 50 rows. Parsed shards stay in the isolate. **Measured on the deployed Worker this is still over the free plan's 10 ms** (median 16 ms, p95 42 ms; see "The Worker tools" under Measured results).

## Limits of the free plan

Everything runs on a Workers Free plan and a free Google quota, with no new spend. The caps, in one place (the profile page shows the same list):

| What | Limit | Where it is enforced |
|---|---|---|
| Whole site | 100,000 Worker requests a day; 10 ms of processor time per request; 50 subrequests per request | Cloudflare |
| Any of the three buttons | 6 calls in 10 seconds from one address | the Cloudflare rate-limiting binding `BURST` |
| Look up a word | 20 an hour from one address | counter in D1 |
| Read my site | 5 an hour from one address; one read of a given site a minute for everybody; at most 26 subrequests, 300 KB of sitemap, 64 KB a page, 15 seconds | counters in D1; `suggest-net.ts` |
| Read my channel | 5 a day from one address; 2,000 of the 10,000 free YouTube units a day for everybody, 3 units a read (about 666 reads) | counters in D1 (one row per UTC day for the units) |
| Profile tools | 1,000 requests a day and 60 a minute per key, shared with the other hosted endpoints | D1 |

Cloudflare's rate-limiting binding only knows windows of 10 or 60 seconds, so it cannot hold "20 an hour" by itself. It is the first gate (it stops a flood before any D1 write); the hourly and daily caps are counters in D1 under a keyed hash of the address and the hour or day, which cannot be turned back into an address and are deleted when the window ends (`hosted/migrations/0003_suggest_limits.sql`). When a cap is reached the call answers `429` with `Retry-After`, the page shows the message, and typing topics still works. Nothing is queued.

## What it cannot do

- **Hobbies and ingredients are weak.** See "Measured results" below: the sourdough sample does not reach 70 percent on topic in any country, and birdwatching reaches exactly 70 percent in the United States only after two changes made once the first lists had been judged. The profile page says so.
- **Four countries.** Worldwide and every other country (the files of Pakistan, Nigeria, Bangladesh and Ethiopia are too small, China and Russia have none) have no pool.
- **Small niches have few rows.** Below 10 pages the page says how many fit and does not pad the list.
- **No search volume.** The numbers are Wikipedia views per day.
- **Title-only ties are not in the agent tools.** A page whose only tie to your topic is its title (fit 0.40 for a main topic, 0.32 for a secondary one) is in none of the units a topic is looked up by, so `trends_for_profile` can miss it in the 0.35 to 0.49 band; nothing at 0.50 or more is missed. Checked on the real 2026-11-01 pools: 8 of the 9 sample lists (3 profiles, 3 countries) were identical to the page's top 50 down to 0.35; the ninth (chess, United States) lacked four title-only rows at 0.40 ("history of chess", "glossary of chess", "Chess", "Total Chess World Championship 2026").
- **Read my site needs a sitemap and server-rendered titles.** There is no feed fallback; a site with neither says so and asks you to type your topics. It cannot read a site that blocks Cloudflare addresses, and it refuses a site whose robots.txt asks bots to stay out.
- **Read my channel is off** until the operator adds a YouTube API key (see `hosted/README.md`).

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

### The Worker tools (3 November 2026): judge, reply size and CPU

**Judge on the lists the Worker returns.** The same question, the same three profiles, the top 20 rows of `POST /v1/profile/trends`. The answers came from a single model call per niche that saw only the topic name, its Wikidata description and the niche (the typed judge was unavailable), so this is not an independent judge. It is more lenient than the pass above: birdwatching in the United States is 18 of 20 here against 14 of 20 there. Answers and lists: `docs/personalization/judge-2026-11-02-worker.json`.

| Profile | Country | Rows (N) | On niche | Share | Met |
|---|---|---|---|---|---|
| chess | United States | 20 | 19 | 95% | yes |
| chess | India | 8 | 7 | 88% | no, fewer than 10 rows |
| chess | Brazil | 2 | 1 | 50% | no, fewer than 10 rows |
| birdwatching | United States | 20 | 18 | 90% | yes |
| birdwatching | India | 2 | 2 | 100% | no, fewer than 10 rows |
| birdwatching | Brazil | 2 | 0 | 0% | no, fewer than 10 rows |
| sourdough | United States | 9 | 3 | 33% | no |
| sourdough | India | 1 | 0 | 0% | no |
| sourdough | Brazil | 0 | 0 | none | no |

**Reply size, top 20, United States** (measured on the deployed Worker): chess 10,425 bytes, birdwatching 9,079 bytes, sourdough 4,374 bytes (9 rows). `upcoming_for_profile` for chess, 365 days: 748 bytes. About 500 bytes a row.

**CPU on the deployed Worker**, from `wrangler tail --format json` (`cpuTime`, whole milliseconds), 3 November 2026, free plan. All calls ended `ok`. The limit this plan is built for is 10 ms; **two of the four endpoints are over it**:

| Endpoint | Calls | Median | p95 | Max | Note |
|---|---|---|---|---|---|
| `POST /v1/profile/trends` (3 profiles, 10 calls each) | 30 | 16 ms | 42 ms | 43 ms | the first calls of a new isolate cost 38 to 43 ms; the last 8 calls were 4 to 18 ms |
| `POST /v1/profile/upcoming` (chess) | 3 | 8 ms | 24 ms | 24 ms | too few calls for a p95 |
| `POST /v1/resolve` (20 words, local list and Wikidata) | 21 | 4 ms | 10 ms | 10 ms | within the limit |
| `POST /v1/suggest/site` (5 public sites) | 20 | 6 ms | 40 ms | 50 ms | only 9 of the 20 were real reads (6 answered 200, 3 were 422 for a site with no usable sitemap); 11 were refused by the per-address counter because clearing it between calls failed now and then. The real reads cost 7 to 50 ms |

The profile tools are 1.6 to 4 times over 10 ms at the median and about 4 times at p95; a warm isolate gets close to the limit, a new one does not. In Node on this machine the same work takes 1 to 3 ms warm and 5 to 8 ms cold, so the Worker runtime is several times slower than that estimate and the estimate was wrong. Shards of about 24 KB cut Node's parse time by half but cannot be the whole answer: what remains is parsing about 100 KB, running `match.js` and the D1 reads and writes of key checking and metering. Options if this has to fit 10 ms: a paid plan (not taken), smaller units (the `q` unit of a popular topic is the biggest), or caching the answer for a repeated profile. **The browser fallback for Read my site (the Worker returns the capped bodies, the page extracts the phrases) was not built.** The measurement does not show that phrase extraction is what costs time (in Node it is under 1 ms; fetching, decoding 300 KB and the D1 counters are the rest), so moving it would not bring the call under 10 ms.

**Privacy on the deployed site.** `node hosted/site/trending/test-privacy.js --live https://nicheclock.elmrank.com` passed all checks (the buttons run against a stubbed Worker, as in the local run). While real sentinel strings (`zzsentinelword`, a sentinel host, a sentinel handle, a profile with a sentinel name and site) were posted to the four live endpoints, `wrangler tail` showed 16 invocations, no log line and no exception, and no line containing a sentinel. The site read with the sentinel host was refused by the per-address counter before it reached the network, so that path is covered by `hosted/test/privacy.test.ts` only, not by a live check.

## Data and files

`python -m nicheclock.catalog match` (run after `build` by the daily job) writes, to the directory given by `--match-out` (the job uses `~/.nicheclock-catalog/match`; the files are not committed to git and `deploy-site.sh` copies them into its build): `pool-<REGION>.json` (one per country, at most 2.5 MB gzip for the United States and 0.5 MB for the others), `concepts.json` (the topic list), `bridges.json` and `manifest.json`, and adds handles to the events of `upcoming.json`, which is committed (about 20 KB). Wikidata is read in batches of 50 by at most 5 threads with an identity User-Agent, `maxlag` and `Retry-After` honoured; a page read in the last weeks is not read again. Wikidata statements are CC0; pageviews are CC0 (Wikimedia Analytics datasets); the text of Wikipedia articles is not copied.

The site build (`hosted/site/trending/build.py`, `stage_shards`, using `nicheclock/catalog/shard.py`) also cuts the same pools, the topic list and the events into **shards** under `catalog-data/shards/`, for the Worker's profile tools. A shard is a JSON file of at most 150 KB (the build raises the number of shards until none is bigger, and aims at about 24 KB each because the Worker pays about 1 ms of processor time per 50 KB it parses; `max_shard_bytes` in each manifest says the real maximum). A shard holds *units*: `q<qid>` is every row whose own id is that qid or that has it as a handle target, `w<word>` is every row that has a class or occupation (at most 2 steps) whose name contains that word, with those class names (`l`), because the "named" tie needs them. A unit goes to shard `fnv1a(key) % n`; a unit larger than 12 KB is cut into chunks `key~0`, `key~1` (ordered by views) listed in the manifest. The 7-day series is not kept. Per country this is the whole pool several times over (a row sits in the unit of each of its handle targets), which is why it is files on the Workers static-assets store (about 2,400 files for the four countries, the topic list and the events; 20,000 are allowed on the free plan, 25 MiB each) and not one object. `hosted/test/shards/` is a small copy built from the page's test fixture by `hosted/scripts/make-test-shards.py`.

---
title: Sources and terms
description: Every source nicheclock reads, the terms behind it, which sources are on or off on the website and why, and how to switch each one off.
order: 8
publish: true
---

# Sources and terms

nicheclock ships only sources whose published terms allow automated use, and each source module carries the link and the clause it relies on. The reports show the attribution a source asks for. All requests go through one client: your identity as User-Agent, at most one request per second per host, robots.txt respected and `Retry-After` honoured.

| Source (`sources:` name) | What it gives | Terms | Notes |
|---|---|---|---|
| Google Trends (`google_trends`) | Today's trending searches per country, from the public trending RSS. | [Google Trends terms](https://support.google.com/trends/answer/4365538?hl=en) | "Attribute the information to Google with a citation." Only the RSS feed, never the internal explore endpoints. |
| Wikimedia pageviews (`wikipedia`) | Daily views per article, top articles, early risers. | [Pageviews data (CC0)](https://dumps.wikimedia.org/other/pageviews/readme.html) | Needs an accurate User-Agent. Titles link back to Wikipedia (CC BY-SA). |
| MediaWiki API (used by `wikipedia` and by `init`) | Facts only: article titles and short descriptions, to turn phrases into entities. | [API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette) | `en.wikipedia.org/w/api.php` is the one endpoint called without the robots.txt check, because Wikimedia documents it for bots; identity, rate limit and `Retry-After` still apply. |
| Hacker News (`hn`) | Stories and points, through the Algolia search API. | [HN Search API](https://hn.algolia.com/api) | Title, link and points only. |
| GitHub (`github`) | Repositories that are new or gaining stars. | [GitHub Acceptable Use Policies](https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies) | REST API, not HTML. Set `github_token_env` to your own token for a higher limit; do not share tokens. |
| Apple (`apple`) | Top app charts, from Apple's Marketing Tools RSS. | [Apple Marketing Tools](https://performance-partners.apple.com/resources/documentation/tools-overview) | Charts and countries are options. |
| GDELT (`gdelt`) | News volume for your terms against its four-week mean. | [About GDELT](https://www.gdeltproject.org/about.html) | Citation required and shown in reports. One global queue, ten seconds between requests, the first to be rate limited. |
| Federal Register (`federal_register`) | US notices, comment deadlines and effective dates. | [API documentation](https://www.federalregister.gov/developers/documentation/api/v1) | US government works carry no copyright. |
| RSS and Atom (`rss`) | Items of feeds you list under `rss:` or `watch:`. | The publisher's own terms | You are responsible for the feeds you add. Titles, links and dates are kept, never summaries or content. |
| ICS (`ics:`) | Events from calendars you list. | The publisher's own terms | You are responsible for them. Summary, date and URL are kept. |
| Google Search Console | Your own queries and pages, only when you connect it. | [API usage limits](https://developers.google.com/webmaster-tools/limits) | Your data, read-only, with your own OAuth client. See [Search Console](search-console.md). |

## Switch a source off

A source runs for a niche only when its name is in that niche's `sources:` list. Remove the name:

```bash
nicheclock edit --niche "Optics" --remove-source gdelt
```

or edit the file:

```yaml niche
sources: [google_trends, wikipedia, hn]
```

For `rss` and `ics`, empty the `rss:`, `watch:` or `ics:` list of the niche. To stop `init` from calling the MediaWiki API for entity lookups, run it with `--no-entities`; to stop feed discovery, use `--no-feeds`.

Check what is reachable and allowed from your machine with `nicheclock doctor`: it asks each enabled host for its robots.txt.

## If a source fails

A network error, a 429 or a robots.txt block is printed at the end of the run, the other sources finish, and the exit status is 1. A 429 from a host skips that host for the rest of the run. GDELT's unfinished queries are saved and run first next time.

## Sources nicheclock does not use

Reddit, Product Hunt, GitHub Trending pages, Amazon, the internal endpoints of Google Trends, Chinese hot lists, and store pages, community sites and fan databases whose terms forbid automated collection. Their terms do not allow automated collection for this use, or they offer no sanctioned API for it. Some of them may be fine for your own use under your own terms; nicheclock will not ship them as defaults. You can still add your own feeds with `rss:`.

## On the website: what is on, what is off, and why

The website's catalog is a public product, so it reads a shorter list than the engine above. The state below is as of 1 November 2026. The reasons come from the terms pages we read that day; they are an engineer's reading of public terms, not legal advice, and terms change. The published `/catalog-data/meta.json` lists each source in use with its licence and the clause relied on.

| Source | On the website | Reason |
|---|---|---|
| Wikimedia pageviews, Wikidata | On | Both are released under CC0. Wikimedia asks for an accurate User-Agent and obeying `Retry-After`. |
| GDELT | On | Its datasets are free for unrestricted use with a citation to the GDELT Project. |
| Hacker News | On | Read through the Algolia API; only title, link and points are kept. |
| Public holidays (`holidays` package) | On | MIT licence, and dates are facts. It covers the same ten countries as the excluded Nager.Date API. |
| Federal Register | On | US government work, read through its documented API, not by scraping. |
| Stack Exchange (Stack Overflow tags) | Off until a key exists | The API terms say nothing against commercial use. They require a visible statement that the Stack Exchange Network is the source, an application key (keyless requests share one small quota per IP) and obeying the `backoff` field. The code is done: it reads `STACKEXCHANGE_KEY` from the environment or `~/.config/nicheclock/stackexchange.env`, makes one request a day, obeys and remembers `backoff`, puts "Stack Exchange Network" on every row and its evidence, and keeps only the tag name, its count and the tag link. It stays off until the key is registered on Stack Apps. |
| PyPI download counts | Excluded | A list of packages ranked by downloads is only available from BigQuery (needs a billed Google Cloud project) or from files with no stated licence. PyPI's own statistics page ranks packages by size, not by downloads. The catalog does not pick packages by hand, so the code was removed. |
| GitHub rank | On, official API only | Public repository data (stars, forks, created date, language, topics) from the GitHub REST API with a read-only token and the rate limit respected; the github.com/trending page is not fetched. The star history endpoint is not available, so each window is the difference between our own daily snapshots: a window we cannot know yet is empty, never zero, and a repo younger than a window counts all its stars in it. The lists fill up day by day. |
| GH Archive | Excluded | The dataset states no licence and warns that it may include third-party rights, so the GitHub rank does not use it. |
| GDACS | On | A UN and European Commission service. Its pages are reused under the Commission's CC BY 4.0 reuse policy with the source named, and GDACS says its alerts are indicative. Only the event name, type, alert level, country, date and link are kept. |
| TVmaze | On | The API is licensed CC BY-SA and attribution is a link back to TVmaze, which every row carries. Only the show name, network, popularity weight and link are kept. |
| Open Library | On | Light use with an identifying User-Agent, one request a day for the daily trending list. Only title, authors and link are kept. |
| npm download counts | Excluded | The registry docs state no licence for reuse and there is no official list of the most downloaded packages, so the packages would be picked by hand. |
| CoinGecko | Excluded | The free plan has no commercial rights, and its support pages say attribution alone does not change that. Using it needs a paid plan. |
| Nager.Date holidays | Excluded | Free for private or non-profit projects; commercial use needs sponsorship, and its terms do not allow using the holiday data to run a holiday portal. |
| Apple chart feeds | Excluded | The feed generator is a tool of Apple's affiliate partner programme, and we found no public statement that lets a product redistribute chart data. The engine's `apple` source still works for your own reports; read the terms for your use before you publish anything built from it. |
| TMDB, Spotify charts, Google Play, Google Trends internal endpoints, Baidu, Weibo | Excluded | A licence that needs a commercial agreement, terms that forbid reuse of charts or scraping, an endpoint that is not for automated use, or terms that do not clearly allow this use. |

Off and excluded are different. A source that is off has terms that can be met and a named step left to do; an excluded source stays out unless its terms change or we pay for a plan that grants the rights.

### YouTube

YouTube's most popular videos chart is read through the official API, once per region per day. The API terms limit what a client may do with it: statistics may not be stored for more than 30 days, and API data may not be replaced with independently calculated data or used to make derived metrics. So the build keeps only presence on the chart and the view count exactly as YouTube reports it, computes no ratio or score from either, and keeps nothing between runs. The files are excluded from git, so no dated copies pile up in history.

## What leaves your machine

Requests to the sources above carry your User-Agent and contact email. Nothing else is sent anywhere unless you configure it: a [model](models.md) provider, [notification](configuration.md#notifications) channels, or Google for Search Console.

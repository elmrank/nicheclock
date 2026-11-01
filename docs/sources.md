---
title: Sources and terms
description: Every source nicheclock reads, the terms that allow it, the limits it follows, and how to switch each one off. Plus the sources it refuses to use.
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

## What leaves your machine

Requests to the sources above carry your User-Agent and contact email. Nothing else is sent anywhere unless you configure it: a [model](models.md) provider, [notification](configuration.md#notifications) channels, or Google for Search Console.

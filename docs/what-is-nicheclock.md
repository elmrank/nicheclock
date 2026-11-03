---
title: What is nicheclock
description: nicheclock is a free trend radar for AI agents and SEO teams: rising topics and dated events with publish-by dates, on the web, over a free hosted API and MCP.
order: 2
publish: true
updated: 2026-11-03
jsonld: geo/schema-softwareapplication.json
---

# What is nicheclock

nicheclock is a trend radar. Its tagline is "Rising topics, dated events, and when to publish." It lists topics that are starting to rise on Wikipedia, in the news and on Hacker News, and it lists dated upcoming events with the date to publish by. A person reads the Trending now and Upcoming tables on the website, or the report a self-hosted run writes. Agents such as Claude Code or Cursor read niche trends and events over a free hosted MCP server with a key, or over a local MCP server that runs on your own machine. The home page also has a 74-second silent video that walks through the tables.

## What it returns

- **Rising topics.** One row per topic, with its category, how far it has risen, its volume in the unit of the source that measured it (page views a day, articles a day, points), a link to the source and the licence the source asks for. Rise compares the latest value in a window (24 hours, 48 hours or 7 days) with the topic's own median over the 14 days before. It is shown as a percentage and stops at +1,000%+, with the real multiple under the figure. The data is built once a day, so the page states its data date, and the 4 hour window is shown but switched off because no source is hourly. You can read the whole world or one of ten countries.
- **Upcoming events.** Each dated event has a `publish_by` date: the event date minus a lead time. On the website the lead time is a default for the kind of event, for example 14 days for a film release, 7 for a sports event and 28 for a festival or a public holiday. These are defaults, not measurements. A self-hosted run uses 3 days until you connect Search Console, then the median delay it measures on your own site.
- **Evidence.** Every item carries its source, a link and the licence or attribution the source asks for. Rows are never added together across sources, because a reader count, an article count and a point total are different things.
- **Categories and niches.** The Trending now table groups topics into 19 categories. For an agent, a niche is a topic area such as `birdwatching`; `nicheclock init` builds a list of niches from your site's URL.

## Your niche

The **Your niche** link in the top bar of the website is optional and needs no account. You type up to 8 topics, pick the meaning you intend from a list, choose one of four countries (the United States, India, Brazil or Indonesia) and press Show my niche. Trending now and Upcoming then switch to a For your niche view that lists the rising pages and the events that fit, each with one line saying why. The profile is kept in your browser's local storage and the matching runs on your device, so it is not sent to nicheclock. Three buttons send one field each, and only when you press them: Look up a word, Read my site (it reads the site's robots.txt, one sitemap and up to 8 page titles to suggest topics) and Read my channel. Read my channel stays hidden until a YouTube API key is added, so it is off today.

## How to use it

| You are | Use | Start at |
|---|---|---|
| AI agents, or an assistant such as Claude Code or Cursor | Hosted MCP at `https://nicheclock.elmrank.com/mcp` with a free key (`trending_now`, `upcoming_events`, `search_updates` and three profile tools), or local MCP over stdio: run `nicheclock mcp` on your own machine for `trends_for_niche`, `upcoming`, `gsc_gaps` and `explain_topic` | [MCP](mcp.md) |
| A script or an app | The hosted profile routes and the key routes of the REST API, with the same free key | [API](api.md) |
| An SEO or content person | The Trending now and Upcoming tables on the web, or the HTML report from `nicheclock run` | [Quickstart](quickstart.md) |

A free hosted MCP server and a small REST API are open. Anyone can sign in with GitHub at `https://nicheclock.elmrank.com/keys`, get a key and use it with the limits of 1,000 requests a day and 60 a minute per key, 2 active keys per person. The hosted tools `trending_now` and `upcoming_events` read the same public catalog as the Trending now and Upcoming tables of the website, `search_updates` says whether a Google core or spam update is rolling out and lists Google's Search updates and documentation changes, and three profile tools match a profile you send (`trends_for_profile`, `upcoming_for_profile`, `resolve_topic`). The niche tools and the Search Console tools exist only in the local server. The GitHub rank is built but hidden on the website and is not served by the hosted API, and the website publishes no YouTube data.

## What it does not do

nicheclock has no search volume, no keyword difficulty and no rank tracking, and it does not forecast. Every volume in the table is measured in its own unit and none is search volume. Use it next to a keyword tool, not instead of one. It does not write articles. Interface, reports and keyword extraction are in English only for now. Your niche works best for topics that Wikidata describes in detail, such as sports, games, film, music and species, and is weaker for hobbies and ingredients.

## Where the data comes from

The Trending now table on the website reads Wikipedia pageviews in twelve language editions and Wikimedia's per-country pageview files, GDELT news volume, Hacker News, GDACS disaster alerts, TVmaze and Open Library. The upcoming calendar adds Wikidata, the Federal Register and public holidays for the ten countries. Stack Exchange is off until a key exists, and PyPI, npm and GH Archive counts are not used. A self-hosted run reads the sources you switch on in your niche file, which can include the public Google Trends trending feed, GitHub through its REST API, Apple charts, feeds and calendars; none of those feeds the website table. Each source is used under the terms it publishes. [Sources](sources.md) lists the terms and how to switch each source off. Collection is plain code. A model, when you configure one, scores niche fit and writes a one-sentence reason, and your assistant's own model does the judging over MCP.

## Facts

- Licence: AGPL-3.0-or-later.
- Install: Python 3.11 or newer, `pipx install` from a checkout of the repository, or Docker Compose.
- Hosted API and MCP: free, open with a GitHub sign-in key (1,000 requests a day, 60 a minute per key). A local MCP server over stdio is also included.
- Source code: [github.com/elmrank/nicheclock](https://github.com/elmrank/nicheclock)

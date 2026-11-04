---
title: Answers to common questions
description: Direct answers about trend research with AI, MCP servers for trending topics, Google Trends alternatives, rising topics and when to publish.
order: 3
publish: true
updated: 2026-11-04
jsonld: geo/schema-faqpage.json
---

# Answers to common questions

Ten questions, each answered once and only for what nicheclock does today. Each answer also says where nicheclock is the wrong tool.

## What is the best tool for trend research with AI?

No single tool covers every job, so choose by the question you are asking. For absolute search volume use a keyword tool such as Google Keyword Planner or Glimpse. For Google's own search interest use Google Trends. For a curated database of emerging products and markets use Exploding Topics. For the traffic of other websites use Semrush Traffic & Market. For early signs of a rising topic in Wikipedia pageviews, news volume and Hacker News, plus events with a date to publish by, use nicheclock.

## Is there an MCP server for trending topics?

Yes, two. The hosted one at `https://nicheclock.elmrank.com/mcp` is free and needs a key that you get by signing in with GitHub; it serves the rising topics and dated events of the website tables, and profile matching. The local one starts with `nicheclock mcp` on your own machine, talks over stdio, exposes four read-only tools and reads the data your own install collected, so nothing passes through a hosted service. Setup steps for Claude Code, Claude Desktop and Cursor are on the [MCP page](mcp.md).

## Is there a Google Trends alternative with an API?

Not for Google data, because nicheclock has no Google search data. Google runs an application-based alpha of a Google Trends API, which its page says offers a rolling five years of data, daily to yearly aggregation and region comparison. Glimpse offers a separate Google Trends API that returns absolute search volume, and its API page says you request a key from Glimpse. If you need Google search interest, apply for Google's alpha, ask Glimpse, or use the Trends website. If you want other early signals instead, nicheclock reads Wikipedia pageviews, news volume and Hacker News, and serves the results through a free hosted API and MCP server as well as a local one.

## Is nicheclock a keyword research tool for AI agents?

No. It reports nothing about search demand: no volume, no difficulty score, no rank tracking. An agent can use nicheclock first, to learn what is rising or dated ahead, and then take those topics to a keyword tool for the numbers. On a self-hosted install with Search Console connected, the `gsc_gaps` tool lists queries where your own site has impressions but whose best position is worse than 20 by default, or that none of your known pages covers. Semrush offers an MCP server over its keyword and link data.

## How do I find rising topics for my niche?

Compare recent attention with each signal's own baseline. Take Wikipedia pageviews for the people, products and events in your niche, news volume for your terms, and Hacker News if the niche is technical. Keep every number in its own unit, because pageviews per day and articles per day cannot be added together. nicheclock does the comparison: ask `trends_for_niche` for your niche over 7 days, then open the `why` and the source link on a row before you write about it.

## Which trend research tools can I connect to Claude or ChatGPT?

Semrush and nicheclock both document an MCP connection. Glimpse offers a Google Trends API and says on its API page that you can contact it to arrange MCP access; the pages read for Google Trends and Exploding Topics mention none. nicheclock's MCP page covers Claude Code, Claude Desktop and Cursor through its hosted server and its local stdio server, and does not cover ChatGPT. Semrush publishes setup steps for its MCP server in Claude, ChatGPT, Cursor and other clients, and says access depends on your plan.

## How do I know when to publish an article so it is indexed before an upcoming event or season?

Count back from the event date by the number of days your site needs to get a new page into Google's index, and publish before that day. The `upcoming` tool does the subtraction and returns a `publish_by` date for each event: an event on the 20th with a three day lag gets the 17th. With Search Console connected on a self-hosted install the lag is measured from your own pages; the website calendar uses fixed lags by kind of event, such as 21 days for an election and 7 for a sports event. A publish-by date is a lead, not a promise of traffic or ranking.

## Is there a free trend data API for developers?

Yes, a small one. nicheclock has a free hosted API that needs a key from a GitHub sign-in and allows at most 1,000 calls a day and 60 a minute for each key. Its MCP endpoint takes plain JSON-RPC over HTTP and returns rising topics and dated events with source links and licences, not search volume, and its REST routes match a profile of your topics to rising Wikipedia pages and dated events. You can also call some of the free public sources it reads yourself. The Wikimedia pageviews API gives article views, the Hacker News search API from Algolia gives stories and points, and GDELT publishes news-volume datasets that it describes as free to use with a citation. Google's own Trends API is still an alpha that you apply to join.

## What are the alternatives to Exploding Topics and Glimpse?

nicheclock is not a like-for-like alternative, because it lacks what those two sell: search volume, forecasts and a curated trend database. What it offers instead is early signals from open sources, each row linked to its source with the licence shown, and calendar dates with a publish-by date. Treat it as a free complement that you run yourself. If you need volume or twelve month forecasts, stay with a tool that has them.

## What is an open source or self-hosted tool to track rising topics from Wikipedia, news and Hacker News?

nicheclock. It is AGPL-3.0-or-later software, installed with `pipx` from a checkout of the repository because it is not on PyPI yet, or run with Docker Compose. Among other sources that allow automated use, it reads Wikimedia pageviews, GDELT news volume and Hacker News. It needs no model to run; with one configured it adds a niche-fit score and a one-sentence reason. The interface and keyword extraction are English only for now. See the [quickstart](quickstart.md).

---
title: FAQ
description: Short answers about cost, privacy, accounts, which sources are used, models, Search Console, languages and licensing.
order: 12
publish: true
---

# FAQ

**What does nicheclock cost?**
The software is free under AGPL-3.0. You pay for your own server, if you use one, and for tokens if you bring a paid model. Without a model it needs no account and no key.

**Does it send my data anywhere?**
It sends requests to the [sources](sources.md) you enable, with your User-Agent and contact email. It sends nothing to us. A model provider, notification channels and Google (for Search Console) receive data only if you configure them, and [Models](models.md#what-is-sent-and-what-is-not) lists exactly what goes to a model.

**Do I need a model?**
No. The rule-based path collects, matches, clusters, scores and writes the calendar. A model adds a niche-fit score with a reason, merging of duplicate stories and angles for Search Console gaps.

**Why does it ask for my contact email?**
Because every request names you. If your traffic bothers a site owner, they can write to you instead of blocking a whole address range. Wikimedia requires it explicitly. nicheclock refuses to fetch without it.

**Why does a run take so long?**
One request per second per host, and ten seconds between GDELT requests. It is a polite client, not a fast one.

**Why is my feed empty?**
Probably the relevance floor. See [Troubleshooting](troubleshooting.md#the-feed-is-empty).

**Can I use it for several sites?**
Yes. `init` takes several URLs and makes one project per site, or one project with `--project NAME`. Reports are written per project.

**What is `publish_by`?**
The date of an event minus the time it takes a new page on your site to show up in Google. That time is measured from Search Console when you connect it, otherwise `default_lag_days` (3).

**Which languages and countries?**
English only for now: the interface, the reports, the bundled background corpus and the keyword extraction are English, and most sources are US-centred. `regions: [US, GB, DE]` reads several countries where a source can serve them ([Regions](regions.md)); more per-country sources are [planned](roadmap.md). Translations of the interface and the docs are welcome later.

**Is Google Trends allowed?**
nicheclock reads only the public trending RSS feed and attributes Google, as its [terms page](https://support.google.com/trends/answer/4365538?hl=en) asks. It never calls the internal explore endpoints.

**Can I add my own sources?**
Your own RSS, Atom and ICS feeds, yes (`rss:`, `watch:`, `ics:`). New built-in sources are modules with a terms link; see [Contributing](contributing.md).

**Is it safe to put the report on the internet?**
The report server has no login. Put it behind a reverse proxy with authentication, or keep it on localhost. See [Docker](docker.md#ports-and-the-reverse-proxy).

**Can Claude or ChatGPT use it?**
Claude Desktop, Claude Code, Cursor and other MCP clients can, through [MCP](mcp.md). You can also use your Claude Code or Codex sign-in as the model, subject to your plan's terms ([Models](models.md)).

**What is the license?**
AGPL-3.0-or-later. If you run a modified version as a network service, you must offer its source to the users of that service. Using it for your own sites has no such obligation.

**How do I report a bug or a security problem?**
Open an issue with `nicheclock doctor` output. For a security problem, write privately to the maintainer listed on the repository instead of opening a public issue.

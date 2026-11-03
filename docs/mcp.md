---
title: MCP server
description: Connect Claude Desktop, Claude Code or Cursor to nicheclock over MCP: the free hosted server at nicheclock.elmrank.com with a key, or a local stdio server.
order: 5
publish: true
---

# MCP server

MCP lets an AI assistant call tools you give it. There are two ways to connect nicheclock: the [free hosted server](#hosted-server), which needs a key and no install, and a local server you run yourself. `nicheclock mcp` gives the local one four read-only tools, backed by your own data. It never fetches anything, never calls a model and never writes: when you ask it to judge a topic, your assistant's own model does the judging. Everything from here to the Hosted server section is about the local server; the hosted server has its own, different tools.

| Tool | Arguments | Returns |
|---|---|---|
| `trends_for_niche` | `project`, `niche`, `days=7`, `region=None` | Scored topics for one niche, best first: title, score, `why`, url, sources. |
| `upcoming` | `project`, `days=30`, `region=None` | Dated events soonest first, with `publish_by`, `days_until` and `publish_by_passed`. |
| `gsc_gaps` | `project`, `limit=50` | [Search Console](search-console.md) gaps by opportunity. Empty until you connect Search Console. |
| `explain_topic` | `project`, `topic`, `niche=None` | The evidence for one topic: its signals with sources and links, the niche definition, the site profile, your top search queries and a judging prompt. Nothing is scored on the server. Topics from publishers on the [model denylist](models.md#what-is-sent-and-what-is-not) return a notice instead. |

`region` is a country code such as `US`, or `global` for no filter; see [Regions](regions.md). Topics and gaps also carry the fields of the model you configured (`fit`, `fit_reason`, `angle`, `reader_questions`) when a run had one.

The data comes from the last `nicheclock run`. Run it at least once first, or schedule it ([Docker](docker.md) does that for you).

## Local server over stdio

Install with the MCP extra, then check that the server starts and lists its tools. `tests/mcp_list_tools.py` is the script our CI runs:

```bash
pipx install '.[mcp]'
NICHECLOCK_CONFIG=/absolute/path/to/nicheclock.yaml python3 tests/mcp_list_tools.py
```

It prints `tools: explain_topic, gsc_gaps, trends_for_niche, upcoming`. Use absolute paths everywhere below: desktop apps start the server from their own working directory and often without your shell's `PATH`. Find the binary with `command -v nicheclock`.

### Claude Code

```bash
claude mcp add nicheclock -- nicheclock -c /absolute/path/to/nicheclock.yaml mcp
claude mcp list
```

`claude mcp list` should show `nicheclock ... Connected`. Add `--scope user` to make it available in every project, or `--scope project` to write it to a `.mcp.json` you can commit (leave your config path out of anything you share).

### Claude Desktop

Open Settings, Developer, Edit Config, and add the server to `claude_desktop_config.json`. On macOS the file is at `~/Library/Application Support/Claude/claude_desktop_config.json`, on Windows at `%APPDATA%\Claude\claude_desktop_config.json`. Restart the app afterwards.

```json
{
  "mcpServers": {
    "nicheclock": {
      "command": "/absolute/path/to/nicheclock",
      "args": ["-c", "/absolute/path/to/nicheclock.yaml", "mcp"]
    }
  }
}
```

### Cursor

Put the same block in `~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` inside one project:

```json
{
  "mcpServers": {
    "nicheclock": {
      "command": "/absolute/path/to/nicheclock",
      "args": ["-c", "/absolute/path/to/nicheclock.yaml", "mcp"]
    }
  }
}
```

### If you run nicheclock in Docker

Let the client start a throwaway container that shares the same state directory. `-T` turns off the terminal, which stdio needs:

```bash
claude mcp add nicheclock -- docker compose -f /absolute/path/to/docker-compose.yml run --rm -T nicheclock mcp
```

```json
{
  "mcpServers": {
    "nicheclock": {
      "command": "docker",
      "args": ["compose", "-f", "/absolute/path/to/docker-compose.yml", "run", "--rm", "-T", "nicheclock", "mcp"]
    }
  }
}
```

## Things to ask

- "What is trending for the Feeder niche in project example this week? Give me the evidence."
- "Which upcoming events have a publish-by date in the next ten days?"
- "Use explain_topic on the top topic and tell me whether it fits the niche."
- "Compare this week's topics with my Search Console gaps and suggest three articles."

Your project and niche names are the ones in `nicheclock.yaml`; an unknown name returns an error that lists the valid ones.

## Hosted server

A free hosted MCP server runs at `https://nicheclock.elmrank.com/mcp`. You do not install anything; you need a key.

1. Open `https://nicheclock.elmrank.com/keys` and sign in with GitHub. The account needs a verified primary email, which is used only to limit keys per person.
2. Press Create key. The key starts with `nk_` and is shown once, because only its hash is stored. Copy it into an environment variable such as `NICHECLOCK_KEY`.
3. Add the server to your client with the snippets below.

Limits per key: 1,000 requests a day (UTC) and 60 a minute. Each person can hold 2 active keys. Over a limit the server answers 429 with `Retry-After`. Without a key, or with a revoked one, it answers 401 and the message names the key page. The same key works for the [REST API](api.md). The server speaks Streamable HTTP without sessions and answers with plain JSON; a GET returns 405, as the transport allows.

### Hosted tools

Six read-only tools, with no setup beyond the key. The first two read the same public catalog as the Trending now and Upcoming tabs of the website, updated once a day. `search_updates` reads the Google Search updates page. The other three match a profile you pass in the call.

| Tool | Arguments | Returns |
|---|---|---|
| `trending_now` | `region='global'`, `category`, `window='7d'`, `state='rising'`, `limit=20` | The topics rising now: label, Wikidata id, rise, volume with its unit, state, sources, category, a one-line reason and a link. |
| `upcoming_events` | `region='global'`, `days=30`, `limit=20`, `kind` | Dated events soonest first, with the date to publish by, days left, source link and licence. |
| `search_updates` | `since`, `kind`, `limit=20` | Whether a Google core or spam update is rolling out now, and a list of ranking updates, Search and Crawling documentation changes and Search Central blog posts, newest first. |
| `trends_for_profile` | `profile`, `limit`, `min_fit` | Rising Wikipedia pages that fit the topics in a profile, each with a fit score, the reason and its unit. |
| `upcoming_for_profile` | `profile`, `days`, `limit` | Dated events and yearly observances that fit the profile, with the date to publish by. |
| `resolve_topic` | `text` | The Wikidata id of a word, which a profile topic needs. |

The niche tools of the local server, `trends_for_niche` and `upcoming`, read niches you define in your own `nicheclock.yaml`. The hosted server publishes no niches, so it does not offer them, nor `niche_from_url`; use the self-hosted version for those. `gsc_gaps` and `explain_topic` are local too: the hosted side never holds your Search Console data. Only sources whose terms allow redistribution are served. No number is search volume: each is in the unit written next to it.

The examples below show the arguments of a call and a shortened reply. The values are made up, not live data; the fields are the real ones. Your assistant fills the arguments from a plain request, and the reply always carries the licence or attribution of its sources, which you keep when you show the data.

#### `trending_now`

Ask: "What is rising in technology in the United States this week?" `region` is `global` or a country code (`IN`, `CN`, `US`, `ID`, `PK`, `NG`, `BR`, `BD`, `RU`, `ET`); small countries have thin data and say so in `limited_note`. `category` is an id such as `sports`, `politics` or `technology`, and a wrong one returns the valid ids. `window` is `24h`, `48h` or `7d`. `state='rising'` keeps topics rising in that window; `state='all'` adds steady top items and topics that are fading or have ended, each marked in `state`. `rise` is a multiple of the topic's own median over the 14 days before the window, so 4.2 means +320%.

```json
{"region": "US", "category": "technology", "window": "7d", "state": "rising", "limit": 1}
```

```json
{
  "region": "US",
  "region_label": "United States",
  "window": "7d",
  "state": "rising",
  "category": "technology",
  "data_date": "2026-11-01",
  "generated_at": "2026-11-02T23:01Z",
  "limited_note": null,
  "note": "rise is a multiple of the topic's own median over the 14 days before the window (3.2 means +220%). Every volume is in its own unit; none is search volume.",
  "matching": 31,
  "items": [
    {
      "rank": 1,
      "label": "Example Phone",
      "qid": "Q1000001",
      "rise": 4.2,
      "volume": 182000,
      "unit": "pageviews/day",
      "state": "rising",
      "sources": ["news", "wikipedia"],
      "category": "technology",
      "category_label": "Technology",
      "why": "Wikipedia 182,000 pageviews/day, 4.2x its 14-day median; news 90 articles/day, 3.1x its 14-day median",
      "link": "https://en.wikipedia.org/wiki/Example_Phone"
    }
  ],
  "units": {"pageviews/day": "pageviews/day"},
  "licenses": {
    "news": {"source": "gdelt", "label": "GDELT 15-minute GKG files (news)", "license": "GDELT Project (https://www.gdeltproject.org/): free use with citation", "url": "https://www.gdeltproject.org/about.html"},
    "wikipedia": {"source": "wikimedia_pageviews", "label": "Wikipedia pageviews", "license": "CC0 (Wikimedia Analytics datasets)", "url": "https://dumps.wikimedia.org/other/pageviews/readme.html"}
  }
}
```

`data_date` is the last complete day the page views end on, and `matching` counts every row that fits the filters, not only the `limit` returned. A row for a topic without a Wikidata page has `qid: null`.

#### `upcoming_events`

Ask: "Which film releases in the next month have a publish-by date I can still make?" `days` is 1 to 365. `kind` is one of `film release`, `game release`, `sports event`, `election`, `holiday`, `festival`, `award ceremony` and a few more; a wrong one returns the valid kinds. A holiday or a notice that belongs to some countries is listed only for those regions, and an event with no region list is listed for every region. `publish_by` is the event date minus a default lead time for that kind of event, not a measurement; `publish_by_passed` says the date is behind you.

```json
{"region": "global", "days": 30, "kind": "film release", "limit": 1}
```

```json
{
  "region": "global",
  "days": 30,
  "kind": "film release",
  "data_date": "2026-11-02",
  "note": "publish_by = date - lag_days; lag_days is a default per event kind (see SCHEMA.md), not a measurement.",
  "matching": 12,
  "items": [
    {
      "date": "2026-11-20",
      "title": "Example Film",
      "kind": "film release",
      "category": "entertainment",
      "category_label": "Entertainment",
      "regions": ["global"],
      "publish_by": "2026-11-06",
      "lag_days": 14,
      "days_until": 17,
      "days_to_publish_by": 3,
      "publish_by_passed": false,
      "source": "wikidata",
      "source_url": "https://www.wikidata.org/wiki/Q1000003",
      "license": "CC0 (Wikidata)"
    }
  ]
}
```

#### `search_updates`

Ask: "Is a Google core update rolling out right now, and what did Google change in its Search documentation this month?" `rolling_now` is true while a core or spam update has started and Google has published no end date; `rolling_name` and `rolling_started` say which. `kind` is `ranking` (the updates of the last 12 months, each with `status` `started` while it rolls and `ended` after, the end date and the duration in days), `docs` (Search and Crawling documentation changes) or `blog` (Search Central blog posts). `since` is a date, `YYYY-MM-DD`; a ranking update counts when it started, ended or is still rolling on or after it. `data_date` is the day the Google Search Status Dashboard was last read, and `stale` is true when that is three days old or more. Summaries are Google's own wording, shortened, under the licence in `attribution`. The same data is on [the page](https://nicheclock.elmrank.com/google-updates/).

```json
{"since": "2026-09-01", "limit": 2}
```

```json
{
  "rolling_now": false,
  "rolling_name": null,
  "rolling_started": null,
  "rolling": [],
  "rolling_note": "rolling_now is true while a core or spam update has started and Google has published no end date. Other ranking updates are listed with status started or ended.",
  "data_date": "2026-11-03",
  "stale": false,
  "since": "2026-09-01",
  "kind": null,
  "matching": 14,
  "items": [
    {
      "date": "2026-10-30",
      "kind": "blog",
      "category": "Crawling",
      "title": "Supporting a shared AI training control for websites",
      "url": "https://developers.google.com/search/blog/2026/10/ai-training-control",
      "summary": "Today, we're announcing our plans to support a shared AI training control in robots.txt."
    },
    {
      "date": "2026-09-24",
      "kind": "ranking",
      "category": "Spam update",
      "title": "September 2026 spam update",
      "url": "https://status.search.google.com/incidents/XhUDXP7A67iHCD2kmbVu",
      "summary": "Rollout started on 2026-09-24 and ended on 2026-10-08, after 14 days.",
      "status": "ended",
      "ended": "2026-10-08",
      "duration_days": 14
    }
  ],
  "attribution": "Source: Google Search Central, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Summaries are Google's own wording, shortened; the title links to the original page. Ranking update names, dates and links come from the Google Search Status Dashboard (https://status.search.google.com/)."
}
```

#### `resolve_topic`

Ask: "Which Wikidata item is cricket?" It looks in nicheclock's own topic list first and then in Wikidata, and says when a word is ambiguous. Use the `qid` in a profile.

```json
{"text": "cricket"}
```

```json
{
  "qid": "Q5375",
  "label": "cricket",
  "description": "bat-and-ball sport",
  "confidence": 1,
  "ambiguous": false,
  "alternatives": [],
  "source": "nicheclock-list"
}
```

#### `trends_for_profile`

Ask: "Which rising pages in India fit a cricket blog?" The profile has `topics` (1 to 8, each `{"text": "..."}` or `{"qid": "Q5375", "label": "cricket", "role": "main"}`) and a `region` of `US`, `IN`, `BR` or `ID`; see [API](api.md#match-a-profile-agents) for every field. `limit` is 1 to 50 and `min_fit` is 0.35 to 1. The profile is used in memory and dropped.

```json
{"profile": {"topics": [{"text": "cricket"}], "region": "IN"}, "limit": 1}
```

```json
{
  "region": "IN",
  "data_date": "2026-11-01",
  "matched": 72,
  "returned": 1,
  "unresolved": [],
  "note": "Every volume has its own unit; none is search volume.",
  "rows": [
    {
      "qid": "Q1000002",
      "label": "Example Cricket Cup",
      "fit": 0.88,
      "why": "Sport: cricket, which you follow.",
      "relations": [{"kind": "subject", "via": "Q5375", "via_label": "cricket", "score": 0.8}],
      "trend": "rising",
      "rise_ratio": 10,
      "rise_basis": "last day against the median of the 14 days before",
      "volume": {"value": 12006, "unit": "pageviews/day"},
      "region": "IN",
      "source": "wikimedia_country",
      "source_url": "https://en.wikipedia.org/wiki/Example_Cricket_Cup",
      "license": "CC0 (Wikimedia Analytics datasets)"
    }
  ]
}
```

#### `upcoming_for_profile`

Ask: "Which dated events fit a cricket blog in the next two months, and when must I publish?" It matches Wikidata events and yearly observances; public holidays and notices carry no Wikidata id and are not matched, so use `upcoming_events` for those. With `cadence` in the profile it also proposes `your_slot`, the day of your usual publishing days that still makes the publish-by date.

```json
{"profile": {"topics": [{"qid": "Q5375", "label": "cricket", "role": "main"}], "region": "IN"}, "days": 60, "limit": 1}
```

```json
{
  "data_note": "Wikidata events with a date, and yearly observances (the next date of the rule on Wikidata). Public holidays and Federal Register notices carry no Wikidata id, so they are not matched.",
  "unresolved": [],
  "matched": 3,
  "returned": 1,
  "events": [
    {
      "qid": "Q1000004",
      "title": "Example Cricket Series",
      "kind": "sports event",
      "date": "2026-12-05",
      "days_until": 32,
      "publish_by": "2026-11-28",
      "publish_window_open": true,
      "fit": 0.9,
      "why": "Sport: cricket, which you follow.",
      "relations": [{"kind": "subject", "via": "Q5375", "via_label": "cricket", "score": 0.8}],
      "source": "wikidata",
      "source_url": "https://www.wikidata.org/wiki/Q1000004",
      "license": "CC0 (Wikidata)"
    }
  ]
}
```

### Hosted server in Claude Code

```bash
export NICHECLOCK_KEY=nk_your_key_here
claude mcp add --transport http nicheclock https://nicheclock.elmrank.com/mcp --header "Authorization: Bearer $NICHECLOCK_KEY"
claude mcp list
```

`claude mcp list` should show `nicheclock` as connected. The command stores the key in the Claude Code config for the scope you pick (`--scope user` for every project), so do not commit a `.mcp.json` that holds it.

### Hosted server in Cursor

Put this in `~/.cursor/mcp.json`, or `.cursor/mcp.json` inside one project. Cursor fills `${env:NICHECLOCK_KEY}` from your environment, so the key stays out of the file:

```json
{
  "mcpServers": {
    "nicheclock": {
      "url": "https://nicheclock.elmrank.com/mcp",
      "headers": {"Authorization": "Bearer ${env:NICHECLOCK_KEY}"}
    }
  }
}
```

### Hosted server in Claude Desktop

Settings, Developer, Edit Config, and add the block below to `claude_desktop_config.json`, then restart the app. The connectors screen of the app takes servers with no sign-in or with OAuth, and nicheclock uses a key in a header, so the config file goes through the `mcp-remote` bridge, which needs Node.js. Replace the key in `env`:

```json
{
  "mcpServers": {
    "nicheclock": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://nicheclock.elmrank.com/mcp", "--header", "Authorization:${NICHECLOCK_AUTH}"],
      "env": {"NICHECLOCK_AUTH": "Bearer nk_your_key_here"}
    }
  }
}
```

The header has no space after the colon on purpose: some apps split a space inside `args`, and the space inside `env` is safe.

### Check the connection without a client

```bash
curl -s https://nicheclock.elmrank.com/mcp \
  -H "Authorization: Bearer $NICHECLOCK_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
```

A reply with the six tools means the key works. Clients that can only connect without a header or through OAuth, such as the connector screens of some chat apps, cannot use the hosted server today. Revoke a key that leaks on the key page and create another.

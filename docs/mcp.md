---
title: MCP server
description: Connect Claude Desktop, Claude Code or Cursor to your nicheclock data over MCP, locally over stdio or, soon, through the hosted URL with an API key.
order: 5
publish: true
---

# MCP server

MCP lets an AI assistant call tools you give it. `nicheclock mcp` gives it four read-only ones, backed by your own data. It never fetches anything, never calls a model and never writes: when you ask it to judge a topic, your assistant's own model does the judging.

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

<span class="tag soon">Coming</span> The hosted service will expose the same kind of tools at `https://nicheclock.elmrank.com/mcp` with an API key (see [API](api.md) for how keys will work). The hosted tools are `trends_for_niche(niche, days)`, `upcoming(niche, days)` and `niche_from_url(url)`, and three that take a profile in the call and keep nothing: `trends_for_profile(profile, limit, min_fit)` (rising Wikipedia pages that fit the topics in the profile, each with a fit score, the reason and its unit), `upcoming_for_profile(profile, days, limit)` (dated events and yearly observances, with the date to publish by) and `resolve_topic(text)` (the Wikidata id of a word, which a profile topic needs). The profile tools cover the United States, India, Brazil and Indonesia; see [API](api.md#match-a-profile-agents) for the profile fields. There is no `gsc_gaps`: the hosted side never holds your Search Console data. Hosted keys are not open to the public yet, so the commands below are what we expect to work, not something you can run today:

```bash
claude mcp add --transport http nicheclock https://nicheclock.elmrank.com/mcp --header "Authorization: Bearer $NICHECLOCK_KEY"
```

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

The second block is the Cursor form. Whether and how Claude Desktop connects to a remote server with a custom header depends on your plan and app version; check Anthropic's documentation when the hosted service opens.

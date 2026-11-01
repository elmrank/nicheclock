---
title: Bring your own model
description: Connect Claude, OpenAI, Gemini, a local Ollama model, or your Claude Code or Codex sign-in, set a daily budget, and see what is and is not sent.
order: 4
publish: true
---

# Bring your own model

nicheclock collects with plain code and, when you bring a model, uses it to judge. You use your key or your own sign-in, set your own budget and pick your provider. nicheclock has no model of its own and never routes your calls through a server of ours. Nothing is on by default: no `llm:` section means no model call, ever.

> **Status.** Released. Gemini and Codex are <span class="tag soon">untested live</span>: they have unit tests with recorded replies but have not run against the real service yet, so expect rough edges and report them. Without a model, `run` uses rule-based scoring and works as described elsewhere in these docs.

## What a model does

Four jobs in `nicheclock run`, each switchable with `llm.tasks`, and a fifth during `init`:

| Task | What the model does | You see |
|---|---|---|
| `fit` | Scores each candidate topic 0 to 100 for your niche and says why in one sentence. | The feed score blends the rule score and the fit score; the reason is in `why`. |
| `junk` | Flags ephemeral or off-topic items that slipped through the keyword match. | Fewer items. |
| `merge` | Merges duplicates of one story reported by several sources. | One item with all sources. |
| `gaps` | Suggests angles and reader questions for your Search Console gaps. | Text next to each gap. |
| `profile` | Proposes niches from up to 50 page titles during `init --llm`. | A better first draft. |

Blend: `score = (1 - fit_weight) * rule_score + fit_weight * fit`, with `fit_weight` 0.6 by default. A topic the model did not judge counts as fit 50. Collection, matching, clustering and `publish_by` stay deterministic code. Judgements of the same input are cached for a week.

## Configuration

```yaml top
llm:
  provider: anthropic            # anthropic | openai | gemini | ollama | claude-code | codex
  model: claude-haiku-4-5        # required for API providers
  api_key_env: ANTHROPIC_API_KEY # the NAME of an environment variable; or api_key_file: path
  daily_budget_usd: 1.0          # hard stop for the UTC day; null means no cap
  max_items_per_run: 40          # topics judged per run, all projects together
  tasks: [fit, junk, merge, gaps]
  send_gsc_rows: false           # true also sends Search Console rows (page, clicks, position)
  fit_weight: 0.6
  timeout_s: 120
```

Keys are read from the environment variable or file you name, never from the YAML. An inline `api_key:` is rejected. `base_url` points at an OpenAI-compatible server, a proxy or a remote Ollama. `price_per_mtok: [input, output]` sets a price for a model the built-in table does not know; without a price the call is counted in tokens but not in dollars.

`projects[].profile` tells the model who the site is. `init` fills it from your home page, and you can edit it:

```yaml project
profile:
  title: Birdclub Demo
  description: Reviews and guides for bird feeders, birding optics and field guides.
```

Check the setup with one tiny call, which prints provider, model, latency, tokens and estimated cost, never the key:

```bash
nicheclock llm test
nicheclock llm usage --days 7
```

## Anthropic

```yaml top
llm:
  provider: anthropic
  model: claude-haiku-4-5
  api_key_env: ANTHROPIC_API_KEY
```

Create a key in the Anthropic Console, then `export ANTHROPIC_API_KEY=...` (in Docker: put it in `.env`). `model` is required; there is no built-in default, so pick one your account can use. A small, cheap model is enough for `fit`.

## OpenAI

```yaml top
llm:
  provider: openai
  model: gpt-6-luna
  api_key_env: OPENAI_API_KEY
```

With `base_url`, any OpenAI-compatible server works and the key becomes optional:

```yaml top
llm: {provider: openai, model: my-model, base_url: "http://localhost:8000/v1"}
```

## Gemini <span class="tag soon">untested live</span>

```yaml top
llm:
  provider: gemini
  model: gemini-3.5-flash-lite
  api_key_env: GEMINI_API_KEY
```

Not yet run against the real service; unit-tested only. The key is sent in the `x-goog-api-key` header, never in the URL. Thinking tokens count as output tokens in the cost estimate.

## Ollama, a local model

No key, no cost, nothing leaves your machine.

```bash
ollama pull llama3.2
```

```yaml top
llm: {provider: ollama, model: llama3.2, base_url: "http://localhost:11434"}
```

`base_url` defaults to `http://localhost:11434`. In Docker Compose, start the optional service and use its service name as the host:

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.2
```

```yaml top
llm: {provider: ollama, model: llama3.2, base_url: "http://ollama:11434"}
```

Small local models judge less reliably than large hosted ones, and they need RAM: plan on a few GB for a 3B model. Compare before you trust the ranking (see the last section on `nicheclock eval export`).

## Claude Code or Codex sign-in

If you already pay for a Claude or ChatGPT plan, nicheclock can call the `claude` or `codex` command line tool you are signed in to, instead of using an API key.

```yaml top
llm: {provider: claude-code}      # or: {provider: codex}; model is optional, the CLI's default is used
```

Codex is not yet run against the real service; unit-tested only. On your own machine, sign in once (`claude`, then log in; or `codex login`) and make sure the command is on your `PATH`. `cli_path` names a different binary. nicheclock runs the tool as a text backend only: for Claude Code with tools, user settings, hooks, MCP servers and session storage switched off; for Codex in a read-only sandbox without approvals. The child process gets a minimal environment (`HOME`, `PATH`, the sign-in directory, proxy and CA variables).

> **Your plan's terms apply to you.** Using a subscription this way is covered by the terms of that plan, not by nicheclock. Read them, and the usage limits, before you schedule daily runs: [Anthropic consumer terms](https://www.anthropic.com/legal/consumer-terms), [OpenAI terms of use](https://openai.com/policies/terms-of-use). If a plan does not allow automated use, use an API key instead.

In Docker the image does not contain either tool. Save this as `Dockerfile.cli` next to `docker-compose.yml` to build a small image on top of it, and mount your sign-in directory (`~/.claude` for Claude Code, `~/.codex` for Codex; `CLAUDE_CONFIG_DIR` and `CODEX_HOME` override those):

```dockerfile
FROM nicheclock:latest
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm \
 && npm install -g @anthropic-ai/claude-code @openai/codex \
 && rm -rf /var/lib/apt/lists/*
USER nicheclock
```

Then save this as `docker-compose.cli.yml`:

```yaml compose-override
# use with:  docker compose -f docker-compose.yml -f docker-compose.cli.yml up -d
services:
  nicheclock:
    image: nicheclock-cli:latest
    build:
      context: .
      dockerfile: Dockerfile.cli
    environment:
      CLAUDE_CONFIG_DIR: /login/claude
      CODEX_HOME: /login/codex
    volumes:
      - ${HOME}/.claude:/login/claude
      - ${HOME}/.codex:/login/codex
```

CI builds this image and checks that both commands start. A full sign-in and call from inside a container has not been tested. The mounts must be writable because the tools refresh their tokens. Anyone who can read those directories can use your plan, so treat them like a password.

## Budget

`daily_budget_usd` is a hard stop. Every call records tokens and an estimated cost in the local database; when the UTC day's total reaches the cap, the remaining topics keep their rule-based score and the run says so. Estimates come from a price table with the date it was read; the provider's invoice is authoritative. Ollama costs 0. `nicheclock llm usage` prints the ledger by day, model and task.

A run prints one line with the calls and tokens it used, for example `llm ollama/llama3.2: 3 calls, 360+120 tokens`. A tighter lever than the budget is `max_items_per_run`: it bounds the number of topics judged per run, and calls go out in batches of 12 topics.

## What is sent, and what is not

Sent to your provider, per call: your niche definition (name, keywords, entities, formats, excludes), the project's title and description, and for each candidate topic its title, the names of its sources, up to three short snippets of at most 200 characters, and the evidence line (for example `pageviews x3.2`). With Search Console connected: the 15 top query strings of your site.

Never sent: URLs of any kind, your configuration secrets and keys, Search Console rows (page, clicks, impressions, position) unless you set `send_gsc_rows: true`, your files, or anything from a source you disabled.

Also never sent: items from publishers whose terms forbid use by AI systems. A built-in denylist covers Google News and a list of publishers, checked against the domain of the item's URL, of a GDELT item's source and of the feed an RSS item came from. Add your own domains with `llm.denylist`, and switch one of your feeds off for the model with `llm: false` on its entry:

```yaml niche
rss:
  - https://blog.example/feed.xml
  - {url: https://news.example/feed.xml, llm: false}
```

```yaml top
llm:
  provider: ollama
  model: llama3.2
  denylist: [news.example]
```

Those items stay in your feed with their rule score and are marked `not sent to model: source terms`.

During `nicheclock init URL --llm` the model sees up to 50 page titles with their URL slugs and the site's host name, never page text. Its niches are merged into the rule-based draft, and you still edit the YAML before anything runs.

## Compare with and without a model

| | Without a model | With a model |
|---|---|---|
| Collection from sources | yes | same |
| Keyword and entity matching, relevance floor | yes | same |
| Clustering of near-identical titles | by entity and token overlap | plus semantic `merge` |
| Ranking | recency, rank, size of the signal, number of sources | blended with a fit score and a one-line reason |
| Items that match a word but not your audience | pass if they clear the floor | can be scored low or removed (`junk`) |
| Search Console gaps | listed with numbers | plus suggested angles and reader questions |
| Cost | none | tokens at your provider, capped by you |
| Reproducible run | yes | no: the same input can be judged a little differently |

To see the difference on your own data before deciding, export both rankings side by side:

```bash
nicheclock eval export --project example --days 7 --top 20
```

It writes a CSV with the rule-based and the model ranking and two empty columns to mark each row usable or not.

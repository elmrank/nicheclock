---
title: Troubleshooting
description: Fixes for the errors you are most likely to meet: config and identity, empty feeds, rate limits, Search Console tokens, Docker and MCP connections, model keys.
order: 11
publish: true
---

# Troubleshooting

Start with `nicheclock doctor`. It checks that the config parses, the identity is set and, unless you add `--offline`, that robots.txt of every enabled source allows you. Exit status 0 means all checks passed.

## Config and identity

**`config not found: ... (run nicheclock init <url> first)`**
The file is not where nicheclock looks. It tries `--config`, then `$NICHECLOCK_CONFIG`, then `./nicheclock.yaml`. Run the command from the folder that holds the file or pass `-c /path/to/nicheclock.yaml`.

**`identity.user_agent and identity.contact_email must be set before any fetch`**
There is no default identity, on purpose. Fill both fields. The contact needs an `@`.

**`unknown source 'x' (known: ...)`**
A name in a niche's `sources:` list is misspelled. The known names are in the message.

**`no URLs found, skipping` from `init`**
The site has no sitemap and no usable links on its home page, or robots.txt blocks you, or the site is unreachable. This is what `https://example.com` returns: it is a single page. Check `https://your-site.example/robots.txt` for a `Sitemap:` line, and try the sitemap URL in a browser.

## The feed is empty

A new run on a small, specific niche often finds nothing, and that is not a fault. Signals are kept only when they match your keywords or entities and clear the relevance floor ([how](configuration.md#how-a-topic-gets-into-the-feed)). To see why, check in this order:

1. `nicheclock preview --live` shows what a live pull of Google Trends and Wikipedia would match, and what `exclude:` would drop.
2. The run prints `N signals kept of M fetched` per niche. `0 kept of 10 fetched` means the sources answered and nothing matched.
3. Add keywords people actually search, add `entities:` that have a Wikipedia article, add feeds the niche's audience reads with `nicheclock edit --niche NAME --add-watch URL`, then run again.
4. Check `sources:`. A niche without `hn` or `wikipedia` has fewer chances to match.

## Rate limits and failing sources

**`gdelt: rate-limited, N request(s) queued for the next run`**
GDELT is strict. nicheclock waits ten seconds between requests and, when it still gets HTTP 429, skips GDELT for the rest of the run, saves the unfinished queries and runs them first next time. The exit status is 1, the reports are written. It evens out over a few runs, or remove `gdelt` from the niche.

**`robots.txt ... DISALLOWS`**
That host's robots.txt does not allow your identity for that path. nicheclock does not override it. For your own feeds, check the feed URL.

**Exit status 1**
One or more sources failed; the failures are listed at the end of the run. Status 2 is a configuration problem.

## Optional packages

**`this command needs the optional package 'mcp': pip install 'nicheclock[mcp]'`**
Install the extra named in the message: `mcp`, `gsc`, or `onboard`. With pipx, from a checkout: `pipx install --force '.[gsc,mcp]'`.

## Search Console

**`gsc.client_secret ... is not set`** or **`client secret file not found`**
Set `gsc.client_secret` to the downloaded JSON, and use a full path or `~`.

**`not connected: run nicheclock connect gsc --project NAME`**
No token file for that project name. Tokens are named `gsc-token-<project>.json` in `~/.config/nicheclock/` (or `$NICHECLOCK_CONFIG_DIR`).

**`token expired` or `token refresh failed`, about a week after connecting**
The consent screen is in *Testing* status, where Google expires refresh tokens after 7 days. Set it to *In production* and connect again. See [Search Console](search-console.md).

**Access denied or no rows**
The Google account you approved must have access to the exact `property`. Domain properties are written `sc-domain:example.com`.

## Docker

**The container is `unhealthy` and the log repeats `no /nicheclock/nicheclock.yaml yet`**
You have not run `init`. Run the command the log prints, once.

**`permission denied` writing to `/nicheclock`**
`nicheclock-data` is owned by someone other than the container user. Create it yourself before the first start and set `NICHECLOCK_UID` and `NICHECLOCK_GID` in `.env` to the output of `id -u` and `id -g`.

**`unhealthy` after a while, but the page loads**
The third check failed: no run started in two intervals plus an hour. Look at `docker compose logs nicheclock` for the last `nicheclock run` and its exit status.

**`port is already allocated`**
Something else uses 8080. Set `NICHECLOCK_HOST_PORT=8081` in `.env`.

**`env file .env not found` or `required` is not supported**
Compose v2.24 or newer is needed for the optional `.env`. Update Compose, or create an empty `.env`.

**A change to `nicheclock.yaml` seems ignored**
The next scheduled run reads it. To run now: `docker compose exec nicheclock nicheclock run`.

## MCP

**The client shows the server as failed or disconnected**
Use absolute paths for the command and the config (see [MCP](mcp.md)). Desktop apps do not start with your shell's `PATH`. Run the same command by hand: it should wait silently for input, and `Ctrl-C` ends it. Anything it prints to stdout other than protocol messages breaks the connection, so do not wrap it in a script that echoes.

**Tools return empty lists**
The server reads what `nicheclock run` stored. Run it once. Check the project and niche names against `nicheclock.yaml`; a wrong name returns an error that lists the valid ones.

## Models

**`environment variable NAME (llm.api_key_env) is empty or unset`**
`api_key_env` holds the *name* of a variable, not the key. Export the variable in the shell that runs nicheclock, or put it in `.env` for Docker.

**`ranking is rule-based this run`**
The model could not be reached or configured, so the run fell back. The line before it says why. `nicheclock llm test` reproduces it with one tiny call.

**`llm budget reached`**
`daily_budget_usd` for the UTC day is used up. Remaining topics keep their rule-based score. Raise the cap or wait until 00:00 UTC. `nicheclock llm usage` shows where it went.

**A model-scored item says `not sent to model: source terms`**
Its publisher is on the denylist. See [Models](models.md#what-is-sent-and-what-is-not).

Still stuck? Open an issue with the output of `nicheclock doctor` and the last 30 lines of the run, with your keys removed.

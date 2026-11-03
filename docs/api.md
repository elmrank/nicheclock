---
title: Hosted API
description: The free hosted nicheclock REST API and remote MCP endpoint: keys through GitHub sign-in, 1,000 requests a day, curl examples and the endpoints.
order: 10
publish: true
---

# Hosted API

> **Status.** Open to everyone and free. Sign in with GitHub at `https://nicheclock.elmrank.com/keys` to get a key. The service has no uptime promise, and the limits may change; see the [terms](/terms/) and the [privacy page](/privacy/). Nothing here changes how the self-hosted version works.

The hosted service has two parts. The [remote MCP server](mcp.md#hosted-server) is the main one: six read-only tools, with an example call and reply for each, that an agent can use without running anything. The REST API is small: the profile routes below and your keys. It serves public data only, and it needs no install.

## Self-hosted or hosted

| | Self-hosted | Hosted |
|---|---|---|
| You run it | yes | no |
| Your niches | any, edited by you | none: the hosted server reads the public catalog behind the Trending now and Upcoming tabs, and the profile you send |
| Sources | all of the [sources](sources.md) you enable | only those whose terms allow redistribution, the ones the website shows |
| Search Console gaps | yes | never: the hosted side holds no Search Console data |
| Your own model | yes | no, the client's model does the judging |
| Cost | your server | free quota, see below |

The niche tools of the local MCP server read niches you define, so they belong to the self-hosted version, and the hosted server does not offer them.

## Get a key

Sign in with GitHub at `https://nicheclock.elmrank.com/keys`. The account needs a verified primary email address; it is used only to limit keys per person. Press Create key: the page shows a key that starts with `nk_` once, and stores only its SHA-256 hash afterwards. What is stored about you is on the [privacy page](/privacy/). Rules:

- at most 2 active keys per email and per GitHub account;
- revoke a key from the page, or with `DELETE /v1/keys/{id}`;
- treat a key like a password; anything you send it can be read by whoever holds it.

## Free quota

| Limit | Value |
|---|---|
| Requests | 1,000 per day (UTC) and 60 per minute, per key |
| Profile calls | 30 per minute, per key (`trends_for_profile`, `upcoming_for_profile`, `resolve_topic`) |

Going over returns `429` with `Retry-After`. Every MCP call, whatever the tool, counts as one request. The free tier may change once the service has real usage; paid plans, if any, will be announced before they apply.

## Endpoints

Every request needs `Authorization: Bearer nk_...`. A request without a valid key gets `401` and a message that points to the key page. Errors are `{"error": {"code": "...", "message": "..."}}`.

| Endpoint | Returns |
|---|---|
| `POST /mcp` | Remote MCP, same key: `trending_now`, `upcoming_events`, `search_updates` and the three profile tools. See [MCP](mcp.md#hosted-server) for how to connect and for each tool's arguments and reply. |
| `POST /v1/profile/trends`, `/v1/profile/upcoming`, `/v1/profile/resolve` | The profile routes, below. |
| `GET /v1/keys` | Your keys and those of the same GitHub account. |
| `DELETE /v1/keys/{id}` | Revokes one of them. |

`trending_now`, `upcoming_events` and `search_updates` are MCP tools only; they have no REST route. Every item carries `source`, `source_url` and a `license` or `attribution`, so you can show where it came from. Keep the attribution when you display the data.

## Example

A tool call over plain HTTP, the same request an MCP client sends. The reply is shown on the [MCP page](mcp.md#hosted-tools):

```bash
export NICHECLOCK_KEY=nk_your_key_here
BASE=https://nicheclock.elmrank.com

curl -s -X POST "$BASE/mcp" -H "Authorization: Bearer $NICHECLOCK_KEY" -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "trending_now", "arguments": {"region": "US", "limit": 3}}}'

# your keys
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/keys"
```

## Match a profile (agents)

These routes take a small profile in the request body, and keep nothing: the profile is used in memory for the call and dropped. They answer from the Wikipedia pageview pools of the United States, India, Brazil and Indonesia (`region` is one of `US`, `IN`, `BR`, `ID`) and from Wikidata events, and the same three are MCP tools (`trends_for_profile`, `upcoming_for_profile`, `resolve_topic`).

| Endpoint | Body | Returns |
|---|---|---|
| `POST /v1/profile/trends` | `{"profile": {...}, "limit": 20, "min_fit": 0.5}` | Rising and steady pages that fit the profile, best fit first, one row per Wikidata id, each with `fit` (0 to 1), `why`, the `relations` that scored, `volume` with its unit, `source_url` and `license`. `limit` is 1 to 50, `min_fit` 0.35 to 1. |
| `POST /v1/profile/upcoming` | `{"profile": {...}, "days": 30, "limit": 20}` | Dated events and yearly observances that fit, soonest first, with `publish_by` and, when the profile has `cadence.days`, `your_slot`. `days` is 1 to 365. |
| `POST /v1/profile/resolve` | `{"text": "chess", "context": "a blog about board games"}` | The Wikidata id, label and description for a word, with `confidence`, `ambiguous` and `alternatives`. |

```bash
curl -s -X POST -H "Authorization: Bearer $NICHECLOCK_KEY" -H "Content-Type: application/json" \
  -d '{"profile": {"topics": [{"text": "chess"}], "region": "US"}, "limit": 5}' "$BASE/v1/profile/trends"
```

The profile has `topics` (1 to 8, each `{"qid": "Q718", "label": "chess", "role": "main"}` or just `{"text": "chess"}`, which is looked up in nicheclock's topic list and returned in `unresolved` when it is not there), `region`, and optionally `avoid` (ids whose pages are dropped) and `cadence` (`days` of the week, for `your_slot`). A call is limited to 8 KB of profile. These calls count against the same quota as the others.

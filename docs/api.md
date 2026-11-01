---
title: Hosted API
description: The hosted nicheclock REST API and remote MCP endpoint: free API keys with GitHub login, quota, curl examples. Coming soon, in private pilot.
order: 10
publish: true
---

# Hosted API

<span class="tag soon">Coming soon</span>

> **Status.** The hosted service is running as a private pilot for the team. Keys are not open to the public, GitHub login is switched off, and the commands on this page cannot be run by outsiders yet. This page describes what the service is built to do. Nothing here changes how the self-hosted version works.

The hosted API serves precomputed radar data for a catalog of niches over REST and over [remote MCP](mcp.md#hosted-server), so an agent or a script can ask "what is trending in this niche" without running anything. You can also ask it to build a niche from your site's URL.

## Self-hosted or hosted

| | Self-hosted | Hosted (coming soon) |
|---|---|---|
| You run it | yes | no |
| Your niches | any, edited by you | catalog niches, plus up to 3 custom niches built from a URL |
| Sources | all of the [sources](sources.md) you enable | only those whose terms allow redistribution: Wikimedia pageviews, Wikipedia facts, Wikidata, GDELT, Hacker News, Federal Register |
| Search Console gaps | yes | never: the hosted side holds no Search Console data |
| Your own model | yes | no, the client's model does the judging |
| Cost | your server | free quota, see below |

## Get a key

Sign in with GitHub at `https://nicheclock.elmrank.com/keys`. The key page shows a key that starts with `nk_` once, and stores only its SHA-256 hash afterwards. Rules:

- at most 2 active keys per email and per GitHub account;
- revoke a key from the page, or with `DELETE /v1/keys/{id}`;
- treat a key like a password; anything you send it can be read by whoever holds it.

## Free quota

| Limit | Value |
|---|---|
| Requests | 1,000 per day (UTC) and 60 per minute, per key |
| Custom niches | 3 per key, built from at most 50 pages each and refreshed daily |
| New custom-niche jobs | 20 per day for the whole service |

Going over returns `429` with `Retry-After`. The free tier may change once the service has real usage; paid plans, if any, will be announced before they apply.

## Endpoints

Every request needs `Authorization: Bearer nk_...`. Errors are `{"error": {"code": "...", "message": "..."}}`.

| Endpoint | Returns |
|---|---|
| `GET /v1/niches` | Catalog niches and your custom ones. |
| `GET /v1/niches/{id}/trends?days=7` | Scored topics, best first. `days` is 1 to 365. |
| `GET /v1/niches/{id}/upcoming?days=30` | Dated events with `publish_by`. `days` is 1 to 730. |
| `GET /v1/evidence/{item_id}` | The raw signals behind one topic. |
| `POST /v1/jobs` with `{"url": "https://your-site.example"}` | Queues a custom niche. `202` when queued, `200` if that site is already queued or done for your key. |
| `GET /v1/jobs/{id}` | `queued`, `running`, `done` or `failed`. |
| `GET /v1/keys` | Your keys and those of the same GitHub account. |
| `POST /mcp` | Remote MCP, same key. |

Every item carries `source`, `source_url` and a `license` or `attribution`, so you can show where it came from. Keep the attribution when you display the data.

## Examples

```bash
export NICHECLOCK_KEY=nk_your_key_here
BASE=https://nicheclock.elmrank.com

# which niches exist
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/niches"

# what is trending in one of them, last 7 days
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/niches/birdwatching/trends?days=7"

# dated events in the next 30 days, with the date to publish by
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/niches/birdwatching/upcoming?days=30"

# the signals behind one topic (ids come from the trends response)
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/evidence/birdwatching~a1b2c3d4e5f6"

# build a niche from your site, then poll the job
curl -s -X POST -H "Authorization: Bearer $NICHECLOCK_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://your-site.example"}' "$BASE/v1/jobs"
curl -s -H "Authorization: Bearer $NICHECLOCK_KEY" "$BASE/v1/jobs/JOB_ID"
```

A trends response looks like this (a shortened example, not live data):

```json
{
  "niche": "birdwatching",
  "items": [
    {
      "id": "birdwatching~a1b2c3d4e5f6",
      "title": "Example topic",
      "score": 71.4,
      "why": "hn 120 pts, 40 comments",
      "at": "2026-10-31T03:10:00Z",
      "source": "hn",
      "source_url": "https://news.ycombinator.com/item?id=1",
      "attribution": "Hacker News via Algolia HN Search API"
    }
  ]
}
```

Custom niches are built by a batch job on our side, not live, so allow up to a day. `niche_from_url` in the MCP server does the same thing.

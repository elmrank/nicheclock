# nicheclock hosted: free API key + remote MCP

A Cloudflare Worker (TypeScript) that serves the precomputed nicheclock radar over REST `/v1` and a
remote MCP endpoint `/mcp`, with GitHub-OAuth key issuance, per-key quota and D1 metering. Data layout:
[`SCHEMA.md`](SCHEMA.md).
The self-hosted package (AGPL-3.0, stdio MCP) is unchanged; this directory is only the hosted side.

```
operator cron: nicheclock collect+score ──► python -m nicheclock.hosted_export ──► wrangler r2 object put ──► R2
                                                                                                            │
client / agent ── Authorization: Bearer nk_… ──► Worker (src/) ──► reads R2 JSON, counts in D1 ◄────────────┘
```

No crawling or scoring happens in the Worker (10 ms CPU on the free tier): a request reads one JSON
object, filters it, and counts itself.

## Endpoints

All need `Authorization: Bearer nk_…` (except the key page). Errors: `{"error": {"code", "message"}}`.

| | |
|---|---|
| `GET /v1/niches` | catalog niches + your custom ones |
| `GET /v1/niches/{id}/trends?days=7&region=US` | scored topics (`days` 1-365); optional `region`: country + global items, see SCHEMA.md |
| `GET /v1/niches/{id}/upcoming?days=30&region=US` | dated events with `publish_by` (`days` 1-730); optional `region` |
| `GET /v1/github/rising?window=daily&language=Python&limit=25` | fastest-rising GitHub repos by stars (`window` daily, weekly, monthly; same data as the MCP tool `github_rising`; reads `catalog/github-rank.json`; served to every key: `github` is a public source) |
| `GET /v1/evidence/{item_id}` | raw signals behind a topic |
| `POST /v1/jobs` `{"url": "https://site.com"}` | queue a custom niche (202; 200 if that site is already queued/done for this key) |
| `GET /v1/jobs/{id}` | your job: `queued → running → done \| failed` |
| `GET /v1/keys`, `DELETE /v1/keys/{id}` | list / revoke keys of the same GitHub account |
| `GET /v1/admin/report` | admin scope: weekly numbers (below) |
| `GET /v1/admin/jobs?status=queued`, `POST /v1/admin/jobs/{id}` `{"status": "running\|done\|failed", "error"?}` | admin scope: the operator cron's job queue |
| `POST /mcp` | remote MCP, see below |
| `GET /keys`, `/auth/github/login`, `/auth/github/callback` | key page + GitHub OAuth |
| `POST /v1/profile/trends`, `/v1/profile/upcoming`, `/v1/profile/resolve` | match a profile (or resolve a word) against the niche pool and events; same Bearer key; see "Profile tools" below |

**Source allow-list at serving time** (`src/sources.ts`): wikimedia_pageviews, wikipedia (facts), wikidata,
gdelt, hn, federal_register, github. `internal` and `admin` keys also see nager, google_trends, apple (internal use is not redistribution; items using them are tagged `scope: internal`, see SCHEMA.md). Anything else present in
R2 is dropped, as is any item without `source_url` and `license`/`attribution`.

**Limits per key** (`wrangler.toml [vars]`): 1,000 requests/day (UTC), 60/minute → `429` + `Retry-After`;
3 custom niches (`403 custom_niche_limit`); 2 active keys per email and per GitHub account
(`409 key_limit`); `JOBS_DAILY_CAP` (20) new custom-niche jobs per day for the whole service.

### Quota design (D1 only, no Durable Objects)

Per authenticated request: 1 D1 read (key row + today's request total in one query), 1 write (minute
counter upsert), 1 write (`usage_daily` upsert with bytes). The daily limit is exact up to racing
concurrent requests; a request refused by the daily limit costs 0 writes. An in-isolate counter refuses
keys that are already over the minute limit without touching D1, so one hammering client cannot burn the
free D1 write budget (it can only overshoot by the number of isolates it reaches). `last_used_at` is
refreshed at most hourly. Chosen over Durable Objects because D1 is needed anyway and the plan forbids
spending money before the thresholds; if D1 writes become the limit (about 50k requests/day, below), move
the minute counter to a Durable Object or raise to the paid plan.

### Admin report and the free-tier numbers

`GET /v1/admin/report` (admin key) returns: keys (total, active, revoked, `created_7d`, `active_7d` from
`usage_daily`), requests (today, 7 days, per day, per endpoint with bytes), jobs (total, `created_7d`,
by status) and `free_tier_estimate` with `pct` and `over_threshold` (> 70%, the plan's line, as a 7-day
average). The estimate is `usage_daily × cost model` (`src/admin.ts`): 2 D1 rows written and 12 rows read
per request, R2 class B reads per endpoint (niches 2, trends/upcoming/evidence/`tools/call` 1). It is a
lower bound for Workers requests (401s and the key page are not metered). The Cloudflare dashboard is
authoritative. Limits used (read 2026-10-31):

* Workers Free: 100,000 requests/day, 10 ms CPU — <https://developers.cloudflare.com/workers/platform/limits/>
* D1 Free: 5,000,000 rows read/day, 100,000 rows written/day, 5 GB — <https://developers.cloudflare.com/d1/platform/pricing/> and <https://developers.cloudflare.com/d1/platform/limits/>
* R2 Free: 10 GB-month, 1,000,000 class A and 10,000,000 class B operations/month — <https://developers.cloudflare.com/r2/pricing/>

Unverified: whether D1 counts primary-key index maintenance as extra rows written; the tables are
`WITHOUT ROWID` with the key as primary key, so an upsert should be one row. Check against the D1 dashboard
after the first week of use.

## Remote MCP (`POST /mcp`)

Stateless Streamable HTTP, JSON-RPC 2.0, same Bearer key. Follows MCP revision **2026-07-28** (current on
2026-10-31): no sessions, per-request `_meta` (`io.modelcontextprotocol/protocolVersion`,
`…/clientCapabilities`), `MCP-Protocol-Version` + `Mcp-Method` (+ `Mcp-Name` for `tools/call`) headers
validated (`400 -32020`), `server/discover`, `UnsupportedProtocolVersion` (`-32022`), `resultType` on results,
`ttlMs`/`cacheScope` on `tools/list`. Clients on earlier revisions (2025-11-25, 2025-06-18, 2025-03-26) get the
`initialize` handshake, still stateless (no `Mcp-Session-Id`). `GET`/`DELETE` → 405; foreign `Origin` → 403
(`ALLOWED_ORIGINS` adds more). Responses are plain `application/json`, never SSE.

Tools: `trends_for_niche(niche, days=7, region?)`, `upcoming(niche, days=30, region?)`, `niche_from_url(url)` (the local
`gsc_gaps` is not offered: no user GSC data on the hosted side). An unknown niche returns a tool error listing
the valid ids. Call `niche_from_url` again with the same URL to see the job status.

Profile tools (`src/profile-tools.ts`): `trends_for_profile(profile, limit=20, min_fit=0.5)`, `upcoming_for_profile(profile, days=30, limit=20)` and `resolve_topic(text, context?)`, also REST `POST /v1/profile/trends`, `/v1/profile/upcoming` and `/v1/profile/resolve`. The agent carries the profile in each call and nothing is kept. They read build-time **shards** of the niche pool (files of about 25 KB, never over 150 KB, under `/catalog-data/shards/`, through the assets binding), not the 5 MB pool. Measured on the deployed Worker, `trends_for_profile` costs a median 16 ms and a p95 of 42 ms of CPU, which is over the free plan's 10 ms; the numbers, reply sizes and the title-only limit are in `docs/personalization.md`.

```bash
curl -s https://nicheclock.elmrank.com/mcp -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Profile page endpoints (no key)

The profile page of the site has three buttons that call the Worker without a key (`src/suggest*.ts`). Same origin only, `application/json` bodies, no CORS, limits per address in Cloudflare rate-limiting bindings (below), no logging of any input:

| | |
|---|---|
| `GET /v1/suggest/status` | `{resolve, site, youtube}`; `youtube` is true only when the secret `YOUTUBE_API_KEY` is set |
| `POST /v1/resolve` `{"text": "chess"}` | "Look up a word": the topic list first, Wikidata only for a word not in it. 20 a minute per address |
| `POST /v1/suggest/site` `{"url": "https://example.org"}` | "Read my site": robots.txt (RFC 9309), one sitemap (at most 300 KB), at most 8 page titles (64 KB each), at most 26 subrequests, one redirect on the same host at most; user agent `nicheclock-suggest/1.0 (+https://nicheclock.elmrank.com/bot; oss@elmrank.com)`. 3 a minute per address, one read of a site a minute, 2,000 reads a day in all |
| `POST /v1/suggest/youtube` `{"channel": "@example-chess"}` | "Read my channel": 3 YouTube Data API units a call, 2 a minute per address, 2,000 units a day in all (one D1 row per UTC day). 404 `not_available` without the key |

Limits: Cloudflare rate-limiting bindings (`RL_RESOLVE` 20/60 s, `RL_SITE` 3/60 s, `RL_YOUTUBE` 2/60 s, `RL_PROFILE` 30/60 s per API key, `RL_HOST` 1/60 s per host name; they only support 10 or 60 second windows). The counter of a key lives inside Cloudflare, so nothing of ours stores an address, not even hashed (the bindings count per Cloudflare location and are approximate: three reads of one host in a row were all let through, so only the daily D1 caps are exact). The only counters in D1 are two global numbers per UTC day with no caller: `youtube_units_daily` (cap 2,000 units) and `site_reads_daily` (cap 2,000 reads), `migrations/0004_no_address_counters.sql`. Condition (b) of the plan, no logging of what a person typed: no `console` call in these files (`test/privacy.test.ts` greps `src/suggest*.ts`, `profile-tools.ts`, `shards.ts` and `robotstxt.ts`), fixed error messages, and the replies do not hold the input.

### The YouTube key (optional, billing OFF)

"Read my channel" is hidden until the Worker has the secret `YOUTUBE_API_KEY`. Make the key in a **new Google Cloud project that has no billing account**, separate from the project of the catalog build:

1. Open <https://console.cloud.google.com/> and create a project (project picker, **New project**), for example `nicheclock-suggest`. Do not link a billing account; **Billing** should say the project has none.
2. **APIs & Services, Library**: search **YouTube Data API v3** and press **Enable**. It has a free quota of 10,000 units a day and needs no billing.
3. **APIs & Services, Credentials, Create credentials, API key.** Copy the key once.
4. Open the key (**Edit API key**): under **API restrictions** choose **Restrict key**, tick only **YouTube Data API v3**, save. Leave application restrictions at **None**: Cloudflare's addresses are not stable, so an IP restriction would break it.
5. From the machine that deploys, with the Cloudflare token in the environment: `cd hosted && npx wrangler secret put YOUTUBE_API_KEY` and paste the key at the prompt. The key lives only as a Worker secret; never put it in a file, a commit or a shell history line.
6. Check: `curl -s https://nicheclock.elmrank.com/v1/suggest/status` shows `"youtube":true` and the button appears on the profile page. To switch it off: `npx wrangler secret delete YOUTUBE_API_KEY`.

The Worker caps itself at 2,000 of the 10,000 daily units. The key is sent only to `www.googleapis.com`, in the request URL of the three calls, and is never in a reply or a log.

## Local development (no Cloudflare login)

```bash
cd hosted
npm install            # .npmrc sets legacy-peer-deps (vitest 5 / vitest-plugin peer ranges)
npm test               # vitest in workerd (miniflare), offline
npm run typecheck
node scripts/dev-seed.mjs   # local D1 migrations + fixture R2 objects + one internal and one external key (printed once, throwaway)
npm run dev                  # wrangler dev --local on http://localhost:8787
curl -s -H "Authorization: Bearer nk_…" http://localhost:8787/v1/niches
```

GitHub login locally needs `.dev.vars` (copy `.dev.vars.example`) with a real OAuth App whose callback is
`http://localhost:8787/auth/github/callback`, and `PUBLIC_BASE_URL=http://localhost:8787` in `.dev.vars`.
`scripts/mint-key.mjs` creates internal/admin keys without OAuth (prints SQL to stdout, key to stderr).

## Trending now page (static, port 4300)

The homepage candidate is a static page, `hosted/site/trending/`: a multi-source "Trending now" table (filters, sort, "Trend breakdown" per row) and an Upcoming tab. It reads `meta.json`, `trending.json`, `upcoming.json` (schema: `hosted/site/catalog-data/SCHEMA.md`).

```
python hosted/site/trending/serve.py            # builds if out of date, then http://localhost:4300/
python hosted/site/trending/serve.py --data DIR # another data directory
python hosted/site/trending/serve.py --port 4301 --rebuild
python hosted/site/trending/serve.py --host 0.0.0.0   # reachable from the LAN, e.g. http://192.168.100.4:4300 (default is 127.0.0.1)
node hosted/site/trending/test.js               # rules, filters, sort, URL state, fixture checks
node hosted/site/trending/test-match.js         # "Your niche": matching weights, profile validation and storage (docs/personalization.md)
node hosted/site/trending/test-privacy.js       # "Your niche": sentinel strings through the whole flow, HAR of every request (needs puppeteer-core and a Chrome)
```

`serve.py` uses only the Python standard library (no npm, no npx, no shell, no subprocess), so it runs the same on Windows, macOS and Linux, and it sends the same Content-Security-Policy as `site/static/_headers`. GitHub rank tab: from `github-rank.json` (hidden when the file is absent). YouTube: the catalog build writes git-ignored sidecars `youtube[-ID].json` next to the data (API terms: 30-day storage); they are merged by Wikidata id at load, and when a sidecar is missing the YouTube chip and filter entry are hidden. Run the catalog build in the same checkout, or point `--data` at the directory that has them. Regions: when `meta.regions` lists countries the page loads each one's own `trending-<ID>.json` the first time it is picked; a source with no data for a country is greyed out as "no data here", and a region marked `limited` shows "Limited data for this region". Data: `hosted/site/catalog-data/` when it holds the three files, else the sample fixture `site/trending/fixture/` (invented numbers; the page shows a "Sample data" banner). `site/build.py --home trending` makes the page the homepage of the full site with the old demo report at `/demo/`; that rewrites the tracked `site/public/`, so do not commit or deploy it before the homepage switch is approved. Criteria and self-score: `docs/ui-criteria-trending.md`.

## Operator side: export + upload

```bash
# catalog: every niche of a dedicated project whose sources are the allowed ones
python -m nicheclock.hosted_export -c nicheclock.yaml --out ./hosted-out --project catalog
# custom niche made by a queued job: whole project -> custom/<key_id>/<niche_id>/
python -m nicheclock.hosted_export -c job.yaml --out ./hosted-out --project job-site \
    --custom-key k_0123456789abcdef --niche-id custom-site-com
# print the upload commands (never executed by the exporter)
python -m nicheclock.hosted_export ... --print-upload nicheclock-data
```

Then, by hand, with the Cloudflare token in the environment: `scripts/upload-r2.sh ./hosted-out nicheclock-data`
(`wrangler r2 object put … --remote`, `niches.json` last). The exporter drops non-allowed signals, re-scores
topics that used them so score and `why` stay clean, and writes only `schema: 1` JSON. Job loop for the cron:
`GET /v1/admin/jobs?status=queued` → `POST /v1/admin/jobs/{id} {"status":"running"}` → crawl, score, export,
upload → `{"status":"done"}` (or `failed` with `error`). Nothing in the repo runs these.

## Deploy (needs a Cloudflare token with Workers + D1 + R2)

The Worker is `nicheclock-hosted`, the D1 database `nicheclock`, the R2 bucket `nicheclock-data`. GitHub OAuth stays
**disabled on purpose** (`/auth/github/*` answers 503 `not_configured`) until an OAuth App is configured. Steps, for a rebuild:

1. `export CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=…`
2. `npx wrangler d1 create nicheclock` → put the id in `wrangler.toml` (`database_id`).
3. `npx wrangler r2 bucket create nicheclock-data`
4. `npx wrangler d1 migrations apply nicheclock --remote` (0003 adds `youtube_units_daily`; 0004 drops `suggest_rate` and adds `site_reads_daily`: apply 0004 just before the deploy that no longer uses `suggest_rate`).
5. Create the GitHub OAuth App (under the `elmrank` organisation): homepage `https://nicheclock.elmrank.com`,
   callback `https://nicheclock.elmrank.com/auth/github/callback`. Then
   `npx wrangler secret put GITHUB_CLIENT_ID | GITHUB_CLIENT_SECRET | SESSION_SECRET` (SESSION_SECRET: `openssl rand -hex 32`).
   The Worker also reads two optional secrets: `YOUTUBE_API_KEY` (see "Profile page endpoints", it switches the "Read my channel" button on) the Worker needs no other secret for the limits.
6. `npx wrangler deploy` (the route `nicheclock.elmrank.com` is a Custom Domain; `workers_dev = false`). The elmrank.com zone must be on the same account.
7. Mint internal keys and one admin key: `node scripts/mint-key.mjs …` then `npx wrangler d1 execute nicheclock --remote --file mint.sql`.
8. Export + upload the catalog once, then `curl` `/v1/niches` with an internal key.

### Rollback

1. Take it offline: `npx wrangler delete --name nicheclock-hosted` (removes the Worker and, with it, its Custom Domain
   route). If the DNS record for `nicheclock.elmrank.com` is left behind, delete it in the Cloudflare dashboard
   (DNS, zone elmrank.com) or with the API (`DELETE /zones/<zone>/dns_records/<id>`).
2. Data is separate from the Worker: `npx wrangler d1 delete nicheclock` and
   `npx wrangler r2 bucket delete nicheclock-data` (empty the bucket first) only if you want it gone.
3. Revoke a single key without touching anything else: `DELETE /v1/keys/{id}` with that key, or
   `npx wrangler d1 execute nicheclock --remote --command "UPDATE keys SET revoked_at = datetime('now') WHERE id = 'k_…'"`.
4. Redeploy the previous version: `npx wrangler rollback` (versions are listed by `npx wrangler versions list`).

## Before opening keys to outsiders

* Publish an acceptable-use policy at `/terms` and a privacy notice at `/privacy` (what is stored: verified GitHub email and numeric id, key hashes, per-day request/byte counts, custom-niche URLs; no IPs in D1).
* Decide the free-tier limits and whether an account-age check is needed against bulk key creation.
* Custom niches built from an outsider's URL are crawled by the operator: state that only sites you own or that are public may be submitted; the crawler respects robots.txt.
* Configure Cloudflare and GitHub OAuth credentials (see Deploy).

## Known gaps (honest list)

* The three profile-page buttons and the profile tools were tested offline (fake network and fake assets in `test/`, a stubbed Worker in `test-privacy.js`) and, for CPU and reply size, on the deployed Worker (docs/personalization.md). "Read my channel" has not been run against the real YouTube API until a key is added.
* Reading a site cannot tell what a public-looking name resolves to (a Worker has no DNS lookup); see docs/personalization.md, "Read my site".
* The GitHub OAuth flow is tested with a mocked GitHub (`test/github.test.ts`), not against github.com. Unverified end to end.
* The MCP server is tested with raw JSON-RPC against both protocol eras, not with a real client (Claude, Cursor, MCP Inspector). Unverified.
* No account-deletion endpoint, no email notices, no abuse heuristics beyond the limits above, no CORS.
* `hosted_export` is a standalone module (`python -m nicheclock.hosted_export`), not yet a `nicheclock` CLI subcommand, to stay clear of `cli.py`.

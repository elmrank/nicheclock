---
title: Search Console
description: Connect Google Search Console with your own OAuth client and read-only access, step by step, to see content gaps and your real index lag.
order: 9
publish: true
---

# Search Console

Connecting Search Console gives nicheclock two things: **gaps** (queries your site already shows up for but answers badly, or not at all) and your measured **index lag**, which makes `publish_by` dates yours instead of a guess.

It works with your own Google Cloud OAuth client, so no third party sits between you and Google. nicheclock asks for one scope, `https://www.googleapis.com/auth/webmasters.readonly`: read-only, no write access to your property. The token is stored on your machine with mode 600 and is never logged.

Search Console data is yours and stays local. The hosted service never sees it, and a [model](models.md) sees it only as the 15 top query strings, or as rows if you set `send_gsc_rows: true`.

## Steps

You need a Google account that owns or has access to the property in Search Console, and a few minutes in the Google Cloud Console. Screen names change from time to time; the names below are the ones in the console at the time of writing.

1. **Create a project.** In the [Google Cloud Console](https://console.cloud.google.com/) create a project, for example `nicheclock`.
2. **Enable the API.** Under APIs and Services, Library, find *Google Search Console API* and click Enable.
3. **Set up the consent screen** (called *Google Auth Platform* in newer consoles). Choose user type *External*, fill in the app name and your email, and add `https://www.googleapis.com/auth/webmasters.readonly` as the only scope. Add your own Google account as a test user.
4. **Create the OAuth client.** Under Credentials, Create credentials, OAuth client ID. Application type: **Desktop app**. Download the JSON file.
5. **Save the file** where nicheclock can read it and keep it private:

   ```bash
   mkdir -p ~/.config/nicheclock
   mv ~/Downloads/client_secret_*.json ~/.config/nicheclock/client_secret.json
   chmod 600 ~/.config/nicheclock/client_secret.json
   ```

6. **Point the project at it** in `nicheclock.yaml`. `property` is exactly what Search Console shows: `sc-domain:example.com` for a domain property, or `https://example.com/` for a URL-prefix property.

   ```yaml project
   gsc:
     property: "sc-domain:example.com"
     client_secret: ~/.config/nicheclock/client_secret.json
     min_impressions: 50      # optional: a query needs this many impressions to count as a gap
     max_position: 20         # optional: a gap is worse than this average position
     sync_hours: 20           # optional: re-sync at most this often during `run`
   ```

7. **Connect.** This opens the Google consent screen in your browser:

   ```bash
   pip install 'nicheclock[gsc]'    # or: pipx install '.[gsc]' from a checkout
   nicheclock connect gsc --project example
   ```

   After you approve, nicheclock saves the token, pulls the last 28 days and the 28 days before, and prints the gaps. From then on `nicheclock run` re-syncs by itself.

Google shows a warning that the app is not verified. That is expected for a personal OAuth client you made yourself: you are the developer and the only user. Continue only if the app name is the one you created.

> **The seven-day trap.** Google expires refresh tokens after 7 days while the consent screen's publishing status is *Testing* ([Google's OAuth documentation](https://developers.google.com/identity/protocols/oauth2)). Then `run` reports `token expired` and you have to connect again. For a client that only you use, set the publishing status to *In production* on the consent screen; it stays unverified, which is fine for yourself.

## On a server without a browser

The consent screen redirects to `http://localhost:PORT/` on the machine running `connect`. On a remote machine, forward a port and print the URL instead of opening a browser:

```bash
ssh -L 8085:127.0.0.1:8085 you@your-server
nicheclock connect gsc --project example --no-browser --port 8085
```

Open the printed URL in the browser on your own computer. The tunnel carries the redirect back. Or run step 7 on your laptop and copy the token file (below).

## In Docker

The container cannot receive the browser redirect, so connect on your own computer and copy two files into the state directory:

```bash
cp ~/.config/nicheclock/client_secret.json nicheclock-data/secrets/
cp ~/.config/nicheclock/gsc-token-example.json nicheclock-data/secrets/
```

and set `client_secret` to the path inside the container, `/nicheclock/secrets/client_secret.json`. The container looks for tokens in `/nicheclock/secrets/` (`NICHECLOCK_CONFIG_DIR`). Token file names are `gsc-token-<project name>.json`.

## What you get

A **gap** is a query with at least `min_impressions` impressions whose best position is worse than `max_position`, or that no known page covers: every word of the query must appear in a page URL path or title, and known pages are those seen in Search Console plus your sitemap.

**Index lag** is the median number of days from a page's sitemap `lastmod` to its first impression, for pages published in the last 90 days, and needs at least three such pages. It is stored as `index_lag_days` and used for `publish_by`. Until you have it, `default_lag_days` applies.

Gaps appear in `feed.json`, the HTML report and the [MCP](mcp.md) tool `gsc_gaps`. With a model configured, each gap also gets a suggested angle and reader questions.

## Disconnect

Delete the token file (`~/.config/nicheclock/gsc-token-<project>.json`) and remove the grant at <https://myaccount.google.com/permissions>. Remove the `gsc:` block from the project to stop syncing.

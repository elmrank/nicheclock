-- Cookieless page-view counts for the nicheclock site (POST /b, src/beacon.ts).
-- Daily totals only: one row per UTC day x host x path x device x source, never one row per view.
-- host: the production hostname, or "local" for dev servers. No IP, cookie, user agent, referrer URL or id.

CREATE TABLE pageviews_daily (
  day    TEXT NOT NULL,               -- UTC date, YYYY-MM-DD
  host   TEXT NOT NULL,
  path   TEXT NOT NULL,               -- location.pathname, no query or hash
  device TEXT NOT NULL,               -- mobile (viewport <= 767 px) | desktop
  source TEXT NOT NULL,               -- search | social | ai | direct | internal | other (kind of referrer at entry)
  views                     INTEGER NOT NULL DEFAULT 0,
  seconds_total             INTEGER NOT NULL DEFAULT 0,  -- visible seconds, each view capped at 1800
  views_with_internal_click INTEGER NOT NULL DEFAULT 0,  -- views with a click to another page of the site
  -- max scroll bucket per view (share of the page height seen; >= 99% counts as 100)
  scroll_0   INTEGER NOT NULL DEFAULT 0,
  scroll_25  INTEGER NOT NULL DEFAULT 0,
  scroll_50  INTEGER NOT NULL DEFAULT 0,
  scroll_75  INTEGER NOT NULL DEFAULT 0,
  scroll_90  INTEGER NOT NULL DEFAULT 0,
  scroll_100 INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, host, path, device, source)
) WITHOUT ROWID;

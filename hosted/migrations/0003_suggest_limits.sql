-- Limits of the three public "Sends ..." buttons (POST /v1/resolve, /v1/suggest/site, /v1/suggest/youtube; src/suggest-limits.ts).
-- No URL, word, handle or profile is ever written here. A bucket is a kind plus a keyed hash (HMAC-SHA-256 with a Worker secret, cut to
-- 64 bits) of the caller's address and the hour or day, or of the target host and the minute; it cannot be turned back into either, and a
-- row is deleted once its window is over. youtube_units_daily is one number per UTC day: the YouTube Data API units spent (cap 2,000).

CREATE TABLE suggest_rate (
  bucket  TEXT PRIMARY KEY,           -- "<kind>:<16 hex>"
  n       INTEGER NOT NULL,           -- calls in the window
  expires INTEGER NOT NULL            -- unix seconds; the row is deleted after this
) WITHOUT ROWID;

CREATE TABLE youtube_units_daily (
  day   TEXT PRIMARY KEY,             -- UTC date, YYYY-MM-DD
  units INTEGER NOT NULL
) WITHOUT ROWID;

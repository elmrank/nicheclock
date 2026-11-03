-- Privacy: nothing of ours stores an address, not even as a keyed hash. Per-caller limits of the profile-page buttons are Cloudflare's
-- rate-limiting bindings now (state inside Cloudflare, wrangler.toml), so the hashed-address counters of 0003 are gone.
-- site_reads_daily is a global number with no caller in it: site reads today (cap 2,000, src/suggest-limits.ts).
DROP TABLE suggest_rate;

CREATE TABLE site_reads_daily (
  day TEXT PRIMARY KEY,               -- UTC date, YYYY-MM-DD
  n   INTEGER NOT NULL
) WITHOUT ROWID;

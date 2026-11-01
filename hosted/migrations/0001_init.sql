-- nicheclock hosted: keys, metering, rate counters, custom-niche jobs.
-- All timestamps are ISO-8601 UTC strings; `day` is YYYY-MM-DD (UTC).

CREATE TABLE keys (
  id              TEXT PRIMARY KEY,                 -- public key id, "k_" + 16 hex
  owner_github_id INTEGER,                          -- NULL for operator-minted internal/admin keys
  email           TEXT NOT NULL,                    -- lower-cased verified primary GitHub email
  hash            TEXT NOT NULL UNIQUE,             -- SHA-256 hex of the full "nk_..." key; the key itself is never stored
  scope           TEXT NOT NULL DEFAULT 'external' CHECK (scope IN ('external', 'internal', 'admin')),
  plan            TEXT NOT NULL DEFAULT 'free',     -- pricing hook: limits are looked up by plan
  created_at      TEXT NOT NULL,
  last_used_at    TEXT,                             -- refreshed at most once per hour (saves D1 writes)
  revoked_at      TEXT
);
CREATE INDEX keys_email_active ON keys (email) WHERE revoked_at IS NULL;
CREATE INDEX keys_owner ON keys (owner_github_id);

-- Metering. One row per key/day/endpoint template (never per concrete id: bounded cardinality).
CREATE TABLE usage_daily (
  key_id   TEXT NOT NULL,
  day      TEXT NOT NULL,
  endpoint TEXT NOT NULL,                           -- e.g. "GET /v1/niches/:id/trends", "POST /mcp tools/call"
  requests INTEGER NOT NULL DEFAULT 0,
  bytes    INTEGER NOT NULL DEFAULT 0,              -- response body bytes
  PRIMARY KEY (key_id, day, endpoint)
) WITHOUT ROWID;
CREATE INDEX usage_daily_day ON usage_daily (day);

-- Per-minute counter (the 60/min limit). Old rows of a key are pruned when it opens a new minute.
CREATE TABLE rate_minute (
  key_id TEXT NOT NULL,
  minute INTEGER NOT NULL,                          -- floor(epoch seconds / 60)
  n      INTEGER NOT NULL,
  PRIMARY KEY (key_id, minute)
) WITHOUT ROWID;

-- Custom niches from a URL. The Worker only enqueues; the operator's cron processes and uploads to R2.
CREATE TABLE jobs (
  id          TEXT PRIMARY KEY,                     -- "j_" + 12 hex
  key_id      TEXT NOT NULL,
  url         TEXT NOT NULL,
  niche_id    TEXT NOT NULL,                        -- "custom-<host-slug>", unique per key among non-failed jobs
  status      TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
  error       TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX jobs_key ON jobs (key_id, niche_id);
CREATE INDEX jobs_status ON jobs (status, created_at);
CREATE INDEX jobs_created ON jobs (created_at);

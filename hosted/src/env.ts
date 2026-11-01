export interface Env {
  DB: D1Database;
  BUCKET: R2Bucket;

  // [vars] in wrangler.toml
  PUBLIC_BASE_URL: string;
  DAILY_LIMIT: string;
  MINUTE_LIMIT: string;
  MAX_CUSTOM_NICHES: string;
  MAX_KEYS_PER_EMAIL: string;
  JOBS_DAILY_CAP: string;
  ALLOWED_ORIGINS: string;
  /** "on" opens /keys and /auth/* (GitHub key issuance). Anything else: those paths are 404 and nobody can get a new key. */
  KEY_SIGNUP?: string;
  /** "off" serves the static site without the Basic login (launch). Anything else, or unset: the gate is on and fails closed without BASIC_PASSWORD. */
  SITE_GATE?: string;

  // Worker secrets (wrangler secret put ...)
  GITHUB_CLIENT_ID?: string;
  GITHUB_CLIENT_SECRET?: string;
  SESSION_SECRET?: string;
  /** Basic-auth password of the static site (gate.ts); the user name is BASIC_USER and has no default. */
  BASIC_PASSWORD?: string;
  BASIC_USER?: string;

  /** Workers static assets (site/public), reached only through gate.ts. */
  ASSETS: Fetcher;
}

export type Scope = "external" | "internal" | "admin";

/** An authenticated API key row (never contains the secret). */
export interface KeyRow {
  id: string;
  owner_github_id: number | null;
  email: string;
  scope: Scope;
  plan: string;
  created_at: string;
  last_used_at: string | null;
}

export function intVar(value: string | undefined, fallback: number): number {
  const n = Number.parseInt(value ?? "", 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

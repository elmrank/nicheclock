/**
 * Limits of the three public "Sends ..." buttons (POST /v1/resolve, /v1/suggest/site, /v1/suggest/youtube) and of the profile tools.
 *
 * Nothing of ours stores an address. Per-caller limits are Cloudflare's rate-limiting bindings (wrangler.toml `[[ratelimits]]`): the counter
 * for a key lives inside Cloudflare, not in D1, R2 or anywhere we can read. The key is the caller's address (the profile tools: the API key id),
 * or, for a read of a site, the host name. Windows can only be 10 or 60 seconds.
 *
 * What we do store are two global counters with no caller in them: `youtube_units_daily` (YouTube Data API units spent today) and
 * `site_reads_daily` (site reads today). One number per UTC day each.
 */
import type { Env, KeyRow } from "./env";
import { ApiError } from "./http";

export type Kind = "resolve" | "site" | "youtube";

/** calls allowed per 60 seconds per caller (the values of the bindings in wrangler.toml, said again in the message) */
export const PER_MINUTE: Record<Kind | "profile", number> = { resolve: 20, site: 3, youtube: 2, profile: 30 };
export const SITE_DAILY_CAP = 2000;

function tooMany(message: string): ApiError {
  return new ApiError(429, "rate_limited", message, { "retry-after": "60" });
}

function binding(env: Env, kind: Kind | "profile" | "host"): RateLimit | undefined {
  return { resolve: env.RL_RESOLVE, site: env.RL_SITE, youtube: env.RL_YOUTUBE, profile: env.RL_PROFILE, host: env.RL_HOST }[kind];
}

/** Throws a 429 ApiError when the caller is over the limit of this button. Without the binding (tests, local dev) there is no limit. */
export async function checkCaller(env: Env, req: Request, kind: Kind): Promise<void> {
  const rl = binding(env, kind);
  if (!rl) return;
  const { success } = await rl.limit({ key: req.headers.get("cf-connecting-ip") ?? "unknown" });
  if (!success) throw tooMany(`This button can be used ${PER_MINUTE[kind]} times a minute from one address. Wait a minute, or type your topics instead.`);
}

/** The profile tools: per API key, 30 a minute */
export async function checkProfileRate(env: Env, key: KeyRow): Promise<void> {
  const rl = binding(env, "profile");
  if (!rl) return;
  const { success } = await rl.limit({ key: key.id });
  if (!success) throw tooMany(`The profile tools can be used ${PER_MINUTE.profile} times a minute per key. Wait a minute.`);
}

/** One read of a given site a minute for everybody, so the button cannot be used to hammer one site. The key is the host; the state is in Cloudflare. */
export async function checkHost(env: Env, host: string): Promise<void> {
  const rl = binding(env, "host");
  if (!rl) return;
  const { success } = await rl.limit({ key: host });
  if (!success) throw tooMany("This site was read a moment ago. Try again in a minute, or type your topics instead.");
}

/** Count one site read in today's global total. False (nothing counted) when the day's cap is reached. */
export async function spendSiteRead(env: Env, now = new Date()): Promise<boolean> {
  const day = now.toISOString().slice(0, 10);
  const row = await env.DB.prepare("INSERT INTO site_reads_daily (day, n) VALUES (?1, 1) ON CONFLICT(day) DO UPDATE SET n = n + 1 WHERE n < ?2 RETURNING n")
    .bind(day, SITE_DAILY_CAP)
    .first<{ n: number }>();
  return row !== null;
}

/** The daily budget of YouTube Data API units: one D1 row per UTC day. Returns false (and spends nothing) when `units` more would pass the cap. */
export async function spendYoutubeUnits(env: Env, units: number, cap: number, now = new Date()): Promise<boolean> {
  const day = now.toISOString().slice(0, 10);
  const row = await env.DB.prepare(
    "INSERT INTO youtube_units_daily (day, units) VALUES (?1, ?2) ON CONFLICT(day) DO UPDATE SET units = units + ?2 WHERE units + ?2 <= ?3 RETURNING units",
  )
    .bind(day, units, cap)
    .first<{ units: number }>();
  return row !== null;
}

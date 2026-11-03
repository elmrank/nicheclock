/**
 * Limits for the three public "Sends ..." buttons (POST /v1/resolve, /v1/suggest/site, /v1/suggest/youtube).
 *
 * Two layers, both free:
 *   1. The `BURST` rate-limiting binding of Cloudflare (wrangler.toml `[[ratelimits]]`): at most 6 calls per 10 s per address. The binding
 *      only knows windows of 10 or 60 seconds and keeps its counters inside Cloudflare, so nothing of ours stores an address for it.
 *   2. The hourly and daily caps of the plan (20 lookups an hour, 5 site reads an hour, 5 channel reads a day per address; 1 read of a given
 *      site per minute for everybody) are counters in D1, because a window of an hour or a day is beyond what the binding can express.
 *      A bucket is `<kind>:<16 hex>` = a keyed hash (HMAC-SHA-256, secret = SESSION_SECRET) of the caller's address plus the window index,
 *      or of the target host plus the minute. It cannot be turned back into an address or a host, and the row is deleted after its window.
 *      No URL, word, handle or profile reaches this file or the table.
 */
import type { Env } from "./env";
import { ApiError, encoder, hex } from "./http";

export type Kind = "resolve" | "site" | "youtube";

/** calls allowed per address in one window: [count, window in seconds] */
export const LIMITS: Record<Kind, [number, number]> = {
  resolve: [20, 3600],
  site: [5, 3600],
  youtube: [5, 86400],
};
export const HOST_WINDOW: [number, number] = [1, 60];

let keyCache: { secret: string; key: CryptoKey } | null = null;

async function hmac(env: Env, text: string): Promise<string> {
  const secret = env.SESSION_SECRET || "nicheclock-limits";
  if (!keyCache || keyCache.secret !== secret) {
    keyCache = { secret, key: await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]) };
  }
  const sig = await crypto.subtle.sign("HMAC", keyCache.key, encoder.encode(text));
  return hex(sig).slice(0, 16);
}

/** Count one call in a window. Returns the number of calls so far in that window (this one included). */
async function bump(env: Env, bucket: string, windowSeconds: number, nowSec: number): Promise<number> {
  const expires = (Math.floor(nowSec / windowSeconds) + 1) * windowSeconds + 60;
  const row = await env.DB.prepare(
    "INSERT INTO suggest_rate (bucket, n, expires) VALUES (?1, 1, ?2) ON CONFLICT(bucket) DO UPDATE SET n = n + 1 RETURNING n",
  )
    .bind(bucket, expires)
    .first<{ n: number }>();
  if (Math.random() < 0.05) await env.DB.prepare("DELETE FROM suggest_rate WHERE expires < ?1").bind(nowSec).run();
  return row?.n ?? 1;
}

function tooMany(message: string, retryAfter: number): ApiError {
  return new ApiError(429, "rate_limited", message, { "retry-after": String(Math.max(1, retryAfter)) });
}

/** Throws a 429 ApiError when the caller is over a limit. Counts the call otherwise. */
export async function checkCaller(env: Env, req: Request, kind: Kind, now = new Date()): Promise<void> {
  const ip = req.headers.get("cf-connecting-ip") ?? "unknown";
  const nowSec = Math.floor(now.getTime() / 1000);
  if (env.BURST) {
    const { success } = await env.BURST.limit({ key: `${kind}:${ip}` });
    if (!success) throw tooMany("Too many requests in a row. Wait a few seconds.", 10);
  }
  const [max, win] = LIMITS[kind];
  const n = await bump(env, `${kind}:${await hmac(env, `${ip}|${kind}|${Math.floor(nowSec / win)}`)}`, win, nowSec);
  if (n > max) {
    const left = win - (nowSec % win);
    throw tooMany(`This button can be used ${max} times ${win === 3600 ? "an hour" : "a day"} from one address. Try again later, or type your topics instead.`, left);
  }
}

/** One read of a given site per minute across all callers, so the button cannot be used to hammer one site. */
export async function checkHost(env: Env, host: string, now = new Date()): Promise<void> {
  const nowSec = Math.floor(now.getTime() / 1000);
  const [max, win] = HOST_WINDOW;
  const n = await bump(env, `host:${await hmac(env, `${host}|${Math.floor(nowSec / win)}`)}`, win, nowSec);
  if (n > max) throw tooMany("This site was read a moment ago. Try again in a minute, or type your topics instead.", win - (nowSec % win));
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

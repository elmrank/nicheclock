/**
 * Quota + metering on D1 (no Durable Objects).
 *
 * Per authenticated request:
 *   1 read  : key lookup + today's request total (in authenticate()), so the daily limit is exact up
 *             to concurrent requests racing past it
 *   1 write : per-minute counter upsert (rate_minute)
 *   1 write : usage_daily upsert (requests + bytes), after the response body is known
 * A request refused by the daily limit costs 0 writes. A request refused by the per-minute limit
 * costs 1. To keep a hammering client from burning the free D1 write budget, an in-isolate counter
 * short-circuits keys already over the minute limit locally (a local count never exceeds the real
 * one, so this never refuses a request D1 would have allowed).
 */
import type { Authed } from "./keys";
import type { Env } from "./env";
import { intVar } from "./env";
import { ApiError, dayOf, secondsToMidnight } from "./http";

export interface Limits {
  daily: number;
  perMinute: number;
}

/** Pricing hook: limits are chosen by key.plan. Only `free` exists today. */
export function limitsFor(env: Env, _plan: string): Limits {
  return { daily: intVar(env.DAILY_LIMIT, 1000), perMinute: intVar(env.MINUTE_LIMIT, 60) };
}

const local = new Map<string, { minute: number; n: number }>();

function tooMany(kind: "day" | "minute", limit: number, retryAfter: number): ApiError {
  return new ApiError(
    429,
    kind === "day" ? "daily_quota_exceeded" : "rate_limited",
    kind === "day" ? `daily quota of ${limit} requests used up; resets at 00:00 UTC` : `more than ${limit} requests in a minute`,
    { "retry-after": String(retryAfter) },
  );
}

/** Throws 429 (+ Retry-After) or returns the remaining daily requests after this one. */
export async function enforce(env: Env, key: Authed, now: Date): Promise<{ remainingDay: number; limits: Limits }> {
  const limits = limitsFor(env, key.plan);
  if (key.used_today >= limits.daily) throw tooMany("day", limits.daily, secondsToMidnight(now));

  const minute = Math.floor(now.getTime() / 60_000);
  const retryMinute = 60 - Math.floor((now.getTime() / 1000) % 60);
  const mem = local.get(key.id);
  if (mem && mem.minute === minute && mem.n >= limits.perMinute) throw tooMany("minute", limits.perMinute, retryMinute);

  const row = await env.DB.prepare(
    "INSERT INTO rate_minute (key_id, minute, n) VALUES (?1, ?2, 1) ON CONFLICT (key_id, minute) DO UPDATE SET n = n + 1 RETURNING n",
  )
    .bind(key.id, minute)
    .first<{ n: number }>();
  const n = row?.n ?? 1;
  local.set(key.id, { minute, n: mem && mem.minute === minute ? Math.max(mem.n + 1, n) : n });
  if (local.size > 5000) local.clear();
  if (n === 1) {
    // first request of a new minute: prune this key's old counters (also keeps the table tiny)
    await env.DB.prepare("DELETE FROM rate_minute WHERE key_id = ?1 AND minute < ?2").bind(key.id, minute - 2).run();
  }
  if (n > limits.perMinute) throw tooMany("minute", limits.perMinute, retryMinute);
  return { remainingDay: Math.max(0, limits.daily - key.used_today - 1), limits };
}

/** Add one request (and its response size) to usage_daily. */
export async function record(env: Env, keyId: string, endpoint: string, bytes: number, now: Date): Promise<void> {
  await env.DB.prepare(
    `INSERT INTO usage_daily (key_id, day, endpoint, requests, bytes) VALUES (?1, ?2, ?3, 1, ?4)
     ON CONFLICT (key_id, day, endpoint) DO UPDATE SET requests = requests + 1, bytes = bytes + excluded.bytes`,
  )
    .bind(keyId, dayOf(now), endpoint, bytes)
    .run();
}

/** Test hook: forget the in-isolate counters. */
export function resetLocalCounters(): void {
  local.clear();
}

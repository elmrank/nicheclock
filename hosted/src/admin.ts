/**
 * GET /v1/admin/report: the weekly numbers for the operator.
 *
 * The free-tier percentages are an ESTIMATE built from our own usage_daily counts and a fixed cost
 * model (COST_MODEL below); the authoritative numbers are in the Cloudflare dashboard / GraphQL
 * analytics. Limits used (checked against the docs on 2026-10-31):
 *   Workers Free  100,000 requests/day, 10 ms CPU   https://developers.cloudflare.com/workers/platform/limits/
 *   D1 Free       5,000,000 rows read/day, 100,000 rows written/day, 5 GB   https://developers.cloudflare.com/d1/platform/pricing/
 *   R2 Free       10 GB-month, 1,000,000 class A/month, 10,000,000 class B/month   https://developers.cloudflare.com/r2/pricing/
 */
import type { Env } from "./env";
import { dayOf, isoNow } from "./http";

export const FREE_LIMITS = {
  workers_requests_per_day: 100_000,
  d1_rows_written_per_day: 100_000,
  d1_rows_read_per_day: 5_000_000,
  r2_class_b_per_month: 10_000_000,
  r2_class_a_per_month: 1_000_000,
} as const;

/** Alert threshold from the plan: > 70% of any limit as a 7-day average. */
export const THRESHOLD_PCT = 70;

/**
 * Per authenticated request. Not measured; derived from the code paths in quota.ts / keys.ts / catalog.ts.
 *  d1_writes: rate_minute upsert + usage_daily upsert (+ hourly last_used_at, + one prune per key-minute: ignored)
 *  d1_reads : key row + SUM over today's usage_daily rows of that key (<= ~12 rows) -> 12
 *  r2_b     : object GETs (niches: catalog + custom list = 2; trends/upcoming/evidence/tools/call = 1; rest 0)
 * Unauthenticated hits (401s, OAuth pages) are not in usage_daily, so Workers requests are a lower bound.
 */
export const COST_MODEL = { d1_writes_per_request: 2, d1_reads_per_request: 12 } as const;

export function r2ClassBFor(endpoint: string): number {
  if (endpoint === "GET /v1/niches") return 2;
  if (/^GET \/v1\/(niches\/:id\/(trends|upcoming)|evidence\/:id)$/.test(endpoint)) return 1;
  if (endpoint === "POST /mcp tools/call") return 1;
  return 0;
}

function pct(used: number, limit: number): number {
  return Math.round((used / limit) * 1000) / 10;
}

function daysAgo(now: Date, n: number): string {
  return dayOf(new Date(now.getTime() - n * 86_400_000));
}

function metric(used: number, limit: number) {
  const p = pct(used, limit);
  return { estimated_used: Math.round(used), limit, pct: p, over_threshold: p > THRESHOLD_PCT };
}

export async function buildReport(env: Env, now = new Date()) {
  const today = dayOf(now);
  const d7 = daysAgo(now, 6); // 7 days including today
  const since7 = `${daysAgo(now, 7)}T${now.toISOString().slice(11)}`;

  const [keysAgg, scopes, active7, perEndpoint, perDay, jobsAgg, jobsStatus] = await env.DB.batch([
    env.DB.prepare(
      `SELECT COUNT(*) AS total,
              SUM(revoked_at IS NULL) AS active,
              SUM(revoked_at IS NOT NULL) AS revoked,
              SUM(created_at >= ?1) AS created_7d
         FROM keys`,
    ).bind(since7),
    env.DB.prepare("SELECT scope, COUNT(*) AS n FROM keys WHERE revoked_at IS NULL GROUP BY scope"),
    env.DB.prepare("SELECT COUNT(DISTINCT key_id) AS n FROM usage_daily WHERE day >= ?1 AND requests > 0").bind(d7),
    env.DB.prepare(
      "SELECT endpoint, SUM(requests) AS requests, SUM(bytes) AS bytes FROM usage_daily WHERE day >= ?1 GROUP BY endpoint ORDER BY requests DESC",
    ).bind(d7),
    env.DB.prepare("SELECT day, SUM(requests) AS requests, SUM(bytes) AS bytes FROM usage_daily WHERE day >= ?1 GROUP BY day ORDER BY day").bind(d7),
    env.DB.prepare("SELECT COUNT(*) AS total, SUM(created_at >= ?1) AS created_7d FROM jobs").bind(since7),
    env.DB.prepare("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"),
  ]);

  const k = (keysAgg?.results[0] ?? {}) as Record<string, number | null>;
  const endpoints = (perEndpoint?.results ?? []) as { endpoint: string; requests: number; bytes: number }[];
  const days = (perDay?.results ?? []) as { day: string; requests: number; bytes: number }[];
  const total7 = days.reduce((s, d) => s + d.requests, 0);
  const todayReq = days.find((d) => d.day === today)?.requests ?? 0;
  const avgDay = total7 / 7;
  const r2b7 = endpoints.reduce((s, e) => s + r2ClassBFor(e.endpoint) * e.requests, 0);
  const j = (jobsAgg?.results[0] ?? {}) as Record<string, number | null>;

  return {
    generated_at: isoNow(now),
    window: { days: 7, from: d7, to: today },
    keys: {
      total: k.total ?? 0,
      active: k.active ?? 0,
      revoked: k.revoked ?? 0,
      created_7d: k.created_7d ?? 0,
      active_7d: (active7?.results[0] as { n: number } | undefined)?.n ?? 0,
      active_by_scope: Object.fromEntries(((scopes?.results ?? []) as { scope: string; n: number }[]).map((r) => [r.scope, r.n])),
    },
    requests: {
      today: todayReq,
      last_7d: total7,
      per_day: days,
      per_endpoint_7d: endpoints,
    },
    jobs: {
      total: j.total ?? 0,
      created_7d: j.created_7d ?? 0,
      by_status: Object.fromEntries(((jobsStatus?.results ?? []) as { status: string; n: number }[]).map((r) => [r.status, r.n])),
    },
    free_tier_estimate: {
      note: "estimated from usage_daily x COST_MODEL; the Cloudflare dashboard is authoritative. Percentages use the 7-day average.",
      threshold_pct: THRESHOLD_PCT,
      cost_model: COST_MODEL,
      workers_requests_per_day: metric(avgDay, FREE_LIMITS.workers_requests_per_day),
      d1_rows_written_per_day: metric(avgDay * COST_MODEL.d1_writes_per_request, FREE_LIMITS.d1_rows_written_per_day),
      d1_rows_read_per_day: metric(avgDay * COST_MODEL.d1_reads_per_request, FREE_LIMITS.d1_rows_read_per_day),
      r2_class_b_per_month: metric((r2b7 / 7) * 30, FREE_LIMITS.r2_class_b_per_month),
      r2_class_a_per_month: { estimated_used: null, limit: FREE_LIMITS.r2_class_a_per_month, pct: null, over_threshold: false, note: "uploads happen from the operator's machine, count them there (files per `wrangler r2 object put` run x runs/month)" },
      sources: {
        workers: "https://developers.cloudflare.com/workers/platform/limits/",
        d1: "https://developers.cloudflare.com/d1/platform/pricing/",
        r2: "https://developers.cloudflare.com/r2/pricing/",
      },
    },
  };
}

/**
 * Custom niches from a URL. The Worker never crawls (10 ms CPU on the free tier): it validates the
 * URL and writes a D1 row. The operator's cron reads queued jobs through the admin endpoints, crawls,
 * scores, uploads custom/<key_id>/<niche>/... to R2 and marks the job done.
 */
import type { Env, KeyRow } from "./env";
import { intVar } from "./env";
import { ApiError, dayOf, isoNow, randomHex, secondsToMidnight } from "./http";
import { CUSTOM_PREFIX } from "./catalog";

export interface JobView {
  id: string;
  url: string;
  niche_id: string;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  niche_path?: string;
  key_id?: string;
}

const JOB_COLS = "id, key_id, url, niche_id, status, error, created_at, updated_at, finished_at";
const BLOCKED_TLDS = new Set(["local", "localhost", "internal", "lan", "home", "corp", "intranet", "test", "invalid", "example"]);

export interface Target {
  url: string;
  host: string;
  niche_id: string;
}

/** Normalize and validate a user-supplied site URL. Public hostnames only; no credentials. */
export function parseTarget(input: unknown): Target {
  if (typeof input !== "string" || !input.trim()) throw new ApiError(400, "invalid_url", "url is required");
  const raw = input.trim();
  if (raw.length > 2048) throw new ApiError(400, "invalid_url", "url is too long");
  let u: URL;
  try {
    u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    throw new ApiError(400, "invalid_url", "not a valid URL");
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw new ApiError(400, "invalid_url", "only http(s) URLs");
  if (u.username || u.password) throw new ApiError(400, "invalid_url", "URLs with credentials are refused");
  const host = u.hostname.toLowerCase().replace(/\.$/, "");
  const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.includes(":") || host.startsWith("[");
  const tld = host.split(".").pop() ?? "";
  if (isIp || !host.includes(".") || BLOCKED_TLDS.has(tld) || host === "localhost") {
    throw new ApiError(400, "invalid_url", "needs a public hostname (no IPs, localhost or internal names)");
  }
  if (u.port && u.port !== "80" && u.port !== "443") throw new ApiError(400, "invalid_url", "non-standard ports are refused");
  const slug = host
    .replace(/^www\./, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  if (!slug) throw new ApiError(400, "invalid_url", "cannot derive a niche id from this host");
  u.hash = "";
  return { url: u.toString(), host, niche_id: `${CUSTOM_PREFIX}${slug}` };
}

export function view(row: Omit<JobView, "niche_path">, includeKey = false): JobView {
  const v: JobView = { ...row };
  if (!includeKey) delete v.key_id;
  if (row.status === "done") v.niche_path = `/v1/niches/${row.niche_id}/trends`;
  return v;
}

/** Idempotent per key + site: re-posting a URL of an existing (non-failed) custom niche returns it. */
export async function createJob(env: Env, key: KeyRow, urlInput: unknown, now = new Date()): Promise<{ job: JobView; created: boolean }> {
  const t = parseTarget(urlInput);
  const existing = await env.DB.prepare(
    `SELECT ${JOB_COLS} FROM jobs WHERE key_id = ?1 AND niche_id = ?2 AND status != 'failed' ORDER BY created_at DESC LIMIT 1`,
  )
    .bind(key.id, t.niche_id)
    .first<JobView>();
  if (existing) return { job: view(existing), created: false };

  const cap = intVar(env.JOBS_DAILY_CAP, 20);
  const today = await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs WHERE created_at >= ?1")
    .bind(`${dayOf(now)}T00:00:00.000Z`)
    .first<{ n: number }>();
  if ((today?.n ?? 0) >= cap) {
    throw new ApiError(429, "jobs_capacity", "custom niche capacity for today is used up; try after 00:00 UTC", {
      "retry-after": String(secondsToMidnight(now)),
    });
  }

  const max = intVar(env.MAX_CUSTOM_NICHES, 3);
  const id = `j_${randomHex(6)}`;
  const ts = isoNow(now);
  // The niche limit is checked inside the INSERT (distinct non-failed niches of this key).
  const res = await env.DB.prepare(
    `INSERT INTO jobs (id, key_id, url, niche_id, status, created_at, updated_at)
     SELECT ?1, ?2, ?3, ?4, 'queued', ?5, ?5
      WHERE (SELECT COUNT(DISTINCT niche_id) FROM jobs WHERE key_id = ?2 AND status != 'failed') < ?6`,
  )
    .bind(id, key.id, t.url, t.niche_id, ts, max)
    .run();
  if (!res.meta.changes) throw new ApiError(403, "custom_niche_limit", `at most ${max} custom niches per key`);
  const row = await env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs WHERE id = ?1`).bind(id).first<JobView>();
  return { job: view(row as JobView), created: true };
}

export async function getJob(env: Env, key: KeyRow, id: string): Promise<JobView> {
  const row = await env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs WHERE id = ?1 AND key_id = ?2`).bind(id, key.id).first<JobView>();
  if (!row) throw new ApiError(404, "not_found", "job not found");
  return view(row);
}

// ---- admin side (operator cron) ------------------------------------------------------------------

export async function listJobsAdmin(env: Env, status: string | null, limit: number): Promise<JobView[]> {
  const q = status
    ? env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs WHERE status = ?1 ORDER BY created_at LIMIT ?2`).bind(status, limit)
    : env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs ORDER BY created_at DESC LIMIT ?1`).bind(limit);
  return (await q.all<JobView>()).results.map((r) => view(r, true));
}

const NEXT: Record<string, string[]> = { queued: ["running", "done", "failed"], running: ["done", "failed"], done: [], failed: [] };

export async function updateJobAdmin(env: Env, id: string, body: unknown, now = new Date()): Promise<JobView> {
  const b = (typeof body === "object" && body !== null ? body : {}) as Record<string, unknown>;
  const status = typeof b.status === "string" ? b.status : "";
  if (!["running", "done", "failed"].includes(status)) throw new ApiError(400, "invalid_status", "status must be running, done or failed");
  const error = status === "failed" && typeof b.error === "string" ? b.error.slice(0, 500) : null;
  const row = await env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs WHERE id = ?1`).bind(id).first<JobView>();
  if (!row) throw new ApiError(404, "not_found", "job not found");
  if (!(NEXT[row.status] ?? []).includes(status)) throw new ApiError(409, "bad_transition", `${row.status} -> ${status} not allowed`);
  const ts = isoNow(now);
  await env.DB.prepare("UPDATE jobs SET status = ?1, error = ?2, updated_at = ?3, finished_at = ?4 WHERE id = ?5")
    .bind(status, error, ts, status === "running" ? null : ts, id)
    .run();
  const out = await env.DB.prepare(`SELECT ${JOB_COLS} FROM jobs WHERE id = ?1`).bind(id).first<JobView>();
  return view(out as JobView, true);
}

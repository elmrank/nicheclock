/**
 * POST /b: cookieless page-view counts for the nicheclock site, reduced to what a docs site needs.
 *
 * One beacon = one page view leaving (tab hidden / page closed): path, visible seconds, max scroll
 * bucket, device, kind of referrer, whether a link to another page was clicked. A reader who comes back
 * to the tab and leaves again re-sends with `previous`; the view then moves to its new buckets instead
 * of counting twice. Stored: daily totals per UTC day x host x path x device x source (table
 * `pageviews_daily`, migration 0002). Never read or stored: IP, cookies, the full user agent (read only to
 * drop bots), the referrer URL, any identifier.
 *
 * Same origin only: the page and /b live on the same Worker, so there is no CORS. A foreign Origin gets 403.
 */
import type { Env } from "./env";

export const MAX_BODY_CHARS = 512;
export const MAX_SECONDS = 30 * 60;
export const MAX_PATHS_PER_DAY = 300;
/** Whole-isolate cap (no IP is read, so there is no per-client cap). */
export const MAX_PER_MINUTE = 300;

export const SCROLL_BUCKETS = [0, 25, 50, 75, 90, 100] as const;
export const DEVICES = ["mobile", "desktop"] as const;
export const SOURCES = ["search", "social", "ai", "direct", "internal", "other"] as const;

const PATH_RE = /^\/[A-Za-z0-9/_\-.~%]{0,199}$/;
const KEYS = new Set(["path", "seconds", "scroll", "device", "source", "clicks", "previous"]);
const PREV_KEYS = new Set(["seconds", "scroll", "clicks"]);
const BOT_UA_RE =
  /bot\/|\bbot\b|crawl|spider|slurp|archiver|facebookexternalhit|embedly|bingpreview|headless|lighthouse|pagespeed|pingdom|uptime|monitor|curl|wget|python-|httpclient|go-http-client|java\/|okhttp|axios|node-fetch|undici|scrapy|phantomjs|selenium|puppeteer|playwright|cypress|google-inspectiontool|googleother|mediapartners/i;

export interface Snapshot {
  seconds: number;
  scroll: number;
  clicks: number;
}
export interface Beacon extends Snapshot {
  path: string;
  device: (typeof DEVICES)[number];
  source: (typeof SOURCES)[number];
  previous?: Snapshot;
}

export function isBotUserAgent(ua: string | null): boolean {
  const s = (ua ?? "").trim();
  return s === "" || BOT_UA_RE.test(s);
}

/** Origin -> host tag. Local dev origins are stored apart as "local" so they never mix with production. */
export function hostForOrigin(origin: string | null, env: Pick<Env, "PUBLIC_BASE_URL">): string | null {
  if (!origin) return null;
  let prod: URL;
  try {
    prod = new URL(env.PUBLIC_BASE_URL);
  } catch {
    return null;
  }
  if (origin === prod.origin) return prod.hostname;
  let u: URL;
  try {
    u = new URL(origin);
  } catch {
    return null;
  }
  return (u.hostname === "localhost" || u.hostname === "127.0.0.1") && u.protocol === "http:" ? "local" : null;
}

function boundedInt(v: unknown, max: number): number | null {
  if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
  return Math.min(max, Math.floor(v));
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function parseSnapshot(raw: unknown, allowed: Set<string>): Snapshot | null {
  if (!isPlainObject(raw) || Object.keys(raw).some((k) => !allowed.has(k))) return null;
  const seconds = boundedInt(raw.seconds, MAX_SECONDS);
  const clicks = boundedInt(raw.clicks ?? 0, 1);
  const scroll = raw.scroll;
  if (seconds === null || clicks === null || typeof scroll !== "number" || !(SCROLL_BUCKETS as readonly number[]).includes(scroll)) return null;
  return { seconds, scroll, clicks };
}

/** Body text -> a fresh beacon with only the known fields (clamped), or null when anything is off. */
export function parseBeacon(body: string): Beacon | null {
  if (!body || body.length > MAX_BODY_CHARS) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(body);
  } catch {
    return null;
  }
  const snap = parseSnapshot(raw, KEYS);
  if (!snap || !isPlainObject(raw)) return null;
  const path = typeof raw.path === "string" && PATH_RE.test(raw.path) ? raw.path : null;
  const device = (DEVICES as readonly unknown[]).includes(raw.device) ? (raw.device as Beacon["device"]) : null;
  const source = (SOURCES as readonly unknown[]).includes(raw.source) ? (raw.source as Beacon["source"]) : null;
  if (!path || !device || !source) return null;
  const beacon: Beacon = { path, device, source, ...snap };
  if (raw.previous !== undefined) {
    const prev = parseSnapshot(raw.previous, PREV_KEYS);
    if (!prev) return null;
    beacon.previous = prev;
  }
  return beacon;
}

/** True when `previous` can be an earlier state of the same view (nothing goes down). */
export function previousIsEarlier(b: Snapshot, p: Snapshot): boolean {
  return p.seconds <= b.seconds && p.scroll <= b.scroll && p.clicks <= b.clicks;
}

const day = (nowMs: number) => new Date(nowMs).toISOString().slice(0, 10);
const scrollCol = (bucket: number) => `scroll_${bucket}`;

type Key = [string, string, string, string, string];

async function pathAcceptsNewRow(db: D1Database, d: string, host: string, path: string): Promise<boolean> {
  const known = await db.prepare("SELECT 1 FROM pageviews_daily WHERE day = ?1 AND host = ?2 AND path = ?3 LIMIT 1").bind(d, host, path).first();
  if (known) return true;
  const row = await db.prepare("SELECT COUNT(DISTINCT path) AS n FROM pageviews_daily WHERE day = ?1 AND host = ?2").bind(d, host).first<{ n: number }>();
  return (row?.n ?? 0) < MAX_PATHS_PER_DAY;
}

async function applyPrevious(db: D1Database, key: Key, b: Snapshot, p: Snapshot): Promise<boolean> {
  const delta: Record<string, number> = {};
  const add = (col: string, n: number) => {
    delta[col] = (delta[col] ?? 0) + n;
  };
  add("seconds_total", b.seconds - p.seconds);
  add(scrollCol(p.scroll), -1);
  add(scrollCol(b.scroll), 1);
  add("views_with_internal_click", b.clicks - p.clicks);
  const entries = Object.entries(delta).filter(([, n]) => n !== 0);
  const where = "day = ? AND host = ? AND path = ? AND device = ? AND source = ?";
  if (entries.length === 0) return !!(await db.prepare(`SELECT 1 FROM pageviews_daily WHERE ${where} LIMIT 1`).bind(...key).first());
  // MAX(0, ...): a forged `previous` must not drive a counter below zero.
  const set = entries.map(([c, n]) => (n < 0 ? `${c} = MAX(0, ${c} + ?)` : `${c} = ${c} + ?`)).join(", ");
  const res = await db.prepare(`UPDATE pageviews_daily SET ${set} WHERE ${where}`).bind(...entries.map(([, n]) => n), ...key).run();
  return (res.meta?.changes ?? 0) > 0;
}

/** Add one parsed beacon to the day's totals. Returns "new", "updated" or "dropped". */
export async function recordBeacon(db: D1Database, b: Beacon, host: string, nowMs: number): Promise<"new" | "updated" | "dropped"> {
  const d = day(nowMs);
  const key: Key = [d, host, b.path, b.device, b.source];
  if (b.previous) {
    if (!previousIsEarlier(b, b.previous)) return "dropped";
    // No row today: the first exit was counted on an earlier UTC day, so count a view of today.
    if (await applyPrevious(db, key, b, b.previous)) return "updated";
  }
  if (!(await pathAcceptsNewRow(db, d, host, b.path))) return "dropped";
  const scrollColumns = SCROLL_BUCKETS.map(scrollCol);
  const cols = ["day", "host", "path", "device", "source", "views", "seconds_total", "views_with_internal_click", ...scrollColumns];
  const vals = [...key, 1, b.seconds, b.clicks > 0 ? 1 : 0, ...SCROLL_BUCKETS.map((s) => (s === b.scroll ? 1 : 0))];
  const counters = cols.slice(5);
  const sql =
    `INSERT INTO pageviews_daily (${cols.join(", ")}) VALUES (${cols.map(() => "?").join(", ")}) ` +
    `ON CONFLICT (day, host, path, device, source) DO UPDATE SET ${counters.map((c) => `${c} = ${c} + excluded.${c}`).join(", ")}`;
  await db.prepare(sql).bind(...vals).run();
  return "new";
}

let windowStart = 0;
let windowCount = 0;

export function overIsolateCap(nowMs: number): boolean {
  if (nowMs - windowStart >= 60_000) {
    windowStart = nowMs;
    windowCount = 0;
  }
  windowCount += 1;
  return windowCount > MAX_PER_MINUTE;
}

/** Test hook: forget the in-isolate counter. */
export function resetBeaconCap(): void {
  windowStart = 0;
  windowCount = 0;
}

const quiet = (status: number, headers: Record<string, string> = {}) =>
  new Response(null, { status, headers: { "cache-control": "no-store", ...headers } });

/** Handler for `/b`. Never throws: a measurement problem must not surface to the reader. */
export async function handle(req: Request, env: Env, nowMs: number = Date.now()): Promise<Response> {
  if (req.method !== "POST") return quiet(405, { allow: "POST" });
  // Browsers send Origin on same-origin POSTs; Sec-Fetch-Site is the fallback and cannot be set by page scripts.
  const origin = req.headers.get("origin") ?? (req.headers.get("sec-fetch-site") === "same-origin" ? new URL(req.url).origin : null);
  const host = hostForOrigin(origin, env);
  if (!host) return quiet(403);
  if (isBotUserAgent(req.headers.get("user-agent"))) return quiet(204);
  const body = await req.text().catch(() => "");
  const beacon = parseBeacon(body);
  if (!beacon) return quiet(400);
  if (overIsolateCap(nowMs)) return quiet(204);
  try {
    await recordBeacon(env.DB, beacon, host, nowMs);
  } catch (err) {
    console.error("beacon write failed", String(err));
  }
  return quiet(204);
}

/**
 * trending_now and upcoming_events: the MCP tools over the public catalog the site publishes (the Trending now and Upcoming tabs).
 *
 * They read the small digests that site/trending/build.py cuts from the PUBLISHED catalog files (nicheclock/catalog/digest.py, under
 * /catalog-data/tools/), through the assets binding: one region of the trending list is 3 to 80 KB, so a call parses about 1 ms worth of JSON,
 * not the 100 to 650 KB of the full file. A digest row has the one-letter keys documented in digest.py; this file turns them into the answer.
 * Nothing is stored and no profile or caller data is read.
 */
import type { Env } from "./env";
import { ApiError } from "./http";
import { getJson } from "./shards";

const TOOLS_DIR = "/catalog-data/tools/";
const WINDOWS = ["24h", "48h", "7d"] as const;
type Window = (typeof WINDOWS)[number];
const DAY_MS = 86_400_000;
export const RISE_NOTE =
  "rise is a multiple of the topic's own median over the 14 days before the window (3.2 means +220%). Every volume is in its own unit; none is search volume.";

interface Index {
  regions: { id: string; label: string; topics: number; date: string | null; limited: boolean; limited_note: string | null }[];
  categories: Record<string, string>;
  kinds: string[];
  units: Record<string, string>;
  licenses: Record<string, { source: string | null; label: string; license: string; url: string | null }>;
  events_today: string | null;
  lag_note: string | null;
  event_months: Record<string, number>;
}
interface TrendRow {
  q: string | null;
  l: string;
  c: string;
  s: "rising" | "fading" | "ended" | "steady";
  r: (number | null)[];
  v: number | null;
  u: string | null;
  x: string[];
  w: string;
  a: string | null;
  y: string[];
  z?: boolean[];
}
interface TrendFile {
  region: string;
  date: string | null;
  generated_at: string | null;
  rows: TrendRow[];
}
interface EventRow {
  t: string;
  d: string;
  p: string;
  g: number | null;
  k: string;
  c: string | null;
  e: string[] | null;
  o: string | null;
  n: string;
  a: string;
  i: string;
}
interface EventFile {
  events: EventRow[];
}

function bad(message: string): ApiError {
  return new ApiError(400, "invalid_params", message);
}

function limitArg(v: unknown): number {
  if (v === undefined) return 20;
  if (typeof v !== "number" || !Number.isInteger(v) || v < 1 || v > 50) throw bad("limit must be an integer between 1 and 50");
  return v;
}

async function indexOf(env: Env): Promise<Index> {
  const idx = await getJson<Index>(env, `${TOOLS_DIR}index.json`);
  if (!idx) throw new ApiError(503, "catalog_unavailable", "the public catalog is not published on this deployment");
  return idx;
}

function regionArg(v: unknown, idx: Index): Index["regions"][number] {
  const raw = v === undefined ? "global" : v;
  if (typeof raw !== "string") throw bad("region must be 'global' or a country code");
  const want = raw.trim().toLowerCase() === "worldwide" ? "global" : raw.trim().toLowerCase();
  const hit = idx.regions.find((r) => r.id.toLowerCase() === want);
  if (!hit) throw bad(`unknown region '${raw}'. Valid regions: ${idx.regions.map((r) => r.id).join(", ")}`);
  return hit;
}

export async function trendingNow(env: Env, args: Record<string, unknown>): Promise<unknown> {
  const idx = await indexOf(env);
  const region = regionArg(args.region, idx);
  const win = args.window === undefined ? "7d" : args.window;
  if (typeof win !== "string" || !(WINDOWS as readonly string[]).includes(win)) throw bad("window must be 24h, 48h or 7d");
  const wi = WINDOWS.indexOf(win as Window);
  const state = args.state === undefined ? "rising" : args.state;
  if (state !== "rising" && state !== "all") throw bad("state must be 'rising' or 'all'");
  let category: string | null = null;
  if (args.category !== undefined && args.category !== null) {
    if (typeof args.category !== "string") throw bad("category must be a category id such as 'sports'");
    const c = args.category.trim().toLowerCase();
    category = Object.keys(idx.categories).find((id) => id === c || (idx.categories[id] as string).toLowerCase() === c) ?? null;
    if (!category) throw bad(`unknown category '${args.category}'. Valid categories: ${Object.keys(idx.categories).join(", ")}`);
  }
  const limit = limitArg(args.limit);

  const file = region.topics > 0 ? await getJson<TrendFile>(env, `${TOOLS_DIR}trending-${region.id}.json`) : null;
  const rows = (file?.rows ?? []).filter(
    (r) => (category === null || r.c === category) && (state === "all" || (r.s === "rising" && r.z?.[wi] === true)),
  );
  const items = rows.slice(0, limit).map((r, i) => ({
    rank: i + 1,
    label: r.l,
    qid: r.q,
    rise: r.r[wi] ?? null,
    volume: r.v,
    unit: r.u,
    state: r.s,
    sources: r.x,
    category: r.c,
    category_label: idx.categories[r.c] ?? r.c,
    why: r.w,
    link: r.a,
  }));
  const used = new Set(items.flatMap((i) => i.sources));
  return {
    region: region.id,
    region_label: region.label,
    window: win,
    state,
    category,
    data_date: file?.date ?? region.date,
    generated_at: file?.generated_at ?? null,
    limited_note: region.limited ? region.limited_note : null,
    note: RISE_NOTE,
    matching: rows.length,
    items,
    units: Object.fromEntries(Object.entries(idx.units).filter(([short]) => items.some((i) => i.unit === short))),
    licenses: Object.fromEntries(Object.entries(idx.licenses).filter(([key]) => used.has(key))),
  };
}

function utcDay(iso: string): number {
  return Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
}

export async function upcomingEvents(env: Env, args: Record<string, unknown>, now: Date): Promise<unknown> {
  const idx = await indexOf(env);
  const region = regionArg(args.region, idx);
  const days = args.days === undefined ? 30 : args.days;
  if (typeof days !== "number" || !Number.isInteger(days) || days < 1 || days > 365) throw bad("days must be an integer between 1 and 365");
  const limit = limitArg(args.limit);
  let kind: string | null = null;
  if (args.kind !== undefined && args.kind !== null) {
    if (typeof args.kind !== "string") throw bad("kind must be a kind of event such as 'film release'");
    const k = args.kind.trim().toLowerCase();
    kind = idx.kinds.find((x) => x.toLowerCase() === k) ?? null;
    if (!kind) throw bad(`unknown kind '${args.kind}'. Valid kinds: ${idx.kinds.join(", ")}`);
  }
  const today = utcDay(now.toISOString());
  const until = today + days * DAY_MS;
  // one file per month of event dates: a 30-day window reads one or two of them, not the whole calendar
  const months = Object.keys(idx.event_months)
    .filter((m) => Date.parse(`${m}-01T00:00:00Z`) <= until && Date.parse(`${m}-01T00:00:00Z`) + 32 * DAY_MS > today)
    .sort();
  const files = await Promise.all(months.map((m) => getJson<EventFile>(env, `${TOOLS_DIR}events-${m}.json`)));
  const all = files.flatMap((f) => f?.events ?? []);
  // an event with a regions list (holidays, Federal Register) belongs only to those regions; one without (Wikidata) to every region
  const rows = all.filter((e) => {
    const t = utcDay(e.d);
    return t >= today && t <= until && (kind === null || e.k === kind) && (region.id === "global" || !e.e || e.e.includes(region.id));
  });
  const items = rows.slice(0, limit).map((e) => ({
    date: e.d,
    title: e.t,
    kind: e.k,
    category: e.c,
    category_label: e.c ? (idx.categories[e.c] ?? e.c) : null,
    regions: e.e ?? (e.o ? [e.o] : []),
    publish_by: e.p,
    lag_days: e.g,
    days_until: Math.round((utcDay(e.d) - today) / DAY_MS),
    days_to_publish_by: Math.round((utcDay(e.p) - today) / DAY_MS),
    publish_by_passed: utcDay(e.p) < today,
    source: e.n,
    source_url: e.a,
    license: e.i,
  }));
  return {
    region: region.id,
    days,
    kind,
    data_date: idx.events_today,
    note: idx.lag_note ?? "Publish by is the event date minus a default lead time per kind of event, not a measurement.",
    matching: rows.length,
    items,
  };
}

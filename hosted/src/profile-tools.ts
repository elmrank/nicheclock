/**
 * trends_for_profile, upcoming_for_profile and resolve_topic: the MCP tools and the REST routes under POST /v1/profile/... (design 9).
 *
 * The agent carries the profile in every call; the Worker keeps it in memory for the call and drops it. The matcher is the page's own
 * `site/trending/match.js`, so a browser and an agent get the same list from the same rules. What differs is how the candidate rows are
 * found: the page matches against a whole pool it has downloaded; here the pool is read as shards (shards.ts) and only the units a topic
 * can be tied through are parsed: the rows tied to the topic's qid (`q<qid>`) and, for the "named" tie, the rows with a class or occupation
 * whose name has the topic's longest word in it (`w<word>`). A row whose only tie to the topic is its title is not among them (a title-only
 * tie scores at most 0.40, so it never reaches the 0.50 line; it can only be missing from the 0.35 to 0.49 band).
 */
import { match, SORTS, FLAG, yourSlot } from "../site/trending/match.js";
import * as P from "../site/trending/profile.js";
import type { Env } from "./env";
import { ApiError } from "./http";
import { Shards, type PoolRow } from "./shards";
import { cleanText, resolveLocal, resolveText } from "./suggest-resolve";

export const POOL_LICENSE = "CC0 (Wikimedia Analytics datasets)";
export const UNIT_NOTE = "Every volume has its own unit; none is search volume.";
const DAY_MS = 86_400_000;
const STOP_WORDS = new Set("of the and in on at to for a an de la le les el di der die das".split(" "));

interface Entity {
  qid: number;
  title: string;
  views: number;
  ratio: number;
  handles: number[];
  series: 0;
  flags: number;
  event?: EventDoc;
}
export interface EventDoc {
  title: string;
  date: string;
  publish_by: string;
  lag_days: number;
  kind: string;
  category?: string;
  category_label?: string;
  region?: string;
  source: string;
  source_url: string;
  license: string;
  qid: string;
  recurrence?: string;
  rule?: string;
  popularity?: number;
  [k: string]: unknown;
}
interface Profile {
  topics: { qid: string; label: string; role: string; source: string }[];
  avoid: { qid: string; label: string }[];
  region: string;
  cadence?: { per_week?: number; days?: string[] };
  [k: string]: unknown;
}
interface Rel {
  kind: string;
  score: number;
  via: number;
  sub: number;
}
interface Row {
  entity: Entity;
  fit: number;
  rank: number;
  relations: Rel[];
  why: string;
}

const isObj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

/** the word of a topic's name that the "named" units are keyed by: the longest letters-and-digits word that is not a stop word (shard.py words()) */
export function indexWord(label: string): string | null {
  const ws = (label.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).filter((w) => w.length >= 2 && !STOP_WORDS.has(w));
  ws.sort((a, b) => b.length - a.length || (a < b ? -1 : 1));
  return ws[0] ?? null;
}

/** Take the caller's profile, turn `topics[].text` into ids (concept list only, no Wikidata), use the list's own label for each id, validate. */
export async function prepareProfile(env: Env, input: unknown): Promise<{ profile: Profile; unresolved: string[] }> {
  if (!isObj(input)) throw new ApiError(400, "invalid_profile", "profile must be an object (schema nicheclock.profile/1)");
  const unresolved: string[] = [];
  const concepts = new Shards(env, "concepts");
  const topics: Record<string, unknown>[] = [];
  for (const t of Array.isArray(input.topics) ? input.topics.slice(0, 16) : []) {
    if (!isObj(t)) continue;
    if (typeof t.qid !== "string" && typeof t.text === "string") {
      const word = cleanText(t.text);
      const r = await resolveLocal(env, word);
      if (!r) {
        unresolved.push(word);
        continue;
      }
      topics.push({ ...t, qid: r.qid, label: r.label, source: typeof t.source === "string" ? t.source : "typed" });
    } else {
      topics.push({ ...t });
    }
  }
  // A label the caller gave is used as it is (the page does the same: it stores the list's label). Only an id with no label is looked up, so a
  // call that carries labels parses no concept shard at all.
  const unlabeled = topics.filter((t) => typeof t.qid === "string" && /^Q[1-9]\d*$/.test(t.qid as string) && !(typeof t.label === "string" && t.label.trim()));
  const named = await concepts.units(unlabeled.map((t) => `q${(t.qid as string).slice(1)}`));
  for (const t of unlabeled) {
    const c = named.get(`q${(t.qid as string).slice(1)}`)?.c as [string, string] | undefined;
    if (c) t.label = c[0];
  }
  const v = P.validate({ schema: P.SCHEMA, ...input, topics }, { keepUpdated: true });
  if (!v.ok) {
    throw new ApiError(400, "invalid_profile", `${v.errors.join(" ")}${unresolved.length ? ` Not in the concept list (pass a qid, or call resolve_topic): ${unresolved.join(", ")}.` : ""}`);
  }
  return { profile: v.profile as unknown as Profile, unresolved };      // profile.js is plain JS: its inferred type leaves most fields out
}

/** the keys of the units that can hold a tie to any topic of the profile */
export function unitKeys(profile: Profile): string[] {
  const keys: string[] = [];
  for (const t of profile.topics) {
    keys.push(`q${t.qid.slice(1)}`);
    const w = indexWord(t.label);
    if (w) keys.push(`w${w}`);
  }
  return keys;
}

function candidates(units: Map<string, { r?: PoolRow[]; l?: Record<string, string> }>, toEntity: (r: PoolRow) => Entity): { entities: Entity[]; labels: Map<number, string> } {
  const byQid = new Map<number, Entity>();
  const labels = new Map<number, string>();
  for (const u of units.values()) {
    for (const r of u.r ?? []) if (!byQid.has(r[0])) byQid.set(r[0], toEntity(r));
    for (const [q, l] of Object.entries(u.l ?? {})) labels.set(Number(q), l);
  }
  return { entities: [...byQid.values()], labels };
}

function relationsOut(row: Row, profile: Profile, labelOf: (q: number) => string | undefined) {
  return row.relations.map((r) => {
    const anchor = profile.topics.find((t) => Number(t.qid.slice(1)) === r.via);
    return { kind: r.kind, via: `Q${r.via}`, via_label: labelOf(r.via) ?? anchor?.label ?? (r.via === row.entity.qid ? row.entity.title : undefined), score: Math.round(r.score * 100) / 100 };
  });
}

const intArg = (v: unknown, def: number, min: number, max: number, name: string): number => {
  if (v === undefined) return def;
  if (typeof v !== "number" || !Number.isInteger(v) || v < min || v > max) throw new ApiError(400, "invalid_params", `${name} must be an integer between ${min} and ${max}`);
  return v;
};

export async function trendsForProfile(env: Env, args: Record<string, unknown>) {
  const limit = intArg(args.limit, 20, 1, 50, "limit");
  const minFit = args.min_fit === undefined ? 0.5 : args.min_fit;
  if (typeof minFit !== "number" || !(minFit >= 0.35 && minFit <= 1)) throw new ApiError(400, "invalid_params", "min_fit must be a number between 0.35 and 1");
  const { profile, unresolved } = await prepareProfile(env, args.profile);
  const pool = new Shards(env, `pool-${profile.region}`);
  const man = await pool.manifest();
  if (!man) throw new ApiError(400, "no_pool", `There is no niche pool for the region ${profile.region}. Pools exist for the countries listed in /catalog-data/meta.json (match.regions).`);
  const units = await pool.units(unitKeys(profile));
  const { entities, labels } = candidates(units, (r) => ({ qid: r[0], title: r[1], views: r[2], ratio: r[3], handles: r[4], series: 0, flags: r[5] }));
  const labelOf = (q: number) => labels.get(q);
  const res = match(profile, entities, labelOf) as { fits: Row[]; weaker: Row[]; duplicates: number };
  const all: Row[] = res.fits.concat(res.weaker).filter((r) => r.fit >= minFit).sort(SORTS.fit);
  return {
    region: profile.region,
    data_date: man.date ?? null,
    matched: all.length,
    returned: Math.min(all.length, limit),
    unresolved,
    note: UNIT_NOTE,
    rows: all.slice(0, limit).map((r) => {
      const e = r.entity;
      const rising = !!(e.flags & FLAG.RISING);
      return {
        qid: `Q${e.qid}`,
        label: e.title,
        fit: Math.round(r.fit * 100) / 100,
        why: r.why,
        relations: relationsOut(r, profile, labelOf),
        trend: rising ? "rising" : "steady",
        ...(rising ? { rise_ratio: e.ratio, rise_basis: "last day against the median of the 14 days before" } : {}),
        volume: { value: e.views, unit: "pageviews/day" },
        region: profile.region,
        source: "wikimedia_country",
        source_url: `https://en.wikipedia.org/wiki/${encodeURIComponent(e.title.replace(/ /g, "_")).replace(/%2F/g, "/")}`,
        license: POOL_LICENSE,
      };
    }),
  };
}

const ymd = (ms: number) => new Date(ms).toISOString().slice(0, 10);
const dayMs = (s: string) => Date.parse(`${s}T00:00:00Z`);

export async function upcomingForProfile(env: Env, args: Record<string, unknown>, now: Date) {
  const days = intArg(args.days, 30, 1, 365, "days");
  const limit = intArg(args.limit, 20, 1, 50, "limit");
  const { profile, unresolved } = await prepareProfile(env, args.profile);
  const shards = new Shards(env, "events");
  const man = await shards.manifest();
  if (!man) throw new ApiError(503, "no_events", "The events index is not part of this build.");
  const meta = (man.meta ?? {}) as { lag_days?: number; source?: string; license?: string; recurrence?: string; categories?: Record<string, string> };
  const units = await shards.units(unitKeys(profile));
  const toEntity = (r: PoolRow): Entity => {
    const x = (r[6] ?? []) as unknown[];
    let event: EventDoc;
    if (x[0] === "e") event = x[1] as EventDoc;
    else {
      const date = String(x[1]);
      const lag = meta.lag_days ?? 28;
      const cat = String(x[4] ?? "other");
      event = { title: r[1], date, publish_by: ymd(dayMs(date) - lag * DAY_MS), lag_days: lag, kind: "observance", category: cat, category_label: meta.categories?.[cat] ?? cat, region: "global", source: meta.source ?? "wikidata", source_url: `https://www.wikidata.org/wiki/Q${r[0]}`, license: meta.license ?? "CC0 (Wikidata)", qid: `Q${r[0]}`, popularity: Number(x[3] ?? 0), recurrence: meta.recurrence, rule: String(x[2]) };
    }
    return { qid: r[0], title: r[1], views: 0, ratio: 0, handles: r[4], series: 0, flags: r[5], event };
  };
  const { entities, labels } = candidates(units, toEntity);
  const labelOf = (q: number) => labels.get(q);
  const today = dayMs(ymd(now.getTime()));
  const res = match(profile, entities, labelOf) as { fits: Row[] };
  const slotDays = profile.cadence?.days ?? [];
  const inWindow = res.fits
    .filter((r) => {
      const t = dayMs((r.entity.event as EventDoc).date);
      return t >= today && t <= today + days * DAY_MS;
    })
    .sort((a, b) => dayMs((a.entity.event as EventDoc).date) - dayMs((b.entity.event as EventDoc).date) || a.entity.title.localeCompare(b.entity.title));
  return {
    data_note: "Wikidata events with a date, and yearly observances (the next date of the rule on Wikidata). Public holidays and Federal Register notices carry no Wikidata id, so they are not matched.",
    unresolved,
    matched: inWindow.length,
    returned: Math.min(inWindow.length, limit),
    events: inWindow.slice(0, limit).map((r) => {
      const ev = r.entity.event as EventDoc;
      const slot = slotDays.length ? (yourSlot(slotDays, dayMs(ev.publish_by), today) as { ms: number; late?: boolean; none?: boolean } | null) : null;
      return {
        qid: ev.qid,
        title: ev.title,
        kind: ev.kind,
        date: ev.date,
        days_until: Math.round((dayMs(ev.date) - today) / DAY_MS),
        publish_by: ev.publish_by,
        publish_window_open: dayMs(ev.publish_by) >= today,
        ...(slot ? { your_slot: { date: ymd(slot.ms), ...(slot.late ? { late: true } : {}), ...(slot.none ? { no_usual_day_fits: true } : {}) } } : {}),
        fit: Math.round(r.fit * 100) / 100,
        why: r.why,
        relations: relationsOut(r, profile, labelOf),
        ...(ev.recurrence ? { recurrence: ev.recurrence, rule: ev.rule } : {}),
        source: ev.source,
        source_url: ev.source_url,
        license: ev.license,
      };
    }),
  };
}

export async function resolveTopic(env: Env, args: Record<string, unknown>) {
  const text = cleanText(args.text);
  const context = typeof args.context === "string" ? args.context.slice(0, 200) : undefined;
  const topic = await resolveText(env, text, context);
  return topic ?? { qid: null, message: "Nothing on Wikidata matches that text. Try a broader or more common word." };
}

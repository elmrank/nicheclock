/**
 * Reads the precomputed JSON from R2 (layout + schema: hosted/SCHEMA.md) and re-validates it at
 * serving time. The allow-list lives here, not only in the exporter: an item whose source is not
 * allowed for the caller's scope, or that lacks source_url / license|attribution, is dropped.
 * Output objects are built field by field, so unknown fields in R2 never reach a client.
 */
import type { Env, KeyRow, Scope } from "./env";
import { ApiError } from "./http";
import { allowedSources, INTERNAL_SOURCES, NEVER_SERVED_SOURCES } from "./sources";

type Json = Record<string, unknown>;

export const NICHE_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
export const ITEM_ID_RE = /^([a-z0-9][a-z0-9-]{0,63})~([a-z0-9]{6,40})$/;
export const CUSTOM_PREFIX = "custom-";

export interface Provenance {
  source: string;
  source_url: string;
  license?: string;
  attribution?: string;
}

export interface NicheInfo {
  id: string;
  name: string;
  description?: string;
  sources: string[];
  updated_at: string | null;
  counts?: { trends: number; upcoming: number };
  custom: boolean;
  url?: string;
}

/** Region tags (docs/regions.md): an ISO country code, "global" or "multi" (several countries, see `regions`). */
export interface Regional {
  region: string;
  region_precision: "exact" | "approx" | "global";
}

export interface TrendItem extends Provenance, Regional {
  id: string;
  regions: string[];
  title: string;
  score: number;
  why: string;
  url: string | null;
  at: string;
  sources: string[];
  evidence_count: number;
}

export interface UpcomingItem extends Provenance, Regional {
  id: string;
  kind: string;
  title: string;
  date: string;
  date_precision: "day" | "month";
  publish_by: string | null;
  url: string | null;
  days_until: number;
  publish_by_passed: boolean;
}

export interface EvidenceEntry extends Provenance, Regional {
  term: string;
  seen_at: string | null;
  score: number | null;
  rank: number | null;
}

export interface EvidenceRecord extends Provenance {
  id: string;
  niche: string;
  title: string;
  evidence: EvidenceEntry[];
}

// ---- primitives ------------------------------------------------------------------------------

function isObj(x: unknown): x is Json {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function str(x: unknown, max = 500): string | null {
  if (typeof x !== "string") return null;
  const t = x.trim();
  return t && t.length <= max ? t : null;
}

function httpUrl(x: unknown): string | null {
  const s = str(x, 2048);
  if (!s) return null;
  try {
    const u = new URL(s);
    return u.protocol === "https:" || u.protocol === "http:" ? s : null;
  } catch {
    return null;
  }
}

function num(x: unknown): number | null {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

function isoDate(x: unknown): string | null {
  const s = str(x, 40);
  return s && !Number.isNaN(Date.parse(s)) ? s : null;
}

function ymd(x: unknown): string | null {
  const s = str(x, 10);
  return s && /^\d{4}-\d{2}-\d{2}$/.test(s) && !Number.isNaN(Date.parse(s)) ? s : null;
}

const REGION_RE = /^[A-Z]{2}$/;

export function regionTag(raw: Json): Regional {
  const r = typeof raw.region === "string" ? raw.region.trim() : "";
  const up = r.toUpperCase();
  const region = REGION_RE.test(up) ? up : up === "MULTI" ? "multi" : "global";
  const p = raw.region_precision;
  return { region, region_precision: p === "exact" || p === "approx" ? p : "global" };
}

export function regionList(raw: Json): string[] {
  if (!Array.isArray(raw.regions)) return [];
  const codes = raw.regions.filter((x): x is string => typeof x === "string").map((x) => x.trim().toUpperCase()).filter((x) => REGION_RE.test(x));
  return [...new Set(codes)].sort();
}

/** ?region=XX / the MCP `region` argument: null = no filter ("global" or absent). Throws 400 on anything else. */
export function parseRegion(v: string | null | undefined): string | null {
  if (v === null || v === undefined || v === "") return null;
  const t = v.trim();
  if (t.toLowerCase() === "global") return null;
  if (!/^[A-Za-z]{2}$/.test(t)) throw new ApiError(400, "invalid_params", "region must be a two-letter country code (e.g. US) or global");
  return t.toUpperCase();
}

/** Keep the item's own country and global items; a "multi" item only when the country is among its `regions`. */
export function regionKeeps(item: Regional, regions: readonly string[], wanted: string | null): boolean {
  if (wanted === null) return true;
  if (item.region === "multi") return regions.includes(wanted);
  return item.region === wanted || item.region === "global";
}

/**
 * Items the exporter tagged `scope: "internal"` (they used an internal-only source, so their score and
 * text may derive from it) are invisible to external keys even if their own `source` is public.
 */
export function scopeVisible(raw: Json, allowed: ReadonlySet<string>): boolean {
  const internalCaller = INTERNAL_SOURCES.some((s) => allowed.has(s));
  return internalCaller || raw.scope === undefined || raw.scope === "public";
}

/** source in the caller's allow-list + source_url + (license or attribution), else null. */
export function provenance(raw: Json, allowed: ReadonlySet<string>): Provenance | null {
  if (!scopeVisible(raw, allowed)) return null;
  const source = str(raw.source, 64);
  if (!source || !allowed.has(source)) return null;
  const source_url = httpUrl(raw.source_url);
  if (!source_url) return null;
  const license = str(raw.license, 300);
  const attribution = str(raw.attribution, 300);
  if (!license && !attribution) return null;
  const p: Provenance = { source, source_url };
  if (license) p.license = license;
  if (attribution) p.attribution = attribution;
  return p;
}

/**
 * Free text must not leak data from sources we do not serve (the exporter rescored from allowed
 * signals only; this is the second line). Any mention of a known non-served source id blanks it.
 */
export function safeWhy(why: unknown, allowed: ReadonlySet<string>): string {
  const s = str(why, 600);
  if (!s) return "";
  const low = s.toLowerCase();
  const blocked = [...NEVER_SERVED_SOURCES, ...INTERNAL_SOURCES.filter((x) => !allowed.has(x))];
  // whole-word match ("google trends" == google_trends); "topics" or "physics" never trip it
  return blocked.some((b) => new RegExp(`(?<![a-z0-9])${b.replace(/_/g, "[\\s_-]+")}(?![a-z0-9])`).test(low)) ? "" : s;
}

// ---- cleaners --------------------------------------------------------------------------------

export function cleanTrend(raw: unknown, allowed: ReadonlySet<string>, fallbackAt: string | null): TrendItem | null {
  if (!isObj(raw)) return null;
  const prov = provenance(raw, allowed);
  const id = str(raw.id, 120);
  const title = str(raw.title, 300);
  const at = isoDate(raw.at) ?? fallbackAt;
  if (!prov || !id || !ITEM_ID_RE.test(id) || !title || !at) return null;
  const sources = new Set<string>([prov.source]);
  if (Array.isArray(raw.sources)) for (const s of raw.sources) if (typeof s === "string" && allowed.has(s)) sources.add(s);
  return {
    id,
    title,
    score: num(raw.score) ?? 0,
    why: safeWhy(raw.why, allowed),
    url: httpUrl(raw.url),
    at,
    ...prov,
    ...regionTag(raw),
    regions: regionList(raw),
    sources: [...sources].sort(),
    evidence_count: Math.max(0, Math.trunc(num(raw.evidence_count) ?? 0)),
  };
}

export function cleanUpcoming(raw: unknown, allowed: ReadonlySet<string>, today: string): UpcomingItem | null {
  if (!isObj(raw)) return null;
  const prov = provenance(raw, allowed);
  const id = str(raw.id, 120);
  const title = str(raw.title, 300);
  const date = ymd(raw.date);
  if (!prov || !id || !title || !date) return null;
  const publish_by = ymd(raw.publish_by);
  const days = Math.round((Date.parse(date) - Date.parse(today)) / 86_400_000);
  return {
    id,
    kind: str(raw.kind, 64) ?? "event",
    title,
    date,
    date_precision: raw.date_precision === "month" ? "month" : "day",
    publish_by,
    url: httpUrl(raw.url),
    ...prov,
    ...regionTag(raw),
    days_until: days,
    publish_by_passed: publish_by !== null && publish_by < today,
  };
}

function cleanEvidenceEntry(raw: unknown, allowed: ReadonlySet<string>): EvidenceEntry | null {
  if (!isObj(raw)) return null;
  const prov = provenance(raw, allowed);
  const term = str(raw.term, 300);
  if (!prov || !term) return null;
  return { ...prov, ...regionTag(raw), term, seen_at: isoDate(raw.seen_at), score: num(raw.score), rank: num(raw.rank) };
}

function cleanNiche(raw: unknown, allowed: ReadonlySet<string>, custom: boolean): NicheInfo | null {
  if (!isObj(raw)) return null;
  const id = str(raw.id, 64);
  const name = str(raw.name, 200);
  if (!id || !NICHE_ID_RE.test(id) || !name) return null;
  const declared = Array.isArray(raw.sources) ? raw.sources.filter((s): s is string => typeof s === "string") : [];
  const sources = [...new Set(declared.filter((s) => allowed.has(s)))].sort();
  if (declared.length > 0 && sources.length === 0) return null; // niche built only from sources not served here
  const info: NicheInfo = { id, name, sources, updated_at: isoDate(raw.updated_at), custom };
  const description = str(raw.description, 400);
  if (description) info.description = description;
  if (isObj(raw.counts)) {
    info.counts = { trends: Math.trunc(num(raw.counts.trends) ?? 0), upcoming: Math.trunc(num(raw.counts.upcoming) ?? 0) };
  }
  const url = httpUrl(raw.url);
  if (custom && url) info.url = url;
  return info;
}

// ---- R2 access -------------------------------------------------------------------------------

export async function readJson(env: Env, key: string): Promise<Json | null> {
  const obj = await env.BUCKET.get(key);
  if (!obj) return null;
  try {
    const data: unknown = await obj.json();
    return isObj(data) ? data : null;
  } catch {
    return null; // corrupt object: behave as missing rather than 500
  }
}

export function nicheBase(key: Pick<KeyRow, "id">, nicheId: string): string {
  return nicheId.startsWith(CUSTOM_PREFIX) ? `custom/${key.id}/${nicheId}` : `catalog/${nicheId}`;
}

function checkNicheId(id: string): void {
  if (!NICHE_ID_RE.test(id)) throw new ApiError(400, "invalid_niche", "niche id must match [a-z0-9-], max 64 chars");
}

function notFound(what: string): ApiError {
  return new ApiError(404, "not_found", `${what} not found`);
}

export async function listNiches(env: Env, key: KeyRow): Promise<NicheInfo[]> {
  const allowed = allowedSources(key.scope);
  const out: NicheInfo[] = [];
  const catalog = await readJson(env, "catalog/niches.json");
  if (catalog && Array.isArray(catalog.niches)) {
    for (const n of catalog.niches) {
      const c = cleanNiche(n, allowed, false);
      if (c && !c.id.startsWith(CUSTOM_PREFIX)) out.push(c); // catalog ids can never shadow custom ones
    }
  }
  const mine = await readJson(env, `custom/${key.id}/niches.json`);
  if (mine && Array.isArray(mine.niches)) {
    for (const n of mine.niches) {
      const c = cleanNiche(n, allowed, true);
      if (c && c.id.startsWith(CUSTOM_PREFIX)) out.push(c);
    }
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

export async function getTrends(
  env: Env,
  key: KeyRow,
  nicheId: string,
  days: number,
  now: Date,
  region: string | null = null,
): Promise<{ niche: string; days: number; region: string | null; generated_at: string | null; items: TrendItem[] }> {
  checkNicheId(nicheId);
  const doc = await readJson(env, `${nicheBase(key, nicheId)}/trends.json`);
  if (!doc || !Array.isArray(doc.items)) throw notFound(`niche ${nicheId}`);
  const allowed = allowedSources(key.scope);
  const generated = isoDate(doc.generated_at);
  const since = now.getTime() - days * 86_400_000;
  const items: TrendItem[] = [];
  for (const raw of doc.items) {
    const it = cleanTrend(raw, allowed, generated);
    if (it && Date.parse(it.at) >= since && it.id.startsWith(`${nicheId}~`) && regionKeeps(it, it.regions, region)) items.push(it);
  }
  items.sort((a, b) => b.score - a.score || a.title.localeCompare(b.title));
  return { niche: nicheId, days, region, generated_at: generated, items };
}

export async function getUpcoming(
  env: Env,
  key: KeyRow,
  nicheId: string,
  days: number,
  now: Date,
  region: string | null = null,
): Promise<{ niche: string; days: number; region: string | null; generated_at: string | null; items: UpcomingItem[] }> {
  checkNicheId(nicheId);
  const doc = await readJson(env, `${nicheBase(key, nicheId)}/upcoming.json`);
  if (!doc || !Array.isArray(doc.items)) throw notFound(`niche ${nicheId}`);
  const allowed = allowedSources(key.scope);
  const today = now.toISOString().slice(0, 10);
  const horizon = new Date(now.getTime() + days * 86_400_000).toISOString().slice(0, 10);
  const items: UpcomingItem[] = [];
  for (const raw of doc.items) {
    const it = cleanUpcoming(raw, allowed, today);
    if (it && it.date >= today && it.date <= horizon && regionKeeps(it, [], region)) items.push(it);
  }
  items.sort((a, b) => a.date.localeCompare(b.date) || a.title.localeCompare(b.title));
  return { niche: nicheId, days, region, generated_at: isoDate(doc.generated_at), items };
}

export async function getEvidence(env: Env, key: KeyRow, itemId: string): Promise<EvidenceRecord> {
  const m = ITEM_ID_RE.exec(itemId);
  if (!m) throw new ApiError(400, "invalid_item_id", "item id looks like <niche>~<hash>");
  const nicheId = m[1] as string;
  const doc = await readJson(env, `${nicheBase(key, nicheId)}/evidence.json`);
  const rec = doc && isObj(doc.items) ? doc.items[itemId] : undefined;
  if (!isObj(rec)) throw notFound(`item ${itemId}`);
  const allowed = allowedSources(key.scope);
  const prov = provenance(rec, allowed); // the item itself must be servable, else it does not exist for this key
  const title = str(rec.title, 300);
  if (!prov || !title) throw notFound(`item ${itemId}`);
  const evidence = (Array.isArray(rec.evidence) ? rec.evidence : [])
    .map((e) => cleanEvidenceEntry(e, allowed))
    .filter((e): e is EvidenceEntry => e !== null);
  return { id: itemId, niche: nicheId, title, ...prov, evidence };
}

// ---- GitHub rising repos (catalog/github-rank.json, written by nicheclock.catalog) ---------------

export const GITHUB_WINDOWS = ["daily", "weekly", "monthly"] as const;
export type GithubWindow = (typeof GITHUB_WINDOWS)[number];

export interface GithubRepo extends Provenance {
  rank: number;
  repo: string;
  url: string;
  description: string | null;
  language: string | null;
  topics: string[];
  total_stars: number;
  total_forks: number;
  stars: number;
  forks: number;
  stars_per_day: number;
  series: number[];
  new_repo: boolean;
}

/**
 * github_rising: the repos of one window (daily = last UTC day, weekly = 7 days, monthly = 30 days) in rank order,
 * optionally one language (case-insensitive). The `github` source must be allowed for the caller (it is an
 * internal-scope source until sources.ts moves it to PUBLIC_SOURCES), and a row without url/license is dropped.
 */
export async function getGithubRising(
  env: Env,
  key: KeyRow,
  window: GithubWindow,
  language: string | null,
  limit: number,
): Promise<{ window: GithubWindow; language: string | null; date: string | null; generated_at: string | null; unit: string; note: string | null; items: GithubRepo[] }> {
  const doc = await readJson(env, "catalog/github-rank.json");
  if (!doc || !isObj(doc.ranks) || !Array.isArray(doc.repos)) throw notFound("github rank");
  const allowed = allowedSources(key.scope);
  const byName = new Map<string, Json>();
  for (const r of doc.repos) if (isObj(r) && typeof r.repo === "string") byName.set(r.repo, r);
  const order = Array.isArray((doc.ranks as Json)[window]) ? ((doc.ranks as Json)[window] as unknown[]) : [];
  const items: GithubRepo[] = [];
  for (const name of order) {
    const raw = typeof name === "string" ? byName.get(name) : undefined;
    if (!raw) continue;
    const prov = provenance(raw, allowed);
    const url = httpUrl(raw.url);
    const stars = isObj(raw.stars) ? num(raw.stars[window]) : null;
    if (!prov || !url || stars === null) continue;
    const lang = str(raw.language, 64);
    if (language !== null && (lang ?? "").toLowerCase() !== language.toLowerCase()) continue;
    const forks = isObj(raw.forks) ? num(raw.forks[window]) : null;
    const perDay = isObj(raw.stars_per_day) ? num(raw.stars_per_day[window]) : null;
    items.push({
      rank: items.length + 1,
      repo: name as string,
      url,
      description: str(raw.description, 300),
      language: lang,
      topics: Array.isArray(raw.topics) ? raw.topics.filter((t): t is string => typeof t === "string" && t.length <= 50).slice(0, 8) : [],
      total_stars: num(raw.total_stars) ?? 0,
      total_forks: num(raw.total_forks) ?? 0,
      stars,
      forks: forks ?? 0,
      stars_per_day: perDay ?? stars,
      series: Array.isArray(raw.series) ? raw.series.filter((x): x is number => typeof x === "number" && Number.isFinite(x)).slice(-14) : [],
      new_repo: raw.new_repo === true,
      ...prov,
    });
    if (items.length >= limit) break;
  }
  return { window, language, date: ymd(doc.date), generated_at: isoDate(doc.generated_at), unit: "stars/day", note: str(doc.note, 500), items };
}

export type { Scope };

/**
 * "Look up a word": POST /v1/resolve {"text": "..."} and the MCP tool resolve_topic.
 *
 * The concept list of the site (shards of concepts.json, keyed by the lower-cased name and every alias) is read first and needs no network.
 * Only a word that is not in it goes to Wikidata, as two small calls: wbsearchentities (the word), then wbgetentities for the English
 * Wikipedia sitelink of each candidate (the pool is made of Wikipedia pages). The word is the only thing sent to Wikidata; the person's
 * address is not part of this request. Nothing is stored or logged; failures are fixed messages.
 */
import type { Env } from "./env";
import { ApiError } from "./http";
import { Shards } from "./shards";
import { USER_AGENT } from "./suggest-net";

export interface Topic {
  qid: string;
  label: string;
  description: string;
}
export interface Resolved extends Topic {
  confidence: number;
  ambiguous: boolean;
  alternatives: Topic[];
  source: "nicheclock-list" | "wikidata";
}

export const MAX_TEXT = 100;

/** the key of a name: lower case, runs of white space as one space (shard.py norm_text) */
export const normText = (s: string): string => s.toLowerCase().split(/\s+/).filter(Boolean).join(" ");

export function cleanText(input: unknown): string {
  if (typeof input !== "string") throw new ApiError(400, "invalid_params", "text is required (a word or a short phrase)");
  const t = input.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").replace(/\s+/g, " ").trim();
  if (!t || t.length > MAX_TEXT) throw new ApiError(400, "invalid_params", `text must be 1 to ${MAX_TEXT} characters`);
  return t;
}

type Concept = [qid: number, label: string, description: string, popularity: number];

/** the concept list only (no network): the best match first, `ambiguous` when the runner-up has at least 0.6 of the best one's popularity */
export async function resolveLocal(env: Env, text: string): Promise<Resolved | null> {
  const key = `t${normText(text)}`;
  const u = (await new Shards(env, "concepts").units([key])).get(key);
  const cs = (u?.c as Concept[] | undefined) ?? [];
  const top = cs[0];
  if (!top) return null;
  const second = cs[1];
  const topic = (c: Concept): Topic => ({ qid: `Q${c[0]}`, label: c[1], description: c[2] });
  return {
    ...topic(top),
    confidence: 1,
    ambiguous: !!second && second[3] >= 0.6 * top[3],
    alternatives: cs.slice(1, 5).map(topic),
    source: "nicheclock-list",
  };
}

interface WdReply {
  error?: unknown;
  search?: { id?: unknown; label?: unknown; description?: unknown }[];
  entities?: Record<string, { sitelinks?: { enwiki?: unknown } } | undefined>;
}

async function wikidata(params: Record<string, string>): Promise<WdReply> {
  const qs = new URLSearchParams({ format: "json", maxlag: "5", ...params });
  let res: Response;
  try {
    res = await fetch(`https://www.wikidata.org/w/api.php?${qs}`, { headers: { "user-agent": USER_AGENT, accept: "application/json" }, signal: AbortSignal.timeout(5000) });
  } catch {
    throw new ApiError(502, "lookup_unavailable", "Wikidata could not be reached just now. Try again in a minute.");
  }
  if (res.status !== 200) {
    await res.body?.cancel().catch(() => undefined);
    throw new ApiError(502, "lookup_unavailable", "Wikidata could not answer just now. Try again in a minute.");
  }
  let body: WdReply;
  try {
    body = (await res.json()) as WdReply;
  } catch {
    throw new ApiError(502, "lookup_unavailable", "Wikidata sent an answer that could not be read. Try again in a minute.");
  }
  if (body.error) throw new ApiError(502, "lookup_unavailable", "Wikidata is busy just now. Try again in a minute.");
  return body;
}

/** Wikidata: candidates for a word, those with an English Wikipedia article first */
export async function resolveWikidata(text: string, context?: string): Promise<Resolved | null> {
  const found = await wikidata({ action: "wbsearchentities", search: text, language: "en", uselang: "en", type: "item", limit: "6" });
  const cands: { id: string; label: string; description: string }[] = [];
  for (const c of found.search ?? []) {
    if (typeof c.id === "string" && /^Q[1-9]\d*$/.test(c.id)) cands.push({ id: c.id, label: String(c.label ?? c.id), description: String(c.description ?? "") });
  }
  if (!cands.length) return null;
  const ents = await wikidata({ action: "wbgetentities", ids: cands.map((c) => c.id).join("|"), props: "sitelinks", sitefilter: "enwiki" });
  const has = new Set(cands.filter((c) => ents.entities?.[c.id]?.sitelinks?.enwiki).map((c) => c.id));
  const words = (context ?? "").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length >= 3);
  const inContext = (c: { description: string }) => words.some((w) => c.description.toLowerCase().includes(w));
  const ranked = [...cands].sort((a, b) => Number(has.has(b.id)) - Number(has.has(a.id)) || (context ? Number(inContext(b)) - Number(inContext(a)) : 0));
  const top = ranked[0] as (typeof cands)[number];
  const exact = (c: { label: string }) => c.label.toLowerCase() === text.toLowerCase();
  const sameName = ranked.filter((c) => has.has(c.id) && exact(c));
  return {
    qid: top.id,
    label: top.label,
    description: top.description,
    confidence: has.has(top.id) ? (exact(top) ? 0.9 : 0.7) : 0.4,
    ambiguous: sameName.length > 1,
    alternatives: ranked.slice(1, 5).map((c) => ({ qid: c.id, label: c.label, description: c.description })),
    source: "wikidata",
  };
}

/** concept list first, Wikidata for what it does not hold */
export async function resolveText(env: Env, text: string, context?: string): Promise<Resolved | null> {
  return (await resolveLocal(env, text)) ?? (await resolveWikidata(text, context));
}

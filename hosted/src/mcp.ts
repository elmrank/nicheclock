/**
 * Remote MCP endpoint: stateless Streamable HTTP, JSON-RPC 2.0 over POST /mcp, same Bearer key as REST.
 *
 * Spec followed: Model Context Protocol revision 2026-07-28 (current as of 2026-10-31), transport
 * https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
 * (stateless: no sessions, no initialize handshake, per-request _meta, MCP-Protocol-Version +
 * Mcp-Method + Mcp-Name headers validated, server/discover, resultType on results, ttlMs/cacheScope
 * on tools/list; GET and DELETE answer 405; Mcp-Session-Id is ignored and never minted).
 *
 * Dual-era: clients speaking the earlier handshake revisions (2025-11-25, 2025-06-18, 2025-03-26) are
 * served too, still without state: `initialize` just negotiates a version and returns capabilities,
 * `notifications/initialized` is accepted with 202, and no session id is issued
 * (https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning, "Backward Compatibility").
 *
 * Responses are always a single application/json object (never an SSE stream): allowed, and the tools
 * are plain reads. Batches are refused (removed from the spec in 2025-06-18).
 */
import { GITHUB_WINDOWS, getGithubRising, getTrends, getUpcoming, listNiches, parseRegion } from "./catalog";
import type { GithubWindow } from "./catalog";
import type { Env, KeyRow } from "./env";
import { ApiError, byteLength } from "./http";
import { createJob } from "./jobs";
import { resolveTopic, trendsForProfile, upcomingForProfile } from "./profile-tools";
import { trendingNow, upcomingEvents } from "./catalog-tools";
import { searchUpdates } from "./updates-tool";
import { checkProfileRate } from "./suggest-limits";

export const MCP_SPEC_VERSION = "2026-07-28";
export const LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"] as const;
export const SUPPORTED_VERSIONS = [MCP_SPEC_VERSION, ...LEGACY_VERSIONS];
const META_VERSION = "io.modelcontextprotocol/protocolVersion";
const META_CAPS = "io.modelcontextprotocol/clientCapabilities";
const META_SERVER = "io.modelcontextprotocol/serverInfo";
const MAX_BODY = 64 * 1024;

export const SERVER_INFO = { name: "nicheclock-hosted", title: "nicheclock", version: "0.1.0" };
const INSTRUCTIONS =
  "Trend radar over public data. trending_now(region, category, window, state, limit) lists the topics that are rising now, the same list as the " +
  "Trending now tab of the website; upcoming_events(region, days, limit, kind) lists dated events with the date to publish by; " +
  "search_updates(since, kind, limit) tells whether a Google core or spam update is rolling out now and lists Google Search ranking updates, documentation changes and blog posts. " +
  "trends_for_profile(profile) and upcoming_for_profile(profile) match a nicheclock profile (topics as Wikidata ids or words, a region) against the " +
  "rising Wikipedia pages and dated events; resolve_topic(text) finds the id of a word. The profile travels in the call and is not kept. " +
  "No volume is search volume: every number is in its own unit.";
const NICHE_INSTRUCTIONS =
  " trends_for_niche(niche, days) and upcoming(niche, days) take a niche id such as 'birdwatching' (an unknown id returns the list of valid ids); " +
  "niche_from_url(url) queues a custom niche (ready within about a day; call it again with the same url to see its status, then use the returned niche id).";

/** The niche tools need niches that exist: no public catalog is published and nothing builds custom niches, so external (public) keys do not see them. */
const NICHE_TOOLS_SET: ReadonlySet<string> = new Set(["trends_for_niche", "upcoming", "niche_from_url"]);

const TOOLS = [
  {
    name: "trending_now",
    title: "Topics that are rising now",
    description:
      "The topics that are rising now, the same list as the Trending now tab of nicheclock.elmrank.com. Each item has label, qid (Wikidata id, or null), " +
      "rise (a multiple of the topic's own 14-day median: 3.2 means +220%), volume with its unit (never search volume), state, the sources that " +
      "measure it, category, a one-line why and a link. The answer also has data_date, the licence of each source used and a note on units.",
    inputSchema: {
      type: "object",
      properties: {
        region: { type: "string", default: "global", description: "'global' (worldwide) or a country code: IN, CN, US, ID, PK, NG, BR, BD, RU, ET. Some countries have thin data; limited_note says so" },
        category: { type: "string", description: "Optional category id such as 'sports', 'politics' or 'technology' (a wrong one returns the list of valid ids)" },
        window: { type: "string", enum: ["24h", "48h", "7d"], default: "7d", description: "Window the rise is measured over" },
        state: { type: "string", enum: ["rising", "all"], default: "rising", description: "'rising' keeps topics rising in that window; 'all' adds steady top items and topics that are fading or ended" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20, description: "Number of items" },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "upcoming_events",
    title: "Dated upcoming events with a publish-by date",
    description:
      "Dated events in the next `days` days, soonest first, the same calendar as the Upcoming tab of nicheclock.elmrank.com. Each has date, title, kind, " +
      "publish_by (the event date minus a default lead time for that kind of event, not a measurement), days_until, days_to_publish_by, " +
      "publish_by_passed, source_url and license.",
    inputSchema: {
      type: "object",
      properties: {
        region: { type: "string", default: "global", description: "'global' (all events) or a country code: IN, CN, US, ID, PK, NG, BR, BD, RU, ET" },
        days: { type: "integer", minimum: 1, maximum: 365, default: 30, description: "Look-ahead window in days" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20, description: "Number of events" },
        kind: { type: "string", description: "Optional kind, e.g. 'film release', 'sports event', 'election', 'holiday' (a wrong one returns the list of valid kinds)" },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "search_updates",
    title: "Google Search updates",
    description:
      "Is a Google core or spam update rolling out now, and what changed in Google Search. The answer has rolling_now (true while a core or spam update has started and " +
      "Google has published no end date), rolling_name and rolling_started, and a list of items, newest first: ranking updates of the last 12 months (kind 'ranking': start date, " +
      "status 'started' while rolling or 'ended', end date and duration), Search and Crawling documentation changes (kind 'docs') and Search Central blog posts (kind 'blog'). " +
      "Each item has date, kind, category, title, url and a short summary in Google's own wording; the answer also has data_date and attribution (Google Search Central, CC BY 4.0). " +
      "The same data is on https://nicheclock.elmrank.com/google-updates/.",
    inputSchema: {
      type: "object",
      properties: {
        since: { type: "string", description: "Optional date, YYYY-MM-DD: only items on or after it (a ranking update counts if it started, ended or is still rolling on or after it)" },
        kind: { type: "string", enum: ["ranking", "docs", "blog"], description: "Optional: only ranking updates, only documentation changes or only blog posts" },
        limit: { type: "integer", minimum: 1, maximum: 100, default: 20, description: "Number of items" },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "trends_for_niche",
    title: "Trending topics for a niche",
    description:
      "Trending topics for one niche over the last `days` days (default 7), best score first. Each item has title, score, why, url, " +
      "and its source with source_url and license or attribution. Use the item id with the REST /v1/evidence/{id} for the raw signals.",
    inputSchema: {
      type: "object",
      properties: {
        niche: { type: "string", description: "Niche id, e.g. 'birdwatching', or a 'custom-...' id from niche_from_url" },
        days: { type: "integer", minimum: 1, maximum: 365, default: 7, description: "Look-back window in days" },
        region: { type: "string", description: "Optional country filter: two-letter code such as US keeps that country plus global items; omit or 'global' for no filter" },
      },
      required: ["niche"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "upcoming",
    title: "Upcoming dated events for a niche",
    description:
      "Dated upcoming events for one niche in the next `days` days (default 30), soonest first, each with publish_by (the date to " +
      "publish by so a page is indexed in time), days_until, source, source_url and license or attribution.",
    inputSchema: {
      type: "object",
      properties: {
        niche: { type: "string", description: "Niche id, e.g. 'birdwatching', or a 'custom-...' id" },
        days: { type: "integer", minimum: 1, maximum: 730, default: 30, description: "Look-ahead window in days" },
        region: { type: "string", description: "Optional country filter: two-letter code such as US keeps that country plus global items; omit or 'global' for no filter" },
      },
      required: ["niche"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "niche_from_url",
    title: "Create a custom niche from a site URL",
    description:
      "Queue a custom niche built from a public site URL (max 3 per key, refreshed daily). Returns a job and the niche id; it is " +
      "normally ready within a day. Call again with the same url to see the job status. When status is done, use the niche id " +
      "with trends_for_niche and upcoming.",
    inputSchema: {
      type: "object",
      properties: { url: { type: "string", description: "Public http(s) URL of the site, e.g. https://example.com" } },
      required: ["url"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "trends_for_profile",
    title: "Rising Wikipedia pages that fit a profile",
    description:
      "Rising and steady Wikipedia pages (last full day, per country) that fit a nicheclock profile, best fit first, deduplicated by Wikidata id. " +
      "Each row has fit (0 to 1), why, the relations that scored (kind, via, score), trend, volume with its unit, source, source_url and license. " +
      "The profile (schema nicheclock.profile/1) travels in the call and is not kept: {topics:[{qid:'Q718',label:'chess',role:'main'}|{text:'chess'}], region:'US'}. " +
      "A topic given as text is looked up in the concept list only; texts that are not in it come back in unresolved (pass a qid, or call resolve_topic). " +
      "Pools exist for US, IN, BR and ID.",
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "object", description: "nicheclock.profile/1: topics (1 to 8, each {qid,label?,role?} or {text}), region (US, IN, BR, ID), optional avoid [{qid}]" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20, description: "Number of rows" },
        min_fit: { type: "number", minimum: 0.35, maximum: 1, default: 0.5, description: "Lowest fit returned" },
      },
      required: ["profile"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "upcoming_for_profile",
    title: "Dated events and observances that fit a profile",
    description:
      "Dated events (Wikidata) and yearly observances (next date of the Wikidata rule) that fit a nicheclock profile, soonest first, each with publish_by " +
      "and, when the profile has cadence.days, your_slot (the latest usual day before publish_by). Holidays and Federal Register notices have no Wikidata id and are not matched.",
    inputSchema: {
      type: "object",
      properties: {
        profile: { type: "object", description: "nicheclock.profile/1, as for trends_for_profile; cadence.days (monday..sunday) adds your_slot" },
        days: { type: "integer", minimum: 1, maximum: 365, default: 30, description: "Look-ahead window in days" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20, description: "Number of events" },
      },
      required: ["profile"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
  {
    name: "resolve_topic",
    title: "Find the Wikidata id of a topic word",
    description:
      "Turn a word or short phrase into a Wikidata id for a profile. Reads the nicheclock concept list first; only a word that is not in it is sent to Wikidata " +
      "(this is the only profile tool that calls Wikidata). Returns qid, label, description, confidence, ambiguous and alternatives.",
    inputSchema: {
      type: "object",
      properties: {
        text: { type: "string", description: "The word or short phrase, for example 'chess'" },
        context: { type: "string", description: "Optional words about the site, used to prefer the matching meaning" },
      },
      required: ["text"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
  },
  {
    name: "github_rising",
    title: "Fastest-rising GitHub repositories",
    description:
      "Repositories ranked by stars in a window: daily (last UTC day), weekly (7 days) or monthly (30 days), optionally for one " +
      "language. Each item has stars, forks, stars_per_day, total_stars, description, language, topics, a 14-day series and its " +
      "source with source_url and license. Built from daily snapshots of the GitHub REST API; not a copy of github.com/trending.",
    inputSchema: {
      type: "object",
      properties: {
        language: { type: "string", description: "Optional primary language, e.g. 'Python' or 'Rust' (case-insensitive)" },
        window: { type: "string", enum: ["daily", "weekly", "monthly"], default: "daily", description: "daily, weekly or monthly" },
        limit: { type: "integer", minimum: 1, maximum: 100, default: 25, description: "Number of repos" },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: false },
  },
] as const;

function instructionsFor(env: Env, key: KeyRow): string {
  return key.scope === "external" && env.NICHE_TOOLS !== "on" ? INSTRUCTIONS : INSTRUCTIONS + NICHE_INSTRUCTIONS;
}

/** github_rising is listed and callable only while GITHUB_RANK = "on" (the GitHub rank is hidden on the site). */
function toolsFor(env: Env, key: KeyRow): readonly (typeof TOOLS)[number][] {
  return TOOLS.filter((t) => (env.GITHUB_RANK === "on" || t.name !== "github_rising") && (key.scope !== "external" || env.NICHE_TOOLS === "on" || !NICHE_TOOLS_SET.has(t.name)));
}

type Json = Record<string, unknown>;
type Id = string | number | null;

export interface McpReply {
  status: number;
  body: unknown | null; // null: no body (202)
  endpoint: string;
}

function rpcError(id: Id, code: number, message: string, data?: unknown): Json {
  return { jsonrpc: "2.0", id, error: data === undefined ? { code, message } : { code, message, data } };
}

function ok(id: Id, result: Json): Json {
  return { jsonrpc: "2.0", id, result };
}

function isObj(x: unknown): x is Json {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

/** Mcp-Name / Mcp-Param-* values may be carried as =?base64?...?= (UTF-8). */
export function decodeHeaderValue(v: string): string | null {
  const m = /^=\?base64\?([A-Za-z0-9+/]*={0,2})\?=$/.exec(v);
  if (!m) return v;
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(Uint8Array.from(atob(m[1] as string), (c) => c.charCodeAt(0)));
  } catch {
    return null;
  }
}

/** Origin allow-list (DNS-rebinding protection, required by the transport spec). No Origin header = not a browser = fine. */
export function originAllowed(req: Request, env: Env): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return true;
  const own = new URL(req.url).origin;
  const extra = (env.ALLOWED_ORIGINS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  let pub = "";
  try {
    pub = new URL(env.PUBLIC_BASE_URL).origin;
  } catch {
    /* placeholder */
  }
  return origin === own || origin === pub || extra.includes(origin);
}

function toolText(result: unknown, isError: boolean, modern: boolean, structured: boolean): Json {
  const text = typeof result === "string" ? result : JSON.stringify(result);
  const out: Json = { content: [{ type: "text", text }], isError };
  if (modern) out.resultType = "complete";
  if (structured && !isError && isObj(result)) out.structuredContent = result;
  return out;
}

async function callTool(env: Env, key: KeyRow, name: string, args: Json, now: Date): Promise<{ result: unknown; isError: boolean }> {
  try {
    if (name === "trends_for_niche" || name === "upcoming") {
      const niche = typeof args.niche === "string" ? args.niche.trim().toLowerCase() : "";
      if (!niche) return { result: "niche is required (a niche id such as 'birdwatching')", isError: true };
      const max = name === "upcoming" ? 730 : 365;
      const def = name === "upcoming" ? 30 : 7;
      const days = args.days === undefined ? def : args.days;
      if (typeof days !== "number" || !Number.isInteger(days) || days < 1 || days > max) {
        return { result: `days must be an integer between 1 and ${max}`, isError: true };
      }
      if (args.region !== undefined && typeof args.region !== "string") return { result: "region must be a two-letter country code or 'global'", isError: true };
      let region: string | null;
      try {
        region = parseRegion(args.region as string | undefined);
      } catch (e) {
        return { result: (e as Error).message, isError: true };
      }
      try {
        const data = name === "upcoming" ? await getUpcoming(env, key, niche, days, now, region) : await getTrends(env, key, niche, days, now, region);
        return { result: data, isError: false };
      } catch (e) {
        if (e instanceof ApiError && (e.status === 404 || e.code === "invalid_niche")) {
          const ids = (await listNiches(env, key)).map((n) => n.id);
          return { result: `${e.message}. Valid niche ids: ${ids.length ? ids.join(", ") : "(none yet)"}`, isError: true };
        }
        throw e;
      }
    }
    if (name === "trending_now") return { result: await trendingNow(env, args), isError: false };
    if (name === "upcoming_events") return { result: await upcomingEvents(env, args, now), isError: false };
    if (name === "search_updates") return { result: await searchUpdates(env, args, now), isError: false };
    if (name === "github_rising") {
      const window = args.window === undefined ? "daily" : args.window;
      if (typeof window !== "string" || !(GITHUB_WINDOWS as readonly string[]).includes(window)) {
        return { result: "window must be daily, weekly or monthly", isError: true };
      }
      if (args.language !== undefined && (typeof args.language !== "string" || args.language.length > 64)) {
        return { result: "language must be a string such as 'Python'", isError: true };
      }
      const limit = args.limit === undefined ? 25 : args.limit;
      if (typeof limit !== "number" || !Number.isInteger(limit) || limit < 1 || limit > 100) {
        return { result: "limit must be an integer between 1 and 100", isError: true };
      }
      const language = typeof args.language === "string" && args.language.trim() ? args.language.trim() : null;
      return { result: await getGithubRising(env, key, window as GithubWindow, language, limit), isError: false };
    }
    if (name === "trends_for_profile" || name === "upcoming_for_profile" || name === "resolve_topic") await checkProfileRate(env, key);
    if (name === "trends_for_profile") return { result: await trendsForProfile(env, args), isError: false };
    if (name === "upcoming_for_profile") return { result: await upcomingForProfile(env, args, now), isError: false };
    if (name === "resolve_topic") return { result: await resolveTopic(env, args), isError: false };
    if (name === "niche_from_url") {
      const { job, created } = await createJob(env, key, args.url, now);
      return { result: { job, created }, isError: false };
    }
  } catch (e) {
    if (e instanceof ApiError) return { result: `${e.code}: ${e.message}`, isError: true };
    throw e;
  }
  return { result: `unknown tool ${name}`, isError: true };
}

function endpointLabel(method: string): string {
  const known = ["initialize", "server/discover", "tools/list", "tools/call", "ping"];
  return known.includes(method) ? `POST /mcp ${method}` : "POST /mcp other";
}

export async function handleMcp(req: Request, env: Env, key: KeyRow, now: Date): Promise<McpReply> {
  const text = await req.text();
  if (byteLength(text) > MAX_BODY) return { status: 413, body: rpcError(null, -32600, "request too large"), endpoint: "POST /mcp other" };
  let msg: unknown;
  try {
    msg = JSON.parse(text);
  } catch {
    return { status: 400, body: rpcError(null, -32700, "parse error"), endpoint: "POST /mcp other" };
  }
  if (Array.isArray(msg)) {
    return { status: 400, body: rpcError(null, -32600, "JSON-RPC batches are not supported"), endpoint: "POST /mcp other" };
  }
  if (!isObj(msg) || msg.jsonrpc !== "2.0") {
    return { status: 400, body: rpcError(null, -32600, "invalid request: not a JSON-RPC 2.0 message"), endpoint: "POST /mcp other" };
  }
  // A client-sent response (we never send requests) or a notification: accept, no body.
  if (typeof msg.method !== "string") return { status: 202, body: null, endpoint: "POST /mcp other" };
  const method = msg.method;
  const endpoint = endpointLabel(method);
  if (!("id" in msg) || msg.id === undefined) return { status: 202, body: null, endpoint };
  const id: Id = typeof msg.id === "string" || typeof msg.id === "number" ? msg.id : null;
  const params: Json = isObj(msg.params) ? msg.params : {};
  const meta: Json = isObj(params._meta) ? params._meta : {};
  const headerVersion = req.headers.get("mcp-protocol-version");
  const metaVersion = typeof meta[META_VERSION] === "string" ? (meta[META_VERSION] as string) : null;

  // ---- which era? ----------------------------------------------------------------------------
  const modern = method !== "initialize" && (metaVersion !== null || headerVersion === MCP_SPEC_VERSION);

  if (modern) {
    const unsupported = (requested: string | null) =>
      ({ status: 400, body: rpcError(id, -32022, "Unsupported protocol version", { supported: SUPPORTED_VERSIONS, requested }), endpoint }) as McpReply;
    if (metaVersion === null) {
      return { status: 400, body: rpcError(id, -32602, `params._meta["${META_VERSION}"] is required`), endpoint };
    }
    if (metaVersion !== MCP_SPEC_VERSION) return unsupported(metaVersion);
    if (headerVersion !== metaVersion) {
      return { status: 400, body: rpcError(id, -32020, `Header mismatch: MCP-Protocol-Version ${JSON.stringify(headerVersion)} does not match _meta ${JSON.stringify(metaVersion)}`), endpoint };
    }
    if (!isObj(meta[META_CAPS])) {
      return { status: 400, body: rpcError(id, -32602, `params._meta["${META_CAPS}"] is required`), endpoint };
    }
    if (req.headers.get("mcp-method") !== method) {
      return { status: 400, body: rpcError(id, -32020, "Header mismatch: Mcp-Method is missing or does not match the request method"), endpoint };
    }
    if (method === "tools/call") {
      const h = req.headers.get("mcp-name");
      const decoded = h === null ? null : decodeHeaderValue(h);
      if (decoded === null || decoded !== params.name) {
        return { status: 400, body: rpcError(id, -32020, "Header mismatch: Mcp-Name is missing or does not match params.name"), endpoint };
      }
    }
    const serverMeta = { [META_SERVER]: SERVER_INFO };
    switch (method) {
      case "server/discover":
        return {
          status: 200,
          endpoint,
          body: ok(id, {
            resultType: "complete",
            supportedVersions: SUPPORTED_VERSIONS,
            capabilities: { tools: {} },
            instructions: instructionsFor(env, key),
            ttlMs: 3_600_000,
            cacheScope: "private",
            _meta: serverMeta,
          }),
        };
      case "tools/list":
        return { status: 200, endpoint, body: ok(id, { resultType: "complete", tools: toolsFor(env, key), ttlMs: 300_000, cacheScope: "private", _meta: serverMeta }) };
      case "tools/call": {
        const name = typeof params.name === "string" ? params.name : "";
        if (!toolsFor(env, key).some((t) => t.name === name)) return { status: 200, endpoint, body: rpcError(id, -32602, `Unknown tool: ${name}`) };
        const { result, isError } = await callTool(env, key, name, isObj(params.arguments) ? params.arguments : {}, now);
        return { status: 200, endpoint, body: ok(id, { ...toolText(result, isError, true, true), _meta: serverMeta }) };
      }
      default:
        return { status: 404, body: rpcError(id, -32601, `Method not found: ${method}`), endpoint };
    }
  }

  // ---- legacy era (handshake revisions), still stateless ---------------------------------------
  if (headerVersion !== null && !(LEGACY_VERSIONS as readonly string[]).includes(headerVersion) && method !== "initialize") {
    return { status: 400, body: rpcError(id, -32022, "Unsupported protocol version", { supported: SUPPORTED_VERSIONS, requested: headerVersion }), endpoint };
  }
  const negotiated = headerVersion ?? "2025-03-26";
  switch (method) {
    case "initialize": {
      const asked = typeof params.protocolVersion === "string" ? params.protocolVersion : "";
      const version = (LEGACY_VERSIONS as readonly string[]).includes(asked) ? asked : LEGACY_VERSIONS[0];
      return {
        status: 200,
        endpoint,
        body: ok(id, { protocolVersion: version, capabilities: { tools: { listChanged: false } }, serverInfo: SERVER_INFO, instructions: instructionsFor(env, key) }),
      };
    }
    case "ping":
      return { status: 200, endpoint, body: ok(id, {}) };
    case "server/discover":
      return { status: 400, body: rpcError(id, -32602, `params._meta["${META_VERSION}"] is required`), endpoint };
    case "tools/list":
      return { status: 200, endpoint, body: ok(id, { tools: toolsFor(env, key) }) };
    case "tools/call": {
      const name = typeof params.name === "string" ? params.name : "";
      if (!toolsFor(env, key).some((t) => t.name === name)) return { status: 200, endpoint, body: rpcError(id, -32602, `Unknown tool: ${name}`) };
      const { result, isError } = await callTool(env, key, name, isObj(params.arguments) ? params.arguments : {}, now);
      return { status: 200, endpoint, body: ok(id, toolText(result, isError, false, negotiated !== "2025-03-26")) };
    }
    default:
      return { status: 200, endpoint, body: rpcError(id, -32601, `Method not found: ${method}`) };
  }
}

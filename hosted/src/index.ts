/**
 * nicheclock hosted: REST /v1 + remote MCP /mcp + GitHub key issuance, on one Worker.
 * Layout and limits: hosted/README.md. Data schema: hosted/SCHEMA.md.
 */
import { buildReport } from "./admin";
import { GITHUB_WINDOWS, getEvidence, getGithubRising, getTrends, getUpcoming, listNiches, parseRegion } from "./catalog";
import type { Env, KeyRow, Scope } from "./env";
import { ApiError, byteLength, dayOf, errorReply, jsonReply, toResponse, type Reply } from "./http";
import { createJob, getJob, listJobsAdmin, updateJobAdmin } from "./jobs";
import { authenticate, listOwnKeys, revokeVisible } from "./keys";
import { handleMcp, originAllowed } from "./mcp";
import { resolveTopic, trendsForProfile, upcomingForProfile } from "./profile-tools";
import { checkProfileRate } from "./suggest-limits";
import { enforce, record } from "./quota";
import * as beacon from "./beacon";
import { serveStatic } from "./gate";
import { handleSuggest, isSuggestPath } from "./suggest";
import * as github from "./github";

interface Ctx {
  req: Request;
  env: Env;
  key: KeyRow;
  url: URL;
  params: Record<string, string>;
  now: Date;
}

interface Route {
  method: string;
  re: RegExp;
  /** metering label: the path template, never the concrete ids */
  endpoint: string;
  scope?: Scope;
  handler: (c: Ctx) => Promise<Reply>;
}

function intParam(url: URL, name: string, def: number, min: number, max: number): number {
  const raw = url.searchParams.get(name);
  if (raw === null || raw === "") return def;
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) throw new ApiError(400, "invalid_params", `${name} must be an integer between ${min} and ${max}`);
  return n;
}

async function jsonBody(req: Request): Promise<unknown> {
  const text = await req.text();
  if (byteLength(text) > 16 * 1024) throw new ApiError(413, "too_large", "request body too large");
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    throw new ApiError(400, "invalid_json", "body is not valid JSON");
  }
}

function objectBody(v: unknown): Record<string, unknown> {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ApiError(400, "invalid_json", "body must be a JSON object");
  return v as Record<string, unknown>;
}

const routes: Route[] = [
  {
    method: "GET",
    re: /^\/v1\/niches$/,
    endpoint: "GET /v1/niches",
    handler: async (c) => jsonReply({ niches: await listNiches(c.env, c.key) }),
  },
  {
    method: "GET",
    re: /^\/v1\/niches\/(?<id>[^/]+)\/trends$/,
    endpoint: "GET /v1/niches/:id/trends",
    handler: async (c) => jsonReply(await getTrends(c.env, c.key, c.params.id as string, intParam(c.url, "days", 7, 1, 365), c.now, parseRegion(c.url.searchParams.get("region")))),
  },
  {
    method: "GET",
    re: /^\/v1\/niches\/(?<id>[^/]+)\/upcoming$/,
    endpoint: "GET /v1/niches/:id/upcoming",
    handler: async (c) => jsonReply(await getUpcoming(c.env, c.key, c.params.id as string, intParam(c.url, "days", 30, 1, 730), c.now, parseRegion(c.url.searchParams.get("region")))),
  },
  {
    method: "GET",
    re: /^\/v1\/github\/rising$/,
    endpoint: "GET /v1/github/rising",
    handler: async (c) => {
      const window = c.url.searchParams.get("window") ?? "daily";
      if (!(GITHUB_WINDOWS as readonly string[]).includes(window)) throw new ApiError(400, "invalid_params", "window must be daily, weekly or monthly");
      const language = c.url.searchParams.get("language")?.trim() || null;
      if (language !== null && language.length > 64) throw new ApiError(400, "invalid_params", "language is too long");
      return jsonReply(await getGithubRising(c.env, c.key, window as (typeof GITHUB_WINDOWS)[number], language, intParam(c.url, "limit", 25, 1, 100)));
    },
  },
  {
    method: "GET",
    re: /^\/v1\/evidence\/(?<id>[^/]+)$/,
    endpoint: "GET /v1/evidence/:id",
    handler: async (c) => jsonReply(await getEvidence(c.env, c.key, c.params.id as string)),
  },
  {
    method: "POST",
    re: /^\/v1\/jobs$/,
    endpoint: "POST /v1/jobs",
    handler: async (c) => {
      const body = (await jsonBody(c.req)) as { url?: unknown };
      const { job, created } = await createJob(c.env, c.key, body?.url, c.now);
      return jsonReply({ job, created }, created ? 202 : 200, { location: `/v1/jobs/${job.id}` });
    },
  },
  {
    method: "GET",
    re: /^\/v1\/jobs\/(?<id>[^/]+)$/,
    endpoint: "GET /v1/jobs/:id",
    handler: async (c) => jsonReply({ job: await getJob(c.env, c.key, c.params.id as string) }),
  },
  {
    method: "POST",
    re: /^\/v1\/profile\/trends$/,
    endpoint: "POST /v1/profile/trends",
    handler: async (c) => {
      await checkProfileRate(c.env, c.key);
      return jsonReply(await trendsForProfile(c.env, objectBody(await jsonBody(c.req))));
    },
  },
  {
    method: "POST",
    re: /^\/v1\/profile\/upcoming$/,
    endpoint: "POST /v1/profile/upcoming",
    handler: async (c) => {
      await checkProfileRate(c.env, c.key);
      return jsonReply(await upcomingForProfile(c.env, objectBody(await jsonBody(c.req)), c.now));
    },
  },
  {
    method: "POST",
    re: /^\/v1\/profile\/resolve$/,
    endpoint: "POST /v1/profile/resolve",
    handler: async (c) => {
      await checkProfileRate(c.env, c.key);
      return jsonReply(await resolveTopic(c.env, objectBody(await jsonBody(c.req))));
    },
  },
  {
    method: "GET",
    re: /^\/v1\/keys$/,
    endpoint: "GET /v1/keys",
    handler: async (c) => {
      const keys = await listOwnKeys(c.env, c.key);
      return jsonReply({ keys: keys.map((k) => ({ ...k, current: k.id === c.key.id })) });
    },
  },
  {
    method: "DELETE",
    re: /^\/v1\/keys\/(?<id>[^/]+)$/,
    endpoint: "DELETE /v1/keys/:id",
    handler: async (c) => {
      const at = await revokeVisible(c.env, c.key, c.params.id as string, c.now);
      if (!at) throw new ApiError(404, "not_found", "no such key on your account");
      return jsonReply({ id: c.params.id, revoked_at: at });
    },
  },
  {
    method: "GET",
    re: /^\/v1\/admin\/report$/,
    endpoint: "GET /v1/admin/report",
    scope: "admin",
    handler: async (c) => jsonReply(await buildReport(c.env, c.now)),
  },
  {
    method: "GET",
    re: /^\/v1\/admin\/jobs$/,
    endpoint: "GET /v1/admin/jobs",
    scope: "admin",
    handler: async (c) => {
      const status = c.url.searchParams.get("status");
      if (status && !["queued", "running", "done", "failed"].includes(status)) throw new ApiError(400, "invalid_params", "unknown status");
      return jsonReply({ jobs: await listJobsAdmin(c.env, status, intParam(c.url, "limit", 50, 1, 200)) });
    },
  },
  {
    method: "POST",
    re: /^\/v1\/admin\/jobs\/(?<id>[^/]+)$/,
    endpoint: "POST /v1/admin/jobs/:id",
    scope: "admin",
    handler: async (c) => jsonReply({ job: await updateJobAdmin(c.env, c.params.id as string, await jsonBody(c.req), c.now) }),
  },
];

/** Authenticate, enforce quota, run, meter. Shared by REST and MCP. */
async function metered(
  req: Request,
  env: Env,
  now: Date,
  run: (key: KeyRow) => Promise<{ reply: Reply; endpoint: string }>,
): Promise<Response> {
  let key;
  try {
    key = await authenticate(env, req, dayOf(now), now);
  } catch (e) {
    if (e instanceof ApiError) return toResponse(errorReply(e));
    throw e;
  }
  let remaining: number;
  try {
    ({ remainingDay: remaining } = await enforce(env, key, now));
  } catch (e) {
    if (e instanceof ApiError) return toResponse(errorReply(e));
    throw e;
  }
  let result: { reply: Reply; endpoint: string };
  try {
    result = await run(key);
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    result = { reply: errorReply(e), endpoint: "error" };
  }
  return finish(env, key, result, remaining, now);
}

async function finish(env: Env, key: KeyRow, r: { reply: Reply; endpoint: string }, remaining: number, now: Date): Promise<Response> {
  await record(env, key.id, r.endpoint, byteLength(r.reply.body), now);
  r.reply.headers["x-ratelimit-remaining-day"] = String(remaining);
  return toResponse(r.reply);
}

async function handleApi(req: Request, env: Env, url: URL, now: Date): Promise<Response> {
  let match: { route: Route; params: Record<string, string> } | null = null;
  let pathKnown = false;
  for (const route of routes) {
    const m = route.re.exec(url.pathname);
    if (!m) continue;
    pathKnown = true;
    if (route.method === req.method) {
      match = { route, params: { ...(m.groups ?? {}) } };
      break;
    }
  }
  if (!match) {
    // 404/405 before auth reveal nothing: the route table is public in the docs.
    return toResponse(errorReply(pathKnown ? new ApiError(405, "method_not_allowed", "method not allowed") : new ApiError(404, "not_found", "no such endpoint")));
  }
  const { route, params } = match;
  return metered(req, env, now, async (key) => {
    if (route.scope && key.scope !== route.scope) throw new ApiError(403, "forbidden", `needs a key with scope ${route.scope}`);
    const reply = await route.handler({ req, env, key, url, params: decodeParams(params), now });
    return { reply, endpoint: route.endpoint };
  });
}

function decodeParams(p: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(p)) {
    try {
      out[k] = decodeURIComponent(v);
    } catch {
      out[k] = v;
    }
  }
  return out;
}

async function handleMcpRoute(req: Request, env: Env, now: Date): Promise<Response> {
  if (!originAllowed(req, env)) {
    return toResponse(jsonReply({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "Origin not allowed" } }, 403));
  }
  if (req.method !== "POST") {
    // Stateless transport: no standalone GET stream, no session to DELETE.
    return new Response(null, { status: 405, headers: { allow: "POST" } });
  }
  return metered(req, env, now, async (key) => {
    const r = await handleMcp(req, env, key, now);
    const body = r.body === null ? "" : JSON.stringify(r.body);
    return { reply: { status: r.status, body, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } }, endpoint: r.endpoint };
  });
}

/**
 * Indexing rule: exactly ONE URL may be indexed, the homepage `/` (tabs, regions and categories are
 * URL-hash state on `/`, so they are the same URL for a crawler). Every other response, API, pages, JSON, assets and
 * errors alike, says noindex. Opening another URL later = add its path to INDEXABLE_PATHS here, remove the meta robots
 * in the page (site/build.py INDEXABLE) and add it to the sitemap; nothing else changes.
 */
export const INDEXABLE_PATHS: ReadonlySet<string> = new Set(["/"]);
const NOINDEX_TAG = "noindex, nofollow";
const INDEX_TAG = "index, follow";

export function robotsTagFor(req: Request, res: Response): string {
  const { pathname } = new URL(req.url);
  const readOnly = req.method === "GET" || req.method === "HEAD";
  return readOnly && res.status === 200 && INDEXABLE_PATHS.has(pathname) ? INDEX_TAG : NOINDEX_TAG;
}

function withRobotsTag(req: Request, res: Response): Response {
  const out = new Response(res.body, res);
  out.headers.set("x-robots-tag", robotsTagFor(req, res));
  return out;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    return withRobotsTag(req, await route(req, env));
  },
} satisfies ExportedHandler<Env>;

async function route(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const now = new Date();
  const p = url.pathname;
  try {
    if (p === "/b") return await beacon.handle(req, env);
    if (p === "/healthz") return toResponse(jsonReply({ ok: true }));
    if ((p === "/keys" || p.startsWith("/keys/") || p.startsWith("/auth/")) && env.KEY_SIGNUP !== "on") {
      return toResponse(errorReply(new ApiError(404, "not_found", "no such page")));   // key issuance is closed until the hosted API opens
    }
    if (p === "/auth/github/login" && req.method === "GET") return github.login(env);
    if (p === "/auth/github/callback" && req.method === "GET") return await github.callback(req, env);
    if (p === "/keys" && req.method === "GET") return await github.keysPage(req, env);
    if (p === "/keys/new" && req.method === "POST") return await github.createKeyAction(req, env);
    if (p === "/auth/logout" && req.method === "POST") return await github.logoutAction(req, env);
    const rev = /^\/keys\/(?<id>k_[0-9a-f]{16})\/revoke$/.exec(p);
    if (rev && req.method === "POST") return await github.revokeKeyAction(req, env, rev.groups?.id as string);
    if (p === "/mcp") return await handleMcpRoute(req, env, now);
    if (isSuggestPath(p)) return await handleSuggest(req, env, p, now);     // the public buttons of the profile page: no key, limits per address
    if (p.startsWith("/v1/")) return await handleApi(req, env, url, now);
    return await serveStatic(req, env);
  } catch (e) {
    if (e instanceof ApiError) return toResponse(errorReply(e));
    console.error("unhandled", e instanceof Error ? e.stack : e);
    return toResponse(errorReply(new ApiError(500, "internal", "internal error")));
  }
}

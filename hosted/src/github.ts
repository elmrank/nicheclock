/**
 * Key issuance through GitHub OAuth (web application flow):
 *   GET  /auth/github/login     -> state cookie + redirect to github.com/login/oauth/authorize (scope user:email)
 *   GET  /auth/github/callback  -> check state, exchange code, read /user and /user/emails, keep the
 *                                  VERIFIED PRIMARY email, set a signed 1-hour session cookie, 303 /keys
 *   GET  /keys                  -> minimal HTML page: sign-in link, or list keys + create + revoke
 *   POST /keys/new              -> creates a key, shows it ONCE in the response (never in a URL)
 *   POST /keys/{id}/revoke      -> revoke one of your keys
 *   POST /auth/logout
 * The GitHub access token is used for two reads and discarded; it is never stored. No Turnstile:
 * a GitHub account with a verified email is the abuse gate (see README.md).
 * Docs: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
 */
import type { Env } from "./env";
import { ApiError, b64url, b64urlDecode, encoder, hex, randomHex } from "./http";
import { createKey, listKeysForGithubId, revokeOwnedByGithub, type KeyInfo } from "./keys";

const GH_AUTHORIZE = "https://github.com/login/oauth/authorize";
const GH_TOKEN = "https://github.com/login/oauth/access_token";
const GH_API = "https://api.github.com";
const SESSION_TTL_S = 3600;
const STATE_COOKIE = "nk_oauth";
const SESSION_COOKIE = "nk_session";

export interface Session {
  gid: number;
  login: string;
  email: string;
  exp: number; // epoch seconds
}

const PAGE_HEADERS: Record<string, string> = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "x-frame-options": "DENY",
  "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
};

export function esc(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

function page(title: string, body: string, status = 200, extra: Headers | null = null): Response {
  const h = new Headers(PAGE_HEADERS);
  extra?.forEach((v, k) => (k === "set-cookie" ? h.append(k, v) : h.set(k, v)));
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(title)} - nicheclock</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1rem;color:#111}
code,pre{font:14px ui-monospace,monospace;background:#f3f3f3;padding:.15rem .3rem;border-radius:4px}pre{padding:.75rem;overflow:auto}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:.35rem .5rem;text-align:left;font-size:14px}
button,.btn{font:inherit;padding:.4rem .8rem;border:1px solid #111;background:#111;color:#fff;border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}
button.ghost{background:#fff;color:#111}.warn{background:#fff6d6;border:1px solid #e5c200;padding:.75rem;border-radius:6px}</style></head>
<body><h1>nicheclock</h1>${body}</body></html>`;
  return new Response(html, { status, headers: h });
}

// ---- cookies + signed session ---------------------------------------------------------------

function cookies(req: Request): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (req.headers.get("cookie") ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
  }
  return out;
}

function cookie(env: Env, name: string, value: string, maxAge: number, path: string): string {
  const secure = env.PUBLIC_BASE_URL.startsWith("https://") ? "; Secure" : "";
  return `${name}=${value}; Max-Age=${maxAge}; Path=${path}; HttpOnly; SameSite=Lax${secure}`;
}

async function hmac(secret: string, data: string): Promise<Uint8Array> {
  const k = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, encoder.encode(data)));
}

function secretOf(env: Env): string {
  if (!env.SESSION_SECRET || env.SESSION_SECRET.length < 16) {
    throw new ApiError(503, "not_configured", "SESSION_SECRET is not set on this deployment");
  }
  return env.SESSION_SECRET;
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= (a[i] as number) ^ (b[i] as number);
  return d === 0;
}

export async function signSession(env: Env, s: Session): Promise<string> {
  const payload = b64url(encoder.encode(JSON.stringify(s)));
  return `${payload}.${b64url(await hmac(secretOf(env), payload))}`;
}

export async function readSession(env: Env, req: Request, nowS = Math.floor(Date.now() / 1000)): Promise<{ s: Session; csrf: string } | null> {
  const raw = cookies(req)[SESSION_COOKIE];
  if (!raw) return null;
  const [payload, sig] = raw.split(".");
  if (!payload || !sig) return null;
  const secret = secretOf(env);
  let given: Uint8Array;
  try {
    given = b64urlDecode(sig);
  } catch {
    return null;
  }
  if (!timingSafeEqual(await hmac(secret, payload), given)) return null;
  try {
    const s = JSON.parse(new TextDecoder().decode(b64urlDecode(payload))) as Session;
    if (typeof s.gid !== "number" || typeof s.email !== "string" || s.exp <= nowS) return null;
    return { s, csrf: hex(await hmac(secret, `csrf:${payload}`)).slice(0, 32) };
  } catch {
    return null;
  }
}

// ---- OAuth ---------------------------------------------------------------------------------

function oauthConfig(env: Env): { id: string; secret: string } {
  if (!env.GITHUB_CLIENT_ID || !env.GITHUB_CLIENT_SECRET) {
    throw new ApiError(503, "not_configured", "GitHub OAuth is not configured on this deployment");
  }
  return { id: env.GITHUB_CLIENT_ID, secret: env.GITHUB_CLIENT_SECRET };
}

function redirectUri(env: Env): string {
  return new URL("/auth/github/callback", env.PUBLIC_BASE_URL).toString();
}

export function login(env: Env): Response {
  const cfg = oauthConfig(env);
  const state = randomHex(16);
  const url = new URL(GH_AUTHORIZE);
  url.searchParams.set("client_id", cfg.id);
  url.searchParams.set("redirect_uri", redirectUri(env));
  url.searchParams.set("scope", "user:email");
  url.searchParams.set("state", state);
  url.searchParams.set("allow_signup", "true");
  return new Response(null, {
    status: 302,
    headers: { location: url.toString(), "set-cookie": cookie(env, STATE_COOKIE, state, 600, "/auth/github"), "cache-control": "no-store" },
  });
}

async function gh<T>(url: string, init: RequestInit): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: {
      accept: "application/vnd.github+json",
      "user-agent": "nicheclock-hosted",
      "x-github-api-version": "2022-11-28",
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  if (!r.ok) throw new ApiError(502, "github_error", `GitHub answered ${r.status}`);
  return (await r.json()) as T;
}

/** Verified primary email from GET /user/emails, lower-cased; null when GitHub has none. */
export function primaryVerifiedEmail(list: unknown): string | null {
  if (!Array.isArray(list)) return null;
  const hit = list.find((e) => e && e.primary === true && e.verified === true && typeof e.email === "string");
  return hit ? (hit.email as string).trim().toLowerCase() : null;
}

export async function callback(req: Request, env: Env): Promise<Response> {
  const cfg = oauthConfig(env);
  const url = new URL(req.url);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  const expected = cookies(req)[STATE_COOKIE];
  const clear = new Headers({ "set-cookie": cookie(env, STATE_COOKIE, "", 0, "/auth/github") });
  if (url.searchParams.get("error")) return page("Sign-in cancelled", `<p>GitHub sign-in was cancelled.</p><p><a href="/keys">Back</a></p>`, 400, clear);
  if (!state || !code || !expected || state !== expected) {
    return page("Sign-in failed", `<p>The sign-in state did not match. Start again from <a href="/keys">the key page</a>.</p>`, 400, clear);
  }
  // Exchange the code (server-to-server; the client secret never reaches the browser).
  const tok = await gh<{ access_token?: string; error?: string }>(GH_TOKEN, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ client_id: cfg.id, client_secret: cfg.secret, code, redirect_uri: redirectUri(env) }),
  });
  if (!tok.access_token) return page("Sign-in failed", `<p>GitHub refused the code (${esc(tok.error ?? "unknown")}). Try again.</p>`, 400, clear);
  const auth = { authorization: `Bearer ${tok.access_token}` };
  const user = await gh<{ id?: number; login?: string }>(`${GH_API}/user`, { headers: auth });
  const email = primaryVerifiedEmail(await gh<unknown>(`${GH_API}/user/emails`, { headers: auth }));
  if (typeof user.id !== "number") throw new ApiError(502, "github_error", "GitHub returned no user id");
  if (!email) {
    return page(
      "No verified email",
      `<p>Your GitHub account has no <b>verified primary</b> email. Verify one at github.com/settings/emails and sign in again.</p>`,
      403,
      clear,
    );
  }
  const session = await signSession(env, { gid: user.id, login: String(user.login ?? ""), email, exp: Math.floor(Date.now() / 1000) + SESSION_TTL_S });
  const h = new Headers({ location: "/keys", "cache-control": "no-store" });
  h.append("set-cookie", cookie(env, SESSION_COOKIE, session, SESSION_TTL_S, "/"));
  h.append("set-cookie", cookie(env, STATE_COOKIE, "", 0, "/auth/github"));
  return new Response(null, { status: 303, headers: h });
}

// ---- key page ------------------------------------------------------------------------------

function keyRows(keys: KeyInfo[], csrf: string): string {
  if (!keys.length) return `<p>No keys yet.</p>`;
  const rows = keys
    .map((k) => {
      const state = k.revoked_at ? `revoked ${esc(k.revoked_at.slice(0, 10))}` : "active";
      const action = k.revoked_at
        ? ""
        : `<form method="post" action="/keys/${esc(k.id)}/revoke"><input type="hidden" name="csrf" value="${esc(csrf)}"><button class="ghost">Revoke</button></form>`;
      return `<tr><td><code>${esc(k.id)}</code></td><td>${esc(k.created_at.slice(0, 10))}</td><td>${esc(k.last_used_at ? k.last_used_at.slice(0, 10) : "never")}</td><td>${state}</td><td>${action}</td></tr>`;
    })
    .join("");
  return `<table><tr><th>Key id</th><th>Created</th><th>Last used</th><th>Status</th><th></th></tr>${rows}</table>`;
}

export async function keysPage(req: Request, env: Env): Promise<Response> {
  const sess = await readSession(env, req);
  if (!sess) {
    return page(
      "API key",
      `<p>Free API key and remote MCP for the nicheclock trend radar. Sign in with GitHub to get a key (your verified primary email is used to limit keys per person).</p>
<p><a class="btn" href="/auth/github/login">Sign in with GitHub</a></p>`,
    );
  }
  const keys = await listKeysForGithubId(env, sess.s.gid);
  return page(
    "API key",
    `<p>Signed in as <b>${esc(sess.s.login || sess.s.email)}</b> (${esc(sess.s.email)}).</p>
<h2>Your keys</h2>${keyRows(keys, sess.csrf)}
<form method="post" action="/keys/new"><input type="hidden" name="csrf" value="${esc(sess.csrf)}"><button>Create key</button></form>
<p>Up to 2 active keys per email. Free quota: 1,000 requests/day and 60/minute per key. <a href="/docs/mcp/#hosted-server">How to connect Claude, Cursor and others</a>.</p>
<form method="post" action="/auth/logout"><input type="hidden" name="csrf" value="${esc(sess.csrf)}"><button class="ghost">Sign out</button></form>`,
  );
}

async function checkedSession(req: Request, env: Env): Promise<{ s: Session; csrf: string } | Response> {
  const sess = await readSession(env, req);
  if (!sess) return page("Signed out", `<p>Your session expired. <a href="/keys">Sign in again</a>.</p>`, 401);
  const form = await req.formData().catch(() => null);
  const sent = form?.get("csrf");
  if (typeof sent !== "string" || !timingSafeEqual(encoder.encode(sent), encoder.encode(sess.csrf))) {
    return page("Rejected", `<p>Form token missing or wrong. <a href="/keys">Reload the key page</a>.</p>`, 403);
  }
  return sess;
}

export async function createKeyAction(req: Request, env: Env): Promise<Response> {
  const sess = await checkedSession(req, env);
  if (sess instanceof Response) return sess;
  try {
    const k = await createKey(env, { githubId: sess.s.gid, email: sess.s.email });
    return page(
      "New key",
      `<div class="warn"><b>Copy this key now.</b> It is shown once; only a hash is stored, so it cannot be recovered. If you lose it, revoke it and create another.</div>
<pre>${esc(k.key)}</pre>
<p>Key id <code>${esc(k.id)}</code>, created ${esc(k.created_at)}.</p>
<p>MCP endpoint: <code>${esc(env.PUBLIC_BASE_URL)}/mcp</code> with the header <code>Authorization: Bearer</code> and this key. Tools: trending_now, upcoming_events, trends_for_profile, upcoming_for_profile, resolve_topic. <a href="/docs/mcp/#hosted-server">Setup for Claude Code, Claude Desktop and Cursor</a>.</p>
<p>Check it: <code>curl -X POST -H "Authorization: Bearer ${esc(k.key)}" -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' ${esc(env.PUBLIC_BASE_URL)}/mcp</code></p>
<p><a href="/keys">Back to your keys</a></p>`,
    );
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      return page("Key limit", `<p>${esc(e.message)}.</p><p><a href="/keys">Back</a></p>`, 409);
    }
    throw e;
  }
}

export async function revokeKeyAction(req: Request, env: Env, keyId: string): Promise<Response> {
  const sess = await checkedSession(req, env);
  if (sess instanceof Response) return sess;
  const found = await revokeOwnedByGithub(env, sess.s.gid, keyId);
  if (!found) return page("Not found", `<p>No such key on your account.</p><p><a href="/keys">Back</a></p>`, 404);
  return new Response(null, { status: 303, headers: { location: "/keys", "cache-control": "no-store" } });
}

export async function logoutAction(req: Request, env: Env): Promise<Response> {
  const sess = await checkedSession(req, env);
  if (sess instanceof Response) return sess;
  return new Response(null, {
    status: 303,
    headers: { location: "/keys", "set-cookie": cookie(env, SESSION_COOKIE, "", 0, "/"), "cache-control": "no-store" },
  });
}


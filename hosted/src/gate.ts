/**
 * HTTP Basic gate for the static site (landing, docs, images, sitemap). The API (/v1, /mcp), the key pages and
 * /b keep their own auth in index.ts. robots.txt stays public. The password is the Worker secret
 * BASIC_PASSWORD and the user name is the Worker secret BASIC_USER (neither is in the repo, and the user name has
 * no default). Without either secret the gate fails closed: every static request is refused.
 */
import type { Env } from "./env";
import { b64urlDecode, encoder } from "./http";

const PUBLIC_PATHS = new Set(["/robots.txt"]);

function challenge(status = 401): Response {
  return new Response("Authentication required\n", {
    status,
    headers: { "www-authenticate": 'Basic realm="nicheclock", charset="UTF-8"', "cache-control": "no-store", "content-type": "text/plain; charset=utf-8" },
  });
}

async function sameSecret(a: string, b: string): Promise<boolean> {
  // Compare SHA-256 digests with a constant-time loop so length and content do not leak through timing.
  const [da, db] = await Promise.all([crypto.subtle.digest("SHA-256", encoder.encode(a)), crypto.subtle.digest("SHA-256", encoder.encode(b))]);
  const x = new Uint8Array(da);
  const y = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

function credentials(req: Request): { user: string; password: string } | null {
  const h = req.headers.get("authorization") ?? "";
  const m = /^Basic\s+([A-Za-z0-9+/_=-]+)$/i.exec(h);
  if (!m?.[1]) return null;
  try {
    const raw = new TextDecoder().decode(b64urlDecode(m[1].replace(/=+$/, "")));
    const i = raw.indexOf(":");
    return i < 0 ? null : { user: raw.slice(0, i), password: raw.slice(i + 1) };
  } catch {
    return null;
  }
}

/** Serve a static asset after a valid Basic login (or without one when SITE_GATE is "off"). */
export async function serveStatic(req: Request, env: Env): Promise<Response> {
  if (env.SITE_GATE !== "off" && !PUBLIC_PATHS.has(new URL(req.url).pathname)) {
    if (!env.BASIC_PASSWORD || !env.BASIC_USER) return new Response("static site gate is not configured\n", { status: 503, headers: { "cache-control": "no-store" } });
    const c = credentials(req);
    const ok = c !== null && (await sameSecret(c.user, env.BASIC_USER)) && (await sameSecret(c.password, env.BASIC_PASSWORD));
    if (!ok) return challenge();
  }
  return env.ASSETS.fetch(req);
}

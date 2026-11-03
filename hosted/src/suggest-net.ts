/**
 * What "Read my site" may reach, and how: the target check (condition (a) of the plan), the neutral user agent, and a fetch that reads at
 * most a given number of bytes, follows at most one redirect and never to another host. No URL, host or body is ever logged: nothing in this
 * file calls `console`, and every error that leaves it is an ApiError with a fixed message.
 *
 * The limit of the host check, stated plainly: a Worker cannot resolve DNS, so a public-looking name that points to a private address is not
 * caught here. What stops it is (1) every IP literal is refused, whatever its range (`privateIpv4` / `privateIpv6` say which range, so the
 * message can), (2) the obvious internal names are refused (single-label names, .local, .internal, .home.arpa, .localhost, ...) and the
 * public wildcard-DNS services that turn a name into any address (nip.io, sslip.io, ...), and (3) Cloudflare's own network does not route a
 * Worker's fetch to RFC 1918 or loopback space. Document: docs/personalization.md, "Read my site".
 */
import { ApiError } from "./http";

export const PRODUCT_TOKEN = "nicheclock-suggest";
export const USER_AGENT = "nicheclock-suggest/1.0 (+https://nicheclock.elmrank.com/bot; oss@elmrank.com)";
export const MAX_SUBREQUESTS = 26;
export const CALL_DEADLINE_MS = 15_000;
const STEP_TIMEOUT_MS = 6_000;

const INTERNAL_TLDS = new Set(["local", "localhost", "internal", "lan", "home", "corp", "intranet", "private", "test", "invalid", "example", "onion", "arpa", "localdomain", "domain", "host", "router", "workgroup", "ts"]);
/** public services that resolve any name under them to an address of the caller's choosing (127.0.0.1.nip.io): a way round an IP literal check */
const WILDCARD_DNS = ["nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me", "traefik.me", "local.gd", "vcap.me", "lacolhost.com", "fbi.com", "1u.ms", "nip.direct"];

const blocked = (message: string) => new ApiError(400, "blocked_host", `${message} Type your topics instead.`);
export const invalidUrl = () => new ApiError(400, "invalid_url", "That does not look like the address of a public website, for example https://example.org. Type your topics instead.");

export function privateIpv4(host: string): string | null {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (!m) return null;
  const [a, b] = [Number(m[1]), Number(m[2])];
  if (a === 0) return "this-network";
  if (a === 10) return "private";
  if (a === 127) return "loopback";
  if (a === 100 && b >= 64 && b <= 127) return "cgnat";
  if (a === 169 && b === 254) return "link-local";
  if (a === 172 && b >= 16 && b <= 31) return "private";
  if (a === 192 && b === 168) return "private";
  if (a === 192 && b === 0 && Number(m[3]) === 0) return "special";
  if (a === 198 && (b === 18 || b === 19)) return "benchmark";
  if (a >= 224) return "reserved";
  return "public";
}

export function privateIpv6(host: string): string | null {
  const h = host.replace(/^\[|\]$/g, "").toLowerCase();
  if (!h.includes(":")) return null;
  if (h === "::1" || h === "::") return "loopback";
  if (/^f[cd]/.test(h)) return "private";
  if (/^fe[89ab]/.test(h)) return "link-local";
  const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(h);
  if (mapped) return privateIpv4(mapped[1] as string) ?? "reserved";
  if (/^::ffff:[0-9a-f]{1,4}:[0-9a-f]{1,4}$/.test(h)) return "mapped";
  return "public";
}

export interface Target {
  scheme: "https:" | "http:";
  host: string;
  origin: string;
}

/** Validate what the person typed. Public http(s) host names only. Throws ApiError(400); the message never contains the input. */
export function checkTarget(input: unknown): Target {
  if (typeof input !== "string" || !input.trim()) throw invalidUrl();
  const raw = input.trim();
  if (raw.length > 2048 || /[\u0000-\u001f\u007f]/.test(raw)) throw invalidUrl();
  let u: URL;
  try {
    u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    throw invalidUrl();
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw invalidUrl();
  if (u.username || u.password) throw blocked("Addresses with a user name or password are refused.");
  if (u.port && u.port !== "80" && u.port !== "443") throw blocked("Only the standard ports are allowed.");
  const host = u.hostname.toLowerCase().replace(/\.$/, "");
  if (host.length > 253) throw invalidUrl();
  const v4 = privateIpv4(host);
  const v6 = privateIpv6(host);
  if (v4 !== null || v6 !== null) {
    const kind = v4 ?? v6;
    throw blocked(kind === "public" ? "An IP address is not accepted, use the site's name." : "That address is private or internal and is never read.");
  }
  const labels = host.split(".");
  const tld = labels[labels.length - 1] as string;
  if (labels.length < 2 || INTERNAL_TLDS.has(tld) || labels.includes("localhost") || /^\d+$/.test(tld)) throw blocked("That name is internal and is never read.");
  if (WILDCARD_DNS.some((d) => host === d || host.endsWith(`.${d}`))) throw blocked("That name belongs to a service that can point to any address, so it is not read.");
  if (!labels.every((l) => /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(l) && l.length <= 63)) throw invalidUrl();
  return { scheme: u.protocol as Target["scheme"], host, origin: `${u.protocol}//${host}` };
}

/** same site: the same host, or the same host with or without a leading www. */
export const sameSite = (a: string, b: string): boolean => a.replace(/^www\./, "") === b.replace(/^www\./, "");

export interface Budget {
  used: number;
  deadline: number;
}
export const newBudget = (now = Date.now()): Budget => ({ used: 0, deadline: now + CALL_DEADLINE_MS });

export interface Fetched {
  status: number;
  bytes: Uint8Array;
  truncated: boolean;
  contentType: string;
  /** a redirect that was refused (another host, a second hop, or no Location) */
  redirectRefused: boolean;
}

async function readCapped(res: Response, max: number): Promise<{ bytes: Uint8Array; truncated: boolean }> {
  const reader = res.body?.getReader();
  if (!reader) return { bytes: new Uint8Array(0), truncated: false };
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    chunks.push(value);
    if (total >= max) {
      truncated = true;     // the cap was reached: the body may go on
      await reader.cancel().catch(() => undefined);
      break;
    }
  }
  const out = new Uint8Array(Math.min(total, max));
  let at = 0;
  for (const c of chunks) {
    const take = Math.min(c.byteLength, out.byteLength - at);
    if (take <= 0) break;
    out.set(c.subarray(0, take), at);
    at += take;
  }
  return { bytes: out, truncated };
}

/** One GET with a neutral user agent. At most one redirect, to the same host and port only (the second hop, or any other host, is refused). */
export async function fetchLimited(budget: Budget, url: URL, maxBytes: number): Promise<Fetched> {
  let target = url;
  for (let hop = 0; hop < 2; hop++) {
    if (budget.used >= MAX_SUBREQUESTS) throw new ApiError(502, "too_many_steps", "The call hit its step limit before it finished. Type your topics instead.");
    const left = budget.deadline - Date.now();
    if (left <= 0) throw new ApiError(504, "timeout", "Reading the site took too long. Type your topics instead.");
    budget.used++;
    let res: Response;
    try {
      res = await fetch(target.toString(), {
        method: "GET",
        redirect: "manual",
        headers: { "user-agent": USER_AGENT, accept: "text/html,application/xhtml+xml,application/xml,text/xml,text/plain;q=0.9,*/*;q=0.5" },
        signal: AbortSignal.timeout(Math.min(STEP_TIMEOUT_MS, left)),
      });
    } catch {
      throw new ApiError(502, "site_unreachable", "The site could not be reached. Type your topics instead.");
    }
    if (res.status >= 300 && res.status < 400 && res.status !== 304) {
      const loc = res.headers.get("location");
      await res.body?.cancel().catch(() => undefined);
      let next: URL | null = null;
      try {
        next = loc ? new URL(loc, target) : null;
      } catch {
        next = null;
      }
      const ok = hop === 0 && next !== null && (next.protocol === "https:" || next.protocol === "http:") && next.hostname.toLowerCase().replace(/\.$/, "") === target.hostname.toLowerCase().replace(/\.$/, "") && (next.port === "" || next.port === "80" || next.port === "443");
      if (!ok || !next) return { status: res.status, bytes: new Uint8Array(0), truncated: false, contentType: "", redirectRefused: true };
      target = next;
      continue;
    }
    const contentType = (res.headers.get("content-type") ?? "").toLowerCase();
    if (res.status < 200 || res.status >= 300) {
      await res.body?.cancel().catch(() => undefined);
      return { status: res.status, bytes: new Uint8Array(0), truncated: false, contentType, redirectRefused: false };
    }
    let body: { bytes: Uint8Array; truncated: boolean };
    try {
      body = await readCapped(res, maxBytes);
    } catch {
      throw new ApiError(502, "site_unreachable", "The site could not be read to the end. Type your topics instead.");
    }
    return { status: res.status, ...body, contentType, redirectRefused: false };
  }
  return { status: 310, bytes: new Uint8Array(0), truncated: false, contentType: "", redirectRefused: true };
}

/** gunzip with an output cap; a damaged or cut stream gives what was decoded so far */
export async function gunzipCapped(bytes: Uint8Array, max: number): Promise<{ bytes: Uint8Array; truncated: boolean }> {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  let truncated = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      chunks.push(value);
      if (total > max) {
        truncated = true;
        await reader.cancel().catch(() => undefined);
        break;
      }
    }
  } catch {
    /* damaged or cut: keep what was decoded */
  }
  const out = new Uint8Array(Math.min(total, max));
  let at = 0;
  for (const c of chunks) {
    const take = Math.min(c.byteLength, out.byteLength - at);
    if (take <= 0) break;
    out.set(c.subarray(0, take), at);
    at += take;
  }
  return { bytes: out, truncated };
}

/** Small response helpers. Every API error has the shape {"error": {"code", "message"}}. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly headers: Record<string, string> = {},
  ) {
    super(message);
  }
}

export interface Reply {
  status: number;
  body: string;
  headers: Record<string, string>;
}

export function jsonReply(data: unknown, status = 200, headers: Record<string, string> = {}): Reply {
  return {
    status,
    body: JSON.stringify(data),
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  };
}

export function errorReply(e: ApiError): Reply {
  return jsonReply({ error: { code: e.code, message: e.message } }, e.status, e.headers);
}

export function toResponse(r: Reply): Response {
  return new Response(r.status === 204 || r.body === "" ? null : r.body, { status: r.status, headers: r.headers });
}

export const encoder = new TextEncoder();

export function byteLength(s: string): number {
  return encoder.encode(s).byteLength;
}

export function isoNow(now: Date = new Date()): string {
  return now.toISOString();
}

export function dayOf(now: Date): string {
  return now.toISOString().slice(0, 10);
}

/** Seconds until the next UTC midnight (at least 1). */
export function secondsToMidnight(now: Date): number {
  const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return Math.max(1, Math.ceil((next - now.getTime()) / 1000));
}

export function hex(bytes: ArrayBuffer | Uint8Array): string {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

export function randomHex(nBytes: number): string {
  return hex(crypto.getRandomValues(new Uint8Array(nBytes)));
}

export async function sha256Hex(s: string): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", encoder.encode(s)));
}

export function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const x of bytes) s += String.fromCharCode(x);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + pad);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

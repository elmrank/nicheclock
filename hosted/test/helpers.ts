import { env } from "cloudflare:workers";
import worker from "../src/index";
import type { Env, Scope } from "../src/env";
import { createKey } from "../src/keys";

let n = 0;

export const BASE = "https://nicheclock.test";

export async function mkKey(scope: Scope = "external", extra: { email?: string; githubId?: number | null } = {}) {
  n += 1;
  const email = extra.email ?? `user${n}-${Math.random().toString(36).slice(2, 8)}@example.org`;
  const k = await createKey(env as unknown as Env, { githubId: extra.githubId === undefined ? 1000 + n + Math.floor(Math.random() * 1e6) : extra.githubId, email, scope });
  return { ...k, email };
}

export interface CallOpts {
  key?: string;
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  env?: Partial<Env>;
}

export async function call(path: string, o: CallOpts = {}): Promise<Response> {
  const headers: Record<string, string> = { ...(o.headers ?? {}) };
  if (o.key) headers.authorization = `Bearer ${o.key}`;
  let body: string | undefined;
  if (o.body !== undefined) {
    body = typeof o.body === "string" ? o.body : JSON.stringify(o.body);
    headers["content-type"] ??= "application/json";
  }
  const e = { ...(env as unknown as Env), ...(o.env ?? {}) } as Env;
  return worker.fetch(new Request(BASE + path, { method: o.method ?? (body ? "POST" : "GET"), headers, body: ["GET", "HEAD"].includes(o.method ?? "") ? undefined : body }), e);
}

export async function json<T = any>(r: Response): Promise<T> {
  return (await r.json()) as T;
}

export const db = () => (env as unknown as Env).DB;

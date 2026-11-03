/**
 * Reader of the build-time shards (nicheclock/catalog/shard.py): the niche pool, the concept index and the events cut into files of at most
 * 150 KB, so a profile tool parses only the files that hold the units it asks for (10 ms of CPU on the free plan; a whole pool is 5 MB).
 *
 * A unit key goes to shard fnv1a(key) % n. A unit cut into chunks (`key~0`, `key~1`, ...) is listed in the manifest and read whole.
 * Parsed shards are kept in the isolate (at most MAX_CACHED of them), so a warm isolate answers a repeated topic without parsing again.
 */
import type { Env } from "./env";
import { encoder } from "./http";

export const SHARD_DIR = "/catalog-data/shards/";
const MAX_CACHED = 24;

export type PoolRow = [qid: number, title: string, views: number, ratio: number, handles: number[], flags: number, extra?: unknown[]];
export interface Unit {
  r?: PoolRow[];
  l?: Record<string, string>;
  c?: unknown;
}
interface ShardFile {
  u: Record<string, Unit>;
}
export interface Manifest {
  shards: number;
  chunks?: Record<string, number>;
  [k: string]: unknown;
}

/** 32-bit FNV-1a over the UTF-8 bytes; the same number as shard.py fnv1a */
export function fnv1a(s: string): number {
  let h = 0x811c9dc5;
  for (const b of encoder.encode(s)) h = Math.imul(h ^ b, 0x01000193);
  return h >>> 0;
}

const files = new Map<string, Promise<unknown | null>>();

/** forget every parsed shard and manifest (tests; a new deploy starts new isolates anyway) */
export function clearShardCache(): void {
  files.clear();
}

export async function getJson<T>(env: Env, path: string): Promise<T | null> {
  let p = files.get(path) as Promise<T | null> | undefined;
  if (!p) {
    p = (async () => {
      const res = await env.ASSETS.fetch(new Request(`https://assets.invalid${path}`));
      if (res.status !== 200) {
        await res.body?.cancel().catch(() => undefined);
        return null;
      }
      return (await res.json()) as T;
    })();
    files.set(path, p);
    if (files.size > MAX_CACHED + 8) {
      const oldest = files.keys().next().value;
      if (oldest !== undefined) files.delete(oldest);
    }
    p.then((v) => {
      if (v === null) files.delete(path);
    }, () => files.delete(path));
  }
  return p;
}

export class Shards {
  private man: Promise<Manifest | null> | null = null;
  constructor(
    private readonly env: Env,
    readonly name: string,
  ) {}

  manifest(): Promise<Manifest | null> {
    this.man ??= getJson<Manifest>(this.env, `${SHARD_DIR}${this.name}.json`);
    return this.man;
  }

  private async one(man: Manifest, key: string): Promise<Unit | null> {
    const idx = fnv1a(key) % man.shards;
    const file = await getJson<ShardFile>(this.env, `${SHARD_DIR}${this.name}-${String(idx).padStart(3, "0")}.json`);
    return file?.u[key] ?? null;
  }

  /** the units behind these keys (chunks joined); a key with no unit is absent from the map */
  async units(keys: Iterable<string>): Promise<Map<string, Unit>> {
    const man = await this.manifest();
    const out = new Map<string, Unit>();
    if (!man) return out;
    await Promise.all(
      [...new Set(keys)].map(async (key) => {
        const n = man.chunks?.[key] ?? 1;
        if (n === 1) {
          const u = await this.one(man, key);
          if (u) out.set(key, u);
          return;
        }
        const parts = await Promise.all(Array.from({ length: n }, (_v, i) => this.one(man, `${key}~${i}`)));
        const joined: Unit = { r: [], l: {} };
        for (const p of parts) {
          if (!p) continue;
          joined.r?.push(...(p.r ?? []));
          Object.assign(joined.l ?? {}, p.l ?? {});
        }
        out.set(key, joined);
      }),
    );
    return out;
  }
}

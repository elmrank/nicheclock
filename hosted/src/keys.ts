/** API keys: generation, SHA-256 storage, lookup, creation limits, revocation. */
import type { Env, KeyRow, Scope } from "./env";
import { intVar } from "./env";
import { ApiError, b64url, isoNow, randomHex, sha256Hex } from "./http";

export const KEY_PREFIX = "nk_";
const KEY_RE = /^nk_[A-Za-z0-9_-]{32,64}$/;

/** "nk_" + 32 random bytes as base64url (43 chars). */
export function generateKey(): string {
  return KEY_PREFIX + b64url(crypto.getRandomValues(new Uint8Array(32)));
}

export function bearerToken(req: Request): string | null {
  const h = req.headers.get("authorization");
  if (!h) return null;
  const m = /^Bearer\s+(\S+)$/i.exec(h);
  return m ? (m[1] as string) : null;
}

export interface Authed extends KeyRow {
  used_today: number;
}

/**
 * Resolve a Bearer key to its row and today's request count (one D1 read). Revoked keys never match.
 * Throws 401 for a missing / malformed / unknown / revoked key.
 */
export async function authenticate(env: Env, req: Request, day: string, now: Date): Promise<Authed> {
  const token = bearerToken(req);
  if (!token || !KEY_RE.test(token)) {
    throw new ApiError(401, "unauthorized", "send Authorization: Bearer nk_...", {
      "www-authenticate": 'Bearer realm="nicheclock"',
    });
  }
  const hash = await sha256Hex(token);
  const row = await env.DB.prepare(
    `SELECT k.id, k.owner_github_id, k.email, k.scope, k.plan, k.created_at, k.last_used_at,
            (SELECT COALESCE(SUM(u.requests), 0) FROM usage_daily u WHERE u.key_id = k.id AND u.day = ?1) AS used_today
       FROM keys k WHERE k.hash = ?2 AND k.revoked_at IS NULL`,
  )
    .bind(day, hash)
    .first<Authed>();
  if (!row) {
    throw new ApiError(401, "unauthorized", "unknown or revoked key", { "www-authenticate": 'Bearer realm="nicheclock"' });
  }
  // last_used_at is a coarse signal: refresh at most hourly to save D1 writes.
  const last = row.last_used_at ? Date.parse(row.last_used_at) : 0;
  if (now.getTime() - last > 3_600_000) {
    await env.DB.prepare("UPDATE keys SET last_used_at = ?1 WHERE id = ?2").bind(isoNow(now), row.id).run();
  }
  return row;
}

export interface NewKey {
  id: string;
  key: string; // shown once
  scope: Scope;
  created_at: string;
}

/**
 * Create a key. The active-key limit is enforced inside the INSERT so two parallel requests cannot
 * both slip under it. Limit counts active keys sharing the email OR the GitHub id.
 */
export async function createKey(
  env: Env,
  o: { githubId: number | null; email: string; scope?: Scope; plan?: string; now?: Date },
): Promise<NewKey> {
  const email = o.email.trim().toLowerCase();
  const scope = o.scope ?? "external";
  const max = intVar(env.MAX_KEYS_PER_EMAIL, 2);
  const key = generateKey();
  const id = `k_${randomHex(8)}`;
  const created_at = isoNow(o.now);
  const res = await env.DB.prepare(
    `INSERT INTO keys (id, owner_github_id, email, hash, scope, plan, created_at)
     SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7
      WHERE (SELECT COUNT(*) FROM keys WHERE revoked_at IS NULL AND (email = ?3 OR owner_github_id = ?2)) < ?8`,
  )
    .bind(id, o.githubId, email, await sha256Hex(key), scope, o.plan ?? "free", created_at, max)
    .run();
  if (!res.meta.changes) {
    throw new ApiError(409, "key_limit", `at most ${max} active keys per email; revoke one first`);
  }
  return { id, key, scope, created_at };
}

export interface KeyInfo {
  id: string;
  scope: Scope;
  plan: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/** Keys owned by the same GitHub account; an operator-minted key (no owner) only sees itself. */
export async function listOwnKeys(env: Env, key: KeyRow): Promise<KeyInfo[]> {
  const stmt =
    key.owner_github_id === null
      ? env.DB.prepare("SELECT id, scope, plan, created_at, last_used_at, revoked_at FROM keys WHERE id = ?1").bind(key.id)
      : env.DB.prepare(
          "SELECT id, scope, plan, created_at, last_used_at, revoked_at FROM keys WHERE owner_github_id = ?1 ORDER BY created_at",
        ).bind(key.owner_github_id);
  return (await stmt.all<KeyInfo>()).results;
}

export async function listKeysForGithubId(env: Env, githubId: number): Promise<KeyInfo[]> {
  return (
    await env.DB.prepare(
      "SELECT id, scope, plan, created_at, last_used_at, revoked_at FROM keys WHERE owner_github_id = ?1 ORDER BY created_at",
    )
      .bind(githubId)
      .all<KeyInfo>()
  ).results;
}

/** Revoke one key owned by `githubId`. false = not found / not yours (no existence leak). Idempotent. */
export async function revokeOwnedByGithub(env: Env, githubId: number, keyId: string, now = new Date()): Promise<boolean> {
  const own = await env.DB.prepare("SELECT id FROM keys WHERE id = ?1 AND owner_github_id = ?2").bind(keyId, githubId).first();
  if (!own) return false;
  await env.DB.prepare("UPDATE keys SET revoked_at = ?1 WHERE id = ?2 AND revoked_at IS NULL").bind(isoNow(now), keyId).run();
  return true;
}

/** Revoke `keyId` if it is visible to `caller` (same owner, or the caller itself). */
export async function revokeVisible(env: Env, caller: KeyRow, keyId: string, now = new Date()): Promise<string | null> {
  const owned = (await listOwnKeys(env, caller)).find((k) => k.id === keyId);
  if (!owned) return null;
  const at = owned.revoked_at ?? isoNow(now);
  if (!owned.revoked_at) {
    await env.DB.prepare("UPDATE keys SET revoked_at = ?1 WHERE id = ?2 AND revoked_at IS NULL").bind(at, keyId).run();
  }
  return at;
}

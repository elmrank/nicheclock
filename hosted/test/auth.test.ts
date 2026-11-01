import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";
import { sha256Hex } from "../src/http";
import { createKey } from "../src/keys";
import type { Env } from "../src/env";
import { call, db, json, mkKey } from "./helpers";
import { seedCatalog } from "./fixtures";

describe("auth", () => {
  it("rejects missing, malformed and unknown keys with 401 + WWW-Authenticate", async () => {
    for (const key of [undefined, "nk_short", "Bearer-less", `nk_${"a".repeat(43)}`]) {
      const r = await call("/v1/niches", { key });
      expect(r.status).toBe(401);
      expect(r.headers.get("www-authenticate")).toContain("Bearer");
      expect((await json(r)).error.code).toBe("unauthorized");
    }
  });

  it("accepts a valid key and stores only its SHA-256", async () => {
    await seedCatalog();
    const k = await mkKey();
    const r = await call("/v1/niches", { key: k.key });
    expect(r.status).toBe(200);
    const row = await db().prepare("SELECT * FROM keys WHERE id = ?").bind(k.id).first<Record<string, unknown>>();
    expect(row?.hash).toBe(await sha256Hex(k.key));
    expect(JSON.stringify(row)).not.toContain(k.key);
    expect(k.key).toMatch(/^nk_[A-Za-z0-9_-]{43}$/);
    expect(row?.plan).toBe("free");
    expect(row?.scope).toBe("external");
    expect(row?.last_used_at).toBeTruthy(); // set on first use
  });

  it("revoking a key (DELETE /v1/keys/{id}) makes it fail immediately", async () => {
    const k = await mkKey();
    expect((await call("/v1/niches", { key: k.key })).status).toBe(200);
    const del = await call(`/v1/keys/${k.id}`, { key: k.key, method: "DELETE" });
    expect(del.status).toBe(200);
    expect((await json(del)).revoked_at).toBeTruthy();
    const after = await call("/v1/niches", { key: k.key });
    expect(after.status).toBe(401);
    expect((await json(after)).error.message).toContain("revoked");
  });

  it("lists only own keys and cannot revoke someone else's", async () => {
    const gid = 777_000 + Math.floor(Math.random() * 1000);
    const a1 = await mkKey("external", { githubId: gid, email: `a${gid}@example.org` });
    const a2 = await createKey(env as unknown as Env, { githubId: gid, email: `a${gid}@example.org` });
    const other = await mkKey();
    const list = await json(await call("/v1/keys", { key: a1.key }));
    expect(list.keys.map((k: { id: string }) => k.id).sort()).toEqual([a1.id, a2.id].sort());
    expect(list.keys.find((k: { id: string }) => k.id === a1.id).current).toBe(true);
    expect(JSON.stringify(list)).not.toContain("hash");
    expect((await call(`/v1/keys/${other.id}`, { key: a1.key, method: "DELETE" })).status).toBe(404);
    expect((await call("/v1/niches", { key: other.key })).status).toBe(200); // untouched
    // owner can revoke the sibling key
    expect((await call(`/v1/keys/${a2.id}`, { key: a1.key, method: "DELETE" })).status).toBe(200);
    expect((await call("/v1/niches", { key: a2.key })).status).toBe(401);
  });

  it("allows at most 2 active keys per email, and a third after a revoke", async () => {
    const email = `limit${Math.random().toString(36).slice(2)}@Example.org`;
    const k1 = await createKey(env as unknown as Env, { githubId: null, email });
    await createKey(env as unknown as Env, { githubId: null, email: email.toLowerCase() });
    await expect(createKey(env as unknown as Env, { githubId: null, email })).rejects.toMatchObject({ status: 409, code: "key_limit" });
    await call(`/v1/keys/${k1.id}`, { key: k1.key, method: "DELETE" });
    await expect(createKey(env as unknown as Env, { githubId: null, email })).resolves.toBeTruthy();
  });

  it("also limits by GitHub account when the email changes", async () => {
    const gid = 880_000 + Math.floor(Math.random() * 1000);
    await createKey(env as unknown as Env, { githubId: gid, email: `x1-${gid}@example.org` });
    await createKey(env as unknown as Env, { githubId: gid, email: `x2-${gid}@example.org` });
    await expect(createKey(env as unknown as Env, { githubId: gid, email: `x3-${gid}@example.org` })).rejects.toMatchObject({ code: "key_limit" });
  });
});

import { describe, expect, it } from "vitest";
import { dayOf } from "../src/http";
import { call, db, json, mkKey } from "./helpers";
import { seedCatalog } from "./fixtures";

const today = () => dayOf(new Date());

describe("quota + metering", () => {
  it("returns 429 + Retry-After after MINUTE_LIMIT requests in a minute", async () => {
    await seedCatalog();
    const k = await mkKey();
    const env = { MINUTE_LIMIT: "3" };
    for (let i = 0; i < 3; i++) expect((await call("/v1/niches", { key: k.key, env })).status).toBe(200);
    const r = await call("/v1/niches", { key: k.key, env });
    expect(r.status).toBe(429);
    const ra = Number(r.headers.get("retry-after"));
    expect(ra).toBeGreaterThanOrEqual(1);
    expect(ra).toBeLessThanOrEqual(60);
    expect((await json(r)).error.code).toBe("rate_limited");
    // only counted requests are metered: the refused one is not in usage_daily
    const u = await db().prepare("SELECT SUM(requests) AS n FROM usage_daily WHERE key_id = ?").bind(k.id).first<{ n: number }>();
    expect(u?.n).toBe(3);
    // another key is unaffected
    const k2 = await mkKey();
    expect((await call("/v1/niches", { key: k2.key, env })).status).toBe(200);
  });

  it("returns 429 + Retry-After (to 00:00 UTC) when the daily quota is used up, without writing a counter", async () => {
    const k = await mkKey();
    await db().prepare("INSERT INTO usage_daily (key_id, day, endpoint, requests, bytes) VALUES (?, ?, 'GET /v1/niches', 1000, 10)").bind(k.id, today()).run();
    const r = await call("/v1/niches", { key: k.key });
    expect(r.status).toBe(429);
    expect((await json(r)).error.code).toBe("daily_quota_exceeded");
    const ra = Number(r.headers.get("retry-after"));
    expect(ra).toBeGreaterThanOrEqual(1);
    expect(ra).toBeLessThanOrEqual(86_400);
    const rm = await db().prepare("SELECT COUNT(*) AS n FROM rate_minute WHERE key_id = ?").bind(k.id).first<{ n: number }>();
    expect(rm?.n).toBe(0);
  });

  it("counts the daily quota across endpoints and honours DAILY_LIMIT", async () => {
    await seedCatalog();
    const k = await mkKey();
    const env = { DAILY_LIMIT: "3" };
    expect((await call("/v1/niches", { key: k.key, env })).status).toBe(200);
    expect((await call("/v1/niches/birdwatching/trends", { key: k.key, env })).status).toBe(200);
    expect((await call("/v1/keys", { key: k.key, env })).status).toBe(200);
    expect((await call("/v1/niches", { key: k.key, env })).status).toBe(429);
  });

  it("meters requests and bytes per endpoint template and reports the remaining quota", async () => {
    await seedCatalog();
    const k = await mkKey();
    const r1 = await call("/v1/niches/birdwatching/trends?days=7", { key: k.key });
    const body = await r1.text();
    expect(r1.headers.get("x-ratelimit-remaining-day")).toBe("999");
    await call("/v1/niches/birdwatching/trends", { key: k.key });
    const rows = (await db().prepare("SELECT endpoint, requests, bytes FROM usage_daily WHERE key_id = ? AND day = ?").bind(k.id, today()).all<{ endpoint: string; requests: number; bytes: number }>()).results;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ endpoint: "GET /v1/niches/:id/trends", requests: 2 });
    expect(rows[0]?.bytes).toBeGreaterThanOrEqual(new TextEncoder().encode(body).length * 2 - 50);
  });

  it("meters error responses too (404 on an unknown niche still costs a request)", async () => {
    const k = await mkKey();
    const r = await call("/v1/niches/nope/trends", { key: k.key });
    expect(r.status).toBe(404);
    const u = await db().prepare("SELECT SUM(requests) AS n FROM usage_daily WHERE key_id = ?").bind(k.id).first<{ n: number }>();
    expect(u?.n).toBe(1);
  });
});

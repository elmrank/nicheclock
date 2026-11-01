import { describe, expect, it } from "vitest";
import { buildReport, COST_MODEL, FREE_LIMITS, r2ClassBFor } from "../src/admin";
import { dayOf } from "../src/http";
import { call, db, json, mkKey } from "./helpers";
import { seedCatalog } from "./fixtures";

describe("GET /v1/admin/report", () => {
  it("is admin only", async () => {
    const ext = await mkKey("external");
    const int = await mkKey("internal");
    expect((await call("/v1/admin/report", { key: ext.key })).status).toBe(403);
    expect((await call("/v1/admin/report", { key: int.key })).status).toBe(403);
    expect((await call("/v1/admin/report")).status).toBe(401);
  });

  it("reports keys, requests per endpoint, jobs and estimated free-tier usage", async () => {
    await seedCatalog();
    const admin = await mkKey("admin");
    const k = await mkKey("external");
    await call("/v1/niches", { key: k.key });
    await call("/v1/niches/birdwatching/trends", { key: k.key });
    await call("/v1/niches/birdwatching/trends", { key: k.key });
    await call("/v1/jobs", { key: k.key, body: { url: "https://report-site.com" } });
    const res = await call("/v1/admin/report", { key: admin.key });
    expect(res.status).toBe(200);
    const r = await json(res);
    expect(r.keys.created_7d).toBeGreaterThanOrEqual(2);
    expect(r.keys.active).toBeGreaterThanOrEqual(2);
    expect(r.keys.active_7d).toBeGreaterThanOrEqual(1);
    expect(r.keys.active_by_scope.admin).toBeGreaterThanOrEqual(1);
    const per = Object.fromEntries(r.requests.per_endpoint_7d.map((e: { endpoint: string; requests: number }) => [e.endpoint, e.requests]));
    expect(per["GET /v1/niches"]).toBeGreaterThanOrEqual(1);
    expect(per["GET /v1/niches/:id/trends"]).toBeGreaterThanOrEqual(2);
    expect(per["POST /v1/jobs"]).toBeGreaterThanOrEqual(1);
    expect(r.requests.today).toBeGreaterThanOrEqual(4);
    expect(r.jobs.total).toBeGreaterThanOrEqual(1);
    expect(r.jobs.by_status.queued).toBeGreaterThanOrEqual(1);
    const ft = r.free_tier_estimate;
    expect(ft.workers_requests_per_day.limit).toBe(100_000);
    expect(ft.d1_rows_written_per_day.limit).toBe(100_000);
    expect(ft.sources.workers).toContain("developers.cloudflare.com");
    expect(ft.threshold_pct).toBe(70);
  });

  it("computes the percentages from the documented cost model", async () => {
    const day = dayOf(new Date());
    // 70,000 requests on one day spread over 7 days => 10,000/day average.
    await db().prepare("DELETE FROM usage_daily").run();
    await db().prepare("INSERT INTO usage_daily (key_id, day, endpoint, requests, bytes) VALUES ('k_model', ?, 'GET /v1/niches/:id/trends', 70000, 1)").bind(day).run();
    const r = await buildReport({ DB: db() } as never);
    const ft = r.free_tier_estimate;
    expect(ft.workers_requests_per_day).toMatchObject({ estimated_used: 10_000, pct: 10, over_threshold: false });
    expect(ft.d1_rows_written_per_day.estimated_used).toBe(10_000 * COST_MODEL.d1_writes_per_request);
    expect(ft.d1_rows_written_per_day.pct).toBe(20);
    expect(ft.d1_rows_read_per_day.estimated_used).toBe(10_000 * COST_MODEL.d1_reads_per_request);
    // 1 class B read per trends request: 10,000/day * 30 = 300,000 of 10,000,000/month
    expect(ft.r2_class_b_per_month).toMatchObject({ estimated_used: 300_000, pct: 3 });
    expect(FREE_LIMITS.d1_rows_written_per_day).toBe(100_000);
    // over the 70% line
    await db().prepare("UPDATE usage_daily SET requests = 500000 WHERE key_id = 'k_model'").run();
    const over = await buildReport({ DB: db() } as never);
    expect(over.free_tier_estimate.workers_requests_per_day.over_threshold).toBe(true); // 71,428/day > 70k
    expect(r2ClassBFor("GET /v1/niches")).toBe(2);
    expect(r2ClassBFor("POST /v1/jobs")).toBe(0);
  });
});

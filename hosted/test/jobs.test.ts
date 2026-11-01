import { describe, expect, it } from "vitest";
import { parseTarget } from "../src/jobs";
import { call, db, json, mkKey } from "./helpers";
import { put, seedCatalog, trendsDoc } from "./fixtures";

describe("custom niche jobs", () => {
  it("POST /v1/jobs queues a job (202) and GET /v1/jobs/{id} returns it to its owner only", async () => {
    const k = await mkKey();
    const r = await call("/v1/jobs", { key: k.key, body: { url: "https://mysite.example.com/path?q=1#frag" } });
    expect(r.status).toBe(202);
    const { job, created } = await json(r);
    expect(created).toBe(true);
    expect(job).toMatchObject({ status: "queued", niche_id: "custom-mysite-example-com", url: "https://mysite.example.com/path?q=1", error: null });
    expect(job.key_id).toBeUndefined();
    expect(r.headers.get("location")).toBe(`/v1/jobs/${job.id}`);
    const got = await json(await call(`/v1/jobs/${job.id}`, { key: k.key }));
    expect(got.job.id).toBe(job.id);
    const other = await mkKey();
    expect((await call(`/v1/jobs/${job.id}`, { key: other.key })).status).toBe(404);
    expect((await call("/v1/jobs/j_nonexistent", { key: k.key })).status).toBe(404);
  });

  it("is idempotent per site, and caps custom niches at 3 per key", async () => {
    const k = await mkKey();
    const post = (url: string) => call("/v1/jobs", { key: k.key, body: { url } });
    const a = await json(await post("https://a-site.com"));
    const again = await post("https://www.a-site.com/other");
    expect(again.status).toBe(200);
    expect((await json(again)).job.id).toBe(a.job.id);
    expect((await post("https://b-site.com")).status).toBe(202);
    expect((await post("https://c-site.com")).status).toBe(202);
    const fourth = await post("https://d-site.com");
    expect(fourth.status).toBe(403);
    expect((await json(fourth)).error.code).toBe("custom_niche_limit");
    // another key has its own 3
    const k2 = await mkKey();
    expect((await call("/v1/jobs", { key: k2.key, body: { url: "https://d-site.com" } })).status).toBe(202);
  });

  it("refuses URLs that are not a public site", async () => {
    const k = await mkKey();
    for (const url of ["http://localhost/", "http://127.0.0.1/", "http://10.0.0.5/", "http://[::1]/", "ftp://example.com/", "https://user:pw@example.com/", "https://intranet/", "https://printer.local/", "https://example.com:8080/", "", 42, undefined]) {
      const r = await call("/v1/jobs", { key: k.key, body: { url } });
      expect(r.status, JSON.stringify(url)).toBe(400);
      expect((await json(r)).error.code).toBe("invalid_url");
    }
    expect((await call("/v1/jobs", { key: k.key, body: "not json", headers: { "content-type": "application/json" } })).status).toBe(400);
  });

  it("caps new jobs per UTC day for the whole service (429 + Retry-After)", async () => {
    const k = await mkKey();
    const n = (await db().prepare("SELECT COUNT(*) AS n FROM jobs").first<{ n: number }>())?.n ?? 0;
    const r = await call("/v1/jobs", { key: k.key, body: { url: "https://cap-two.com" }, env: { JOBS_DAILY_CAP: String(Math.max(1, n)) } });
    expect(r.status).toBe(429);
    expect(Number(r.headers.get("retry-after"))).toBeGreaterThan(0);
    expect((await json(r)).error.code).toBe("jobs_capacity");
  });

  it("operator loop: admin lists queued jobs, marks them running/done; the result becomes readable", async () => {
    await seedCatalog();
    const admin = await mkKey("admin");
    const k = await mkKey();
    const { job } = await json(await call("/v1/jobs", { key: k.key, body: { url: "https://loop-site.org" } }));
    expect((await call("/v1/admin/jobs", { key: k.key })).status).toBe(403);
    const queued = await json(await call("/v1/admin/jobs?status=queued", { key: admin.key }));
    const mine = queued.jobs.find((j: { id: string }) => j.id === job.id);
    expect(mine.key_id).toBe(k.id);
    expect((await json(await call(`/v1/admin/jobs/${job.id}`, { key: admin.key, body: { status: "running" } }))).job.status).toBe("running");
    // R2 gets the custom niche from the exporter, then the job is closed
    await put(`custom/${k.id}/niches.json`, { schema: 1, niches: [{ id: job.niche_id, name: "loop-site.org", sources: ["hn"], updated_at: new Date().toISOString() }] });
    await put(`custom/${k.id}/${job.niche_id}/trends.json`, trendsDoc(job.niche_id));
    const done = await json(await call(`/v1/admin/jobs/${job.id}`, { key: admin.key, body: { status: "done" } }));
    expect(done.job.finished_at).toBeTruthy();
    const view = await json(await call(`/v1/jobs/${job.id}`, { key: k.key }));
    expect(view.job).toMatchObject({ status: "done", niche_path: `/v1/niches/${job.niche_id}/trends` });
    expect((await call(`/v1/niches/${job.niche_id}/trends`, { key: k.key })).status).toBe(200);
    // done is final; bad status is rejected
    expect((await call(`/v1/admin/jobs/${job.id}`, { key: admin.key, body: { status: "failed" } })).status).toBe(409);
    expect((await call(`/v1/admin/jobs/${job.id}`, { key: admin.key, body: { status: "bogus" } })).status).toBe(400);
  });

  it("a failed job frees the niche slot and can be retried", async () => {
    const admin = await mkKey("admin");
    const k = await mkKey();
    const { job } = await json(await call("/v1/jobs", { key: k.key, body: { url: "https://flaky-site.net" } }));
    await call(`/v1/admin/jobs/${job.id}`, { key: admin.key, body: { status: "failed", error: "robots.txt disallows" } });
    expect((await json(await call(`/v1/jobs/${job.id}`, { key: k.key }))).job).toMatchObject({ status: "failed", error: "robots.txt disallows" });
    const retry = await call("/v1/jobs", { key: k.key, body: { url: "https://flaky-site.net" } });
    expect(retry.status).toBe(202);
    expect((await json(retry)).job.id).not.toBe(job.id);
  });

  it("parseTarget derives the niche id", () => {
    expect(parseTarget("example.com").niche_id).toBe("custom-example-com");
    expect(parseTarget("https://WWW.Sub.Example.co.uk/x").niche_id).toBe("custom-sub-example-co-uk");
  });
});

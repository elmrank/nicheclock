import { beforeEach, describe, expect, it } from "vitest";
import { resetBeaconCap, parseBeacon } from "../src/beacon";
import { call, db } from "./helpers";

const ORIGIN = "https://nicheclock.elmrank.com";
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36";

function send(body: unknown, o: { origin?: string | null; ua?: string | null; method?: string } = {}) {
  const headers: Record<string, string> = { "content-type": "text/plain" };
  if (o.origin !== null) headers.origin = o.origin ?? ORIGIN;
  if (o.ua !== null) headers["user-agent"] = o.ua ?? UA;
  return call("/b", { method: o.method ?? "POST", body: typeof body === "string" ? body : JSON.stringify(body), headers, env: { PUBLIC_BASE_URL: ORIGIN } });
}

const view = { path: "/docs/api/", seconds: 42, scroll: 50, device: "desktop", source: "search", clicks: 0 };
const rows = () => db().prepare("SELECT * FROM pageviews_daily ORDER BY path").all<Record<string, any>>().then((r) => r.results);

describe("POST /b", () => {
  beforeEach(async () => {
    await db().prepare("DELETE FROM pageviews_daily").run();
    resetBeaconCap();
  });

  it("counts one view into daily totals and answers 204 with no body", async () => {
    const r = await send(view);
    expect(r.status).toBe(204);
    expect(r.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    const [row] = await rows();
    expect(row).toMatchObject({ host: "nicheclock.elmrank.com", path: "/docs/api/", device: "desktop", source: "search", views: 1, seconds_total: 42, scroll_50: 1, scroll_0: 0, views_with_internal_click: 0 });
  });

  it("adds a second view to the same row", async () => {
    await send(view);
    await send({ ...view, seconds: 8, scroll: 100, clicks: 1 });
    const [row] = await rows();
    expect(row).toMatchObject({ views: 2, seconds_total: 50, scroll_50: 1, scroll_100: 1, views_with_internal_click: 1 });
  });

  it("moves a returning view to its new buckets instead of counting it twice", async () => {
    await send({ ...view, seconds: 10, scroll: 25 });
    await send({ ...view, seconds: 70, scroll: 90, clicks: 1, previous: { seconds: 10, scroll: 25, clicks: 0 } });
    const rs = await rows();
    expect(rs).toHaveLength(1);
    expect(rs[0]).toMatchObject({ views: 1, seconds_total: 70, scroll_25: 0, scroll_90: 1, views_with_internal_click: 1 });
  });

  it("never drives a counter below zero with a forged previous", async () => {
    await send({ ...view, seconds: 5, scroll: 0 });
    await send({ ...view, seconds: 5, scroll: 100, previous: { seconds: 5, scroll: 75, clicks: 0 } });
    const [row] = await rows();
    expect(row).toMatchObject({ scroll_75: 0, views: 1 });
  });

  it("drops a previous that is larger than the new values", async () => {
    await send({ ...view, previous: { seconds: 999, scroll: 50, clicks: 0 } });
    expect(await rows()).toHaveLength(0);
  });

  it("stores local dev origins under host local", async () => {
    await send(view, { origin: "http://localhost:8787" });
    expect((await rows())[0]).toMatchObject({ host: "local" });
  });

  it("falls back to Sec-Fetch-Site: same-origin when a browser sends no Origin", async () => {
    const r = await call("/b", { method: "POST", body: JSON.stringify(view), headers: { "user-agent": UA, "sec-fetch-site": "same-origin" }, env: { PUBLIC_BASE_URL: "https://nicheclock.test" } });
    expect(r.status).toBe(204);
    expect((await rows())[0]).toMatchObject({ host: "nicheclock.test" });
    const cross = await call("/b", { method: "POST", body: JSON.stringify(view), headers: { "user-agent": UA, "sec-fetch-site": "cross-site" } });
    expect(cross.status).toBe(403);
  });

  it("refuses a foreign or missing Origin with 403 and stores nothing", async () => {
    expect((await send(view, { origin: "https://evil.example" })).status).toBe(403);
    expect((await send(view, { origin: null })).status).toBe(403);
    expect(await rows()).toHaveLength(0);
  });

  it("answers 204 and stores nothing for bots and empty user agents", async () => {
    expect((await send(view, { ua: "Mozilla/5.0 (compatible; Googlebot/2.1)" })).status).toBe(204);
    expect((await send(view, { ua: "HeadlessChrome/130" })).status).toBe(204);
    expect((await send(view, { ua: null })).status).toBe(204);
    expect(await rows()).toHaveLength(0);
  });

  it("rejects bad bodies with 400", async () => {
    for (const bad of [
      "not json",
      { ...view, path: "/x?y=1" },
      { ...view, path: "docs" },
      { ...view, device: "tablet" },
      { ...view, source: "https://example.com/" },
      { ...view, scroll: 33 },
      { ...view, seconds: -1 },
      { ...view, extra: 1 },
      { ...view, previous: { seconds: 1, scroll: 0, clicks: 0, cta: 1 } },
      "x".repeat(600),
    ]) {
      expect((await send(bad)).status).toBe(400);
    }
    expect(await rows()).toHaveLength(0);
  });

  it("clamps seconds to 30 minutes and rejects non-POST", async () => {
    await send({ ...view, seconds: 999999 });
    expect((await rows())[0]).toMatchObject({ seconds_total: 1800 });
    expect((await send(view, { method: "GET" })).status).toBe(405);
  });

  it("stops adding new paths after 300 a day", async () => {
    for (let i = 0; i < 300; i++) await db().prepare("INSERT INTO pageviews_daily (day, host, path, device, source, views) VALUES (?, 'nicheclock.elmrank.com', ?, 'desktop', 'direct', 1)").bind(new Date().toISOString().slice(0, 10), `/p${i}/`).run();
    await send({ ...view, path: "/one-more/" });
    const n = await db().prepare("SELECT COUNT(*) AS n FROM pageviews_daily WHERE path = '/one-more/'").first<{ n: number }>();
    expect(n?.n).toBe(0);
  });

  it("parseBeacon ignores nothing it does not know: unknown fields fail", () => {
    expect(parseBeacon(JSON.stringify(view))).toMatchObject({ path: "/docs/api/", seconds: 42 });
    expect(parseBeacon(JSON.stringify({ ...view, ip: "1.2.3.4" }))).toBeNull();
  });

  it("is not part of the metered API: no key needed, no usage rows", async () => {
    await send(view);
    const u = await db().prepare("SELECT COUNT(*) AS n FROM usage_daily").first<{ n: number }>();
    expect(u?.n).toBe(0);
  });
});

import { describe, expect, it } from "vitest";
import { call, json, mkKey } from "./helpers";
import { put, seedCatalog, trendsDoc } from "./fixtures";
import { getGithubRising, safeWhy } from "../src/catalog";
import { allowedSources } from "../src/sources";

const titles = (r: { items: { title: string }[] }) => r.items.map((i) => i.title);

describe("REST data + allow-list at serving time", () => {
  it("lists niches, dropping ones built only from sources we do not serve", async () => {
    await seedCatalog();
    const ext = await mkKey("external");
    const r = await json(await call("/v1/niches", { key: ext.key }));
    expect(r.niches.map((n: { id: string }) => n.id)).toEqual(["birdwatching"]);
    expect(r.niches[0].sources).toEqual(["gdelt", "hn", "wikimedia_pageviews"]); // apple filtered out for external
    expect(r.niches[0].ignored).toBeUndefined();
    const internal = await json(await call("/v1/niches", { key: (await mkKey("internal")).key }));
    expect(internal.niches[0].sources).toEqual(expect.arrayContaining(["apple"]));
    expect(internal.niches.map((n: { id: string }) => n.id)).toEqual(["birdwatching", "github-only"]); // internal sources count
  });

  it("serves only allowed sources to an external key and drops anything without provenance", async () => {
    await seedCatalog();
    const k = await mkKey("external");
    const r = await json(await call("/v1/niches/birdwatching/trends?days=7", { key: k.key }));
    expect(titles(r)).toEqual(["Merlin Bird ID sequel rumors", "Bird watching roguelike surge", "Bird feeder news volume"]);
    for (const it of r.items) {
      expect(["hn", "wikimedia_pageviews", "gdelt"]).toContain(it.source);
      expect(it.source_url).toMatch(/^https?:\/\//);
      expect(it.license || it.attribution).toBeTruthy();
      expect(it.sources.every((s: string) => ["hn", "wikimedia_pageviews", "gdelt"].includes(s))).toBe(true);
    }
    const first = r.items[0];
    expect(first.sources).toEqual(["hn"]); // apple + google_trends stripped from the list
    expect(first.why).toBe(""); // mentioned google_trends -> blanked
    expect(first.secret_field).toBeUndefined();
    expect(JSON.stringify(r)).not.toMatch(/google_trends|apple|LEAK/i);
  });

  it("lets internal keys also see nager, google_trends, apple; reddit stays out for everyone", async () => {
    await seedCatalog();
    const k = await mkKey("internal");
    const r = await json(await call("/v1/niches/birdwatching/trends", { key: k.key }));
    expect(titles(r)).toEqual(expect.arrayContaining(["App sale", "Apple-only item", "Google Trends term", "Hybrid topic scored with internal signals"]));
    expect(titles(r)).not.toContain("Reddit thread");
    const first = r.items.find((i: { title: string }) => i.title === "Merlin Bird ID sequel rumors");
    expect(first.sources).toEqual(["apple", "google_trends", "hn"]);
    expect(first.why).toContain("google_trends"); // internal callers may read it
    const up = await json(await call("/v1/niches/birdwatching/upcoming?days=30", { key: k.key }));
    expect(titles(up)).toEqual(expect.arrayContaining(["Spring Migration Fest", "Public holiday", "Rule comment deadline"]));
    expect(titles(up)).not.toContain("RSS event");
  });

  it("items tagged scope=internal never reach an external key, whatever their source", async () => {
    await seedCatalog();
    const ext = await mkKey("external");
    const r = await json(await call("/v1/niches/birdwatching/trends", { key: ext.key }));
    expect(titles(r)).not.toContain("Hybrid topic scored with internal signals");
    expect((await call("/v1/evidence/birdwatching~ffffff01", { key: ext.key })).status).toBe(404);
    expect((await call("/v1/evidence/birdwatching~ffffff01", { key: (await mkKey("internal")).key })).status).toBe(200);
    expect(titles(await json(await call("/v1/niches/birdwatching/trends", { key: (await mkKey("admin")).key })))).toContain("Hybrid topic scored with internal signals");
  });

  it("filters upcoming by window, drops past events and computes days_until/publish_by", async () => {
    await seedCatalog();
    const k = await mkKey("external");
    const r = await json(await call("/v1/niches/birdwatching/upcoming?days=30", { key: k.key }));
    expect(titles(r)).toEqual(["Rule comment deadline"]);
    expect(r.items[0]).toMatchObject({ days_until: 5, source: "federal_register", publish_by_passed: false });
    const wide = await json(await call("/v1/niches/birdwatching/upcoming?days=365", { key: k.key }));
    expect(titles(wide)).toEqual(["Rule comment deadline", "Far away event"]);
  });

  it("applies the days window to trends", async () => {
    await seedCatalog();
    const k = await mkKey("external");
    const wide = await json(await call("/v1/niches/birdwatching/trends?days=30", { key: k.key }));
    expect(titles(wide)).toContain("Old item");
    const narrow = await json(await call("/v1/niches/birdwatching/trends?days=1", { key: k.key }));
    expect(titles(narrow)).not.toContain("Old item");
  });

  it("validates parameters", async () => {
    const k = await mkKey();
    expect((await call("/v1/niches/birdwatching/trends?days=0", { key: k.key })).status).toBe(400);
    expect((await call("/v1/niches/birdwatching/trends?days=abc", { key: k.key })).status).toBe(400);
    expect((await call("/v1/niches/birdwatching/upcoming?days=9999", { key: k.key })).status).toBe(400);
    expect((await call("/v1/niches/BAD_ID/trends", { key: k.key })).status).toBe(400);
    expect((await call("/v1/niches/missing-niche/trends", { key: k.key })).status).toBe(404);
    expect((await call("/v1/nothing", { key: k.key })).status).toBe(404);
    expect((await call("/v1/niches", { key: k.key, method: "POST", body: {} })).status).toBe(405);
  });

  it("evidence: filtered signals; items from a non-served source do not exist", async () => {
    await seedCatalog();
    const k = await mkKey("external");
    const ok = await json(await call("/v1/evidence/birdwatching~aaaaaa01", { key: k.key }));
    expect(ok.evidence.map((e: { source: string }) => e.source)).toEqual(["hn"]); // apple, google_trends, provenance-less gdelt dropped
    expect(ok.source).toBe("hn");
    expect((await call("/v1/evidence/birdwatching~bbbbbb01", { key: k.key })).status).toBe(404); // apple item
    expect((await call("/v1/evidence/birdwatching~bbbbbb03", { key: k.key })).status).toBe(404); // apple: external can't
    expect((await call("/v1/evidence/birdwatching~bbbbbb03", { key: (await mkKey("internal")).key })).status).toBe(200);
    expect((await call("/v1/evidence/not-an-id", { key: k.key })).status).toBe(400);
    expect((await call("/v1/evidence/birdwatching~zzzzzz99", { key: k.key })).status).toBe(404);
  });

  it("isolates custom niches per key and lists them next to the catalog", async () => {
    await seedCatalog();
    const a = await mkKey();
    const b = await mkKey();
    await put(`custom/${a.id}/niches.json`, { schema: 1, niches: [{ id: "custom-mysite-com", name: "mysite.com", url: "https://mysite.com/", sources: ["hn"], updated_at: new Date().toISOString() }] });
    await put(`custom/${a.id}/custom-mysite-com/trends.json`, trendsDoc("custom-mysite-com"));
    const listA = await json(await call("/v1/niches", { key: a.key }));
    expect(listA.niches.map((n: { id: string }) => n.id)).toEqual(["birdwatching", "custom-mysite-com"]);
    expect(listA.niches[1]).toMatchObject({ custom: true, url: "https://mysite.com/" });
    expect((await call("/v1/niches/custom-mysite-com/trends", { key: a.key })).status).toBe(200);
    expect((await call("/v1/niches/custom-mysite-com/trends", { key: b.key })).status).toBe(404);
    expect((await json(await call("/v1/niches", { key: b.key }))).niches.map((n: { id: string }) => n.id)).toEqual(["birdwatching"]);
  });

  it("safeWhy blanks text that names a source we do not serve", () => {
    const ext = allowedSources("external");
    expect(safeWhy("2 sources; hn 120 pts", ext)).toBe("2 sources; hn 120 pts");
    expect(safeWhy("Google Trends #3", ext)).toBe("");
    expect(safeWhy("apple #1", ext)).toBe("");
    expect(safeWhy("apple #1", allowedSources("internal"))).toBe("apple #1");
    expect(safeWhy("apple 12 stars", allowedSources("internal"))).toBe("apple 12 stars");
    expect(safeWhy("reddit #1", allowedSources("internal"))).toBe("");
    expect(safeWhy("matches: topics, physics, githubs", ext)).toBe("matches: topics, physics, githubs");
    expect(safeWhy("2 sources; apple 12 stars", ext)).toBe("");
  });
});

describe("noindex until launch", () => {
  it("every Worker-served response carries X-Robots-Tag (static pages + robots.txt: site/public/_headers)", async () => {
    const k = await mkKey();
    const paths: [string, { key?: string; method?: string }][] = [
      ["/healthz", {}], ["/keys", {}], ["/auth/github/login", {}], ["/nope", {}],
      ["/v1/niches", {}], ["/v1/niches", { key: k.key }], ["/v1/niches/x/trends", { key: k.key }], ["/mcp", { method: "GET" }], ["/mcp", { key: k.key, method: "POST" }],
    ];
    for (const [p, o] of paths) {
      const r = await call(p, o);
      expect(r.headers.get("x-robots-tag"), `${o.method ?? "GET"} ${p} ${r.status}`).toBe("noindex, nofollow");
    }
  });
});

describe("github_rising (catalog/github-rank.json)", () => {
  const repo = (name: string, lang: string, daily: number, extra: Record<string, unknown> = {}) => ({
    repo: name, url: `https://github.com/${name}`, description: "d", language: lang, topics: ["t"], total_stars: 1000, total_forks: 50,
    stars: { daily, weekly: daily * 5, monthly: daily * 20 }, forks: { daily: 1, weekly: 5, monthly: 20 },
    stars_per_day: { daily, weekly: daily / 2, monthly: daily / 4 }, series: [1, 2, 3], new_repo: false,
    source: "github", source_url: `https://github.com/${name}`, license: "GitHub public events", ...extra,
  });

  async function seed() {
    await put("catalog/github-rank.json", ({
      schema: 1, date: "2026-10-31", generated_at: "2026-11-01T05:00Z", note: "n", unit: "stars/day",
      ranks: { daily: ["a/one", "b/two", "c/three", "gone/x"], weekly: ["b/two", "a/one"], monthly: [] },
      repos: [repo("a/one", "Python", 400), repo("b/two", "Rust", 300), repo("c/three", "Python", 200, { license: "" })],
    }));
  }

  it("returns a window in rank order, filters by language and drops rows without provenance", async () => {
    await seed();
    const { env } = await import("cloudflare:workers");
    const k = await mkKey("internal");
    const all = await getGithubRising(env, { id: k.id, scope: "internal" } as never, "daily", null, 25);
    expect(all.items.map((i) => i.repo)).toEqual(["a/one", "b/two"]);                 // c/three has no license, gone/x has no row
    expect(all.items[0]).toMatchObject({ rank: 1, stars: 400, language: "Python", source: "github" });
    const py = await getGithubRising(env, { id: k.id, scope: "internal" } as never, "daily", "python", 25);
    expect(py.items.map((i) => i.repo)).toEqual(["a/one"]);
    const wk = await getGithubRising(env, { id: k.id, scope: "internal" } as never, "weekly", null, 1);
    expect(wk.items.map((i) => i.repo)).toEqual(["b/two"]);
    expect(wk.unit).toBe("stars/day");
  });

  it("is served to external keys (github is a public source) but not when the row is tagged internal", async () => {
    await seed();
    const { env } = await import("cloudflare:workers");
    const k = await mkKey("external");
    const r = await getGithubRising(env, { id: k.id, scope: "external" } as never, "daily", null, 25);
    expect(r.items.map((i) => i.repo)).toEqual(["a/one", "b/two"]);
    await put("catalog/github-rank.json", {
      ranks: { daily: ["a/one"] }, repos: [repo("a/one", "Python", 400, { scope: "internal" })], date: "2026-10-31",
    });
    expect((await getGithubRising(env, { id: k.id, scope: "external" } as never, "daily", null, 25)).items).toEqual([]);
  });
});

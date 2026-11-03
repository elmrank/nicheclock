import { beforeEach, describe, expect, it } from "vitest";
import { match, rowToEntity, eventToEntity } from "../site/trending/match.js";
import * as P from "../site/trending/profile.js";
import { clearShardCache, fnv1a } from "../src/shards";
import { MCP_SPEC_VERSION } from "../src/mcp";
import { assets, fixtureJson, shardFiles } from "./assets";
import { call, json, mkKey } from "./helpers";

beforeEach(() => clearShardCache());
const env = { ASSETS: assets, RL_PROFILE: undefined };
const META = { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION, "io.modelcontextprotocol/clientCapabilities": {} };

/** the three sample profiles of the page (profile.js SAMPLES), as an agent would send them */
const SAMPLES: Record<string, object> = Object.fromEntries(
  P.SAMPLES.map((s: { key: string; topics: string[][] }) => [s.key, { schema: P.SCHEMA, topics: s.topics.map((t) => ({ qid: t[0], label: t[1], role: t[2] })), region: "US" }]),
);

function browserList(profile: object | undefined) {
  const pool = fixtureJson("pool-US.json");
  const c = fixtureJson("concepts.json");
  const labels = new Map<number, string>();
  for (const r of c.labels) labels.set(r[0], r[1]);
  for (const r of c.rows) labels.set(r[0], r[1]);
  const v = P.validate(profile);
  return match(v.profile, pool.rows.map(rowToEntity), (q: number) => labels.get(q));
}

describe("shards (build-time cut of the pool)", () => {
  it("every shard file is at most 150 KB and the hash is the one shard.py uses", () => {
    expect(Object.keys(shardFiles).length).toBeGreaterThan(10);
    for (const [name, text] of Object.entries(shardFiles)) expect(new TextEncoder().encode(text).length, name).toBeLessThanOrEqual(150_000);
    // numbers printed by nicheclock.catalog.shard.fnv1a
    expect(fnv1a("q718")).toBe(2578526312);
    expect(fnv1a("wchess")).toBe(2139547790);
    expect(fnv1a("tchess")).toBe(2868594269);
    expect(fnv1a("é")).toBe(513665217);
  });
});

describe("trends_for_profile", () => {
  for (const key of ["chess", "birdwatching", "sourdough"]) {
    it(`gives the browser's list for the ${key} sample (every row at fit 0.50 or more, same order)`, async () => {
      const k = await mkKey();
      const r = await call("/v1/profile/trends", { key: k.key, body: { profile: SAMPLES[key], limit: 50 }, env });
      expect(r.status).toBe(200);
      const out = await json(r);
      const want = browserList(SAMPLES[key]).fits.slice(0, 50);
      expect(out.rows.map((x: any) => x.qid)).toEqual(want.map((x: any) => `Q${x.entity.qid}`));
      expect(out.rows.map((x: any) => x.fit)).toEqual(want.map((x: any) => Math.round(x.fit * 100) / 100));
      expect(out.rows.map((x: any) => x.why)).toEqual(want.map((x: any) => x.why));
      expect(new Set(out.rows.map((x: any) => x.qid)).size).toBe(out.rows.length);
      for (const row of out.rows) {
        expect(row.why.length).toBeGreaterThan(0);
        expect(row.volume.unit).toBe("pageviews/day");
        expect(row.source_url).toMatch(/^https:\/\/en\.wikipedia\.org\/wiki\//);
        expect(row.relations.length).toBeGreaterThan(0);
      }
      console.log(`reply size ${key}: ${JSON.stringify(out).length} bytes, ${out.rows.length} rows (matched ${out.matched})`);
    });
  }

  it("takes topics as text from the concept list and reports what it could not find", async () => {
    const k = await mkKey();
    const r = await call("/v1/profile/trends", { key: k.key, body: { profile: { topics: [{ text: "Chess" }, { text: "zzunknownword" }], region: "US" }, limit: 5 }, env });
    const out = await json(r);
    expect(r.status).toBe(200);
    expect(out.unresolved).toEqual(["zzunknownword"]);
    expect(out.rows.length).toBe(5);
    expect(out.rows[0].qid).toBeDefined();
  });

  it("drops an avoided class, honours min_fit and refuses a region without a pool", async () => {
    const k = await mkKey();
    const base = { topics: [{ qid: "Q718", label: "chess", role: "main" }], region: "US" };
    const all = await json(await call("/v1/profile/trends", { key: k.key, body: { profile: base, limit: 50, min_fit: 0.35 }, env }));
    const strict = await json(await call("/v1/profile/trends", { key: k.key, body: { profile: base, limit: 50, min_fit: 0.9 }, env }));
    expect(strict.rows.every((x: any) => x.fit >= 0.9)).toBe(true);
    expect(strict.matched).toBeLessThan(all.matched);
    const avoid = await json(await call("/v1/profile/trends", { key: k.key, body: { profile: { ...base, avoid: [{ qid: "Q41314", label: "Bobby Fischer" }] }, limit: 50 }, env }));
    expect(avoid.rows.some((x: any) => x.qid === "Q41314")).toBe(false);
    const nopool = await call("/v1/profile/trends", { key: k.key, body: { profile: { ...base, region: "DE" } }, env });
    expect(nopool.status).toBe(400);
    expect((await json(nopool)).error.code).toBe("no_pool");
    const bad = await call("/v1/profile/trends", { key: k.key, body: { profile: { topics: [], region: "US" } }, env });
    expect(bad.status).toBe(400);
    expect((await json(bad)).error.code).toBe("invalid_profile");
    expect((await call("/v1/profile/trends", { key: k.key, body: { profile: base, limit: 0 }, env })).status).toBe(400);
  });

  it("needs the Bearer key", async () => {
    expect((await call("/v1/profile/trends", { body: { profile: SAMPLES.chess }, env })).status).toBe(401);
  });
});

describe("upcoming_for_profile", () => {
  it("finds International chess day for the chess profile, with publish_by and your_slot", async () => {
    const k = await mkKey();
    const profile = { ...(SAMPLES.chess as object), cadence: { days: ["tuesday", "friday"] } };
    const r = await call("/v1/profile/upcoming", { key: k.key, body: { profile, days: 365, limit: 50 }, env });
    expect(r.status).toBe(200);
    const out = await json(r);
    const ev = out.events.find((e: any) => e.title === "International chess day");
    expect(ev).toBeDefined();
    expect(ev.kind).toBe("observance");
    expect(ev.source_url).toMatch(/wikidata\.org/);
    expect(ev.license).toContain("CC0");
    expect(ev.rule).toBe("July 20");
    expect(ev.publish_by <= ev.date).toBe(true);
    expect(ev.your_slot.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    console.log(`upcoming reply size chess: ${JSON.stringify(out).length} bytes, ${out.events.length} events`);
  });

  it("gives the browser's events for the profile (same ids, same fits)", async () => {
    const obs = fixtureJson("observances.json");
    const c = fixtureJson("concepts.json");
    const labels = new Map<number, string>();
    for (const r of c.labels) labels.set(r[0], r[1]);
    for (const r of c.rows) labels.set(r[0], r[1]);
    const ents = obs.rows.map((r: any[]) => eventToEntity({ qid: `Q${r[0]}`, title: r[1], niche: { h: r[6], f: r[7] } })).filter(Boolean);
    const want = match(P.validate(SAMPLES.chess).profile, ents, (q: number) => labels.get(q)).fits;
    const k = await mkKey();
    const out = await json(await call("/v1/profile/upcoming", { key: k.key, body: { profile: SAMPLES.chess, days: 365, limit: 50 }, env }));
    // the browser list has no date window; the tool returns the ones whose next date is within a year
    const got = new Set(out.events.map((e: any) => e.qid));
    expect(got.size).toBeGreaterThan(0);
    for (const q of got) expect(want.some((w: any) => `Q${w.entity.qid}` === q)).toBe(true);
  });
});

describe("MCP", () => {
  const rpc = (key: string, name: string, args: object) =>
    call("/mcp", { key, body: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args, _meta: META } }, headers: { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/call", "mcp-name": name, accept: "application/json, text/event-stream" }, env });

  it("lists the three profile tools and calls them", async () => {
    const k = await mkKey();
    const list = await json(await call("/mcp", { key: k.key, body: { jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: META } }, headers: { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/list", accept: "application/json" } }));
    const names = list.result.tools.map((t: any) => t.name);
    for (const n of ["trends_for_profile", "upcoming_for_profile", "resolve_topic"]) expect(names).toContain(n);
    const t = await json(await rpc(k.key, "trends_for_profile", { profile: SAMPLES.chess, limit: 10 }));
    expect(t.result.isError).toBe(false);
    expect(t.result.structuredContent.rows).toHaveLength(10);
    const u = await json(await rpc(k.key, "upcoming_for_profile", { profile: SAMPLES.chess, days: 365 }));
    expect(u.result.isError).toBe(false);
    const rt = await json(await rpc(k.key, "resolve_topic", { text: "chess" }));
    expect(rt.result.structuredContent.qid).toBe("Q718");
    expect(rt.result.structuredContent.source).toBe("nicheclock-list");
    const bad = await json(await rpc(k.key, "trends_for_profile", { profile: { topics: [], region: "US" } }));
    expect(bad.result.isError).toBe(true);
  });
});

describe("labels", () => {
  it("looks up the list's label for an id with no label, so the named tie still finds chess players", async () => {
    const k = await mkKey();
    const bare = await json(await call("/v1/profile/trends", { key: k.key, body: { profile: { topics: [{ qid: "Q718" }], region: "US" }, limit: 50 }, env }));
    const labeled = await json(await call("/v1/profile/trends", { key: k.key, body: { profile: { topics: [{ qid: "Q718", label: "chess" }], region: "US" }, limit: 50 }, env }));
    expect(bare.rows.map((r: any) => r.qid)).toEqual(labeled.rows.map((r: any) => r.qid));
    expect(bare.rows.some((r: any) => /chess player/i.test(r.why))).toBe(true);
  });
});

describe("rate limit of the profile tools (per API key, through the binding)", () => {
  it("answers 429 over the limit, in REST and as a tool error in MCP", async () => {
    const k = await mkKey();
    const keys: string[] = [];
    let n = 0;
    const RL_PROFILE = { limit: async ({ key }: { key: string }) => { keys.push(key); return { success: ++n <= 2 }; } } as unknown as RateLimit;
    const e = { ...env, RL_PROFILE };
    const body = { profile: SAMPLES.chess, limit: 1 };
    expect((await call("/v1/profile/trends", { key: k.key, body, env: e })).status).toBe(200);
    expect((await call("/v1/profile/resolve", { key: k.key, body: { text: "chess" }, env: e })).status).toBe(200);
    const r = await call("/v1/profile/upcoming", { key: k.key, body, env: e });
    expect(r.status).toBe(429);
    expect(r.headers.get("retry-after")).toBe("60");
    expect(keys.every((x) => x === keys[0] && x.startsWith("k_"))).toBe(true);     // the key id, never an address
    const m = await call("/mcp", { key: k.key, body: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "resolve_topic", arguments: { text: "chess" }, _meta: META } }, headers: { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/call", "mcp-name": "resolve_topic", accept: "application/json" }, env: e });
    expect((await json(m)).result.isError).toBe(true);
  });
});

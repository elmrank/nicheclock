import { beforeEach, describe, expect, it } from "vitest";
import { MCP_SPEC_VERSION } from "../src/mcp";
import { clearShardCache } from "../src/shards";
import { call, json, mkKey } from "./helpers";

/** A fake ASSETS binding with the digests of nicheclock/catalog/digest.py (tiny, made up for the test). */
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const LIC = "CC0 (Wikimedia Analytics datasets)";
const index = {
  schema: 1,
  regions: [
    { id: "global", label: "Worldwide", topics: 5, date: "2026-11-02", limited: false, limited_note: null },
    { id: "US", label: "United States", topics: 1, date: "2026-11-02", limited: false, limited_note: null },
    { id: "IN", label: "India", topics: 0, date: "2026-11-02", limited: false, limited_note: null },
    { id: "ET", label: "Ethiopia", topics: 0, date: "2026-11-02", limited: true, limited_note: "Wikimedia publishes no country data here" },
  ],
  categories: { sports: "Sports", politics: "Politics", "hobbies-leisure": "Hobbies and Leisure" },
  kinds: ["election", "film release", "holiday", "sports event"],
  units: { "pageviews/day": "pageviews/day (first 10 unique per device)", "articles/day": "articles/day (estimated from sampled 15-min GDELT files)" },
  licenses: {
    wikipedia: { source: "wikimedia_pageviews", label: "Wikipedia pageviews", license: LIC, url: "https://dumps.wikimedia.org/" },
    news: { source: "gdelt", label: "GDELT", license: "GDELT Project: free use with citation", url: "https://www.gdeltproject.org/" },
    hn: { source: "hn", label: "Hacker News", license: "HN API terms", url: "https://hn.algolia.com/api" },
  },
  events_today: day(0),
  lag_note: "Publish by is the event date minus a default lead time per kind of event.",
  event_months: {} as Record<string, number>,
};
const row = (o: Record<string, unknown>) => ({ q: "Q1", l: "Topic", c: "sports", s: "rising", r: [2, 3, 4], v: 1000, u: "pageviews/day", x: ["wikipedia"], w: "Wikipedia 1,000 pageviews/day, 3x its 14-day median", a: "https://en.wikipedia.org/wiki/Topic", y: ["wikipedia"], z: [true, true, true], ...o });
const trending = {
  global: {
    schema: 1,
    region: "global",
    date: "2026-11-02",
    generated_at: "2026-11-03T06:43Z",
    rows: [
      row({ q: "Q10", l: "Alpha Cup", r: [9.5, 6, 3], v: 50_000, z: [true, true, true], x: ["news", "wikipedia"] }),
      row({ q: "Q11", l: "Beta Vote", c: "politics", r: [1.1, 1.6, 2.4], v: 8000, z: [false, true, true] }),
      row({ q: null, l: "Gamma Book", c: "hobbies-leisure", s: "steady", r: [null, null, null], v: null, u: null, x: ["openlibrary"], z: undefined, y: [] }),
      row({ q: "Q13", l: "Delta Fall", s: "fading", r: [0.4, 0.9, 5], z: undefined }),
      row({ q: "Q14", l: "Epsilon End", s: "ended", r: [0.2, 0.3, 10], z: undefined }),
    ],
  },
  US: { schema: 1, region: "US", date: "2026-11-02", generated_at: "2026-11-03T06:43Z", rows: [row({ q: "Q20", l: "US Only", c: "politics", u: "articles/day", x: ["news"], y: ["news"] })] },
};
type EventRow = ReturnType<typeof ev>;
const ev = (title: string, offset: number, kind: string, lag: number, regions: string[] | null, region = "global") => ({
  t: title, d: day(offset), p: day(offset - lag), g: lag, k: kind, c: "sports", e: regions, o: region, n: "wikidata", a: "https://www.wikidata.org/wiki/Q1", i: "CC0 (Wikidata)",
});
const events = {
  events: [
    ev("Past Cup", -2, "sports event", 7, null),
    ev("Near Premiere", 5, "film release", 14, null),
    ev("Local Holiday", 10, "holiday", 28, ["IN"], "IN"),
    ev("Shared Holiday", 12, "holiday", 28, ["IN", "US"], "multi"),
    ev("US Election", 20, "election", 21, ["US"], "US"),
    ev("Far Final", 100, "sports event", 7, null),
  ],
};
const served: Record<string, unknown> = {};
const requested: string[] = [];
const assets = {
  fetch: async (r: Request) => {
    const p = new URL(r.url).pathname;
    const name = p.startsWith("/catalog-data/tools/") ? p.slice("/catalog-data/tools/".length) : "";
    requested.push(name);
    const doc = served[name];
    return doc === undefined ? new Response("not found", { status: 404 }) : new Response(JSON.stringify(doc), { headers: { "content-type": "application/json" } });
  },
} as unknown as Fetcher;

beforeEach(() => {
  clearShardCache();
  requested.length = 0;
  for (const k of Object.keys(served)) delete served[k];
  const byMonth: Record<string, EventRow[]> = {};
  for (const e of events.events) (byMonth[e.d.slice(0, 7)] ??= []).push(e);
  index.event_months = Object.fromEntries(Object.entries(byMonth).map(([m, r]) => [m, r.length]));
  Object.assign(served, { "index.json": index, "trending-global.json": trending.global, "trending-US.json": trending.US });
  for (const [m, rows] of Object.entries(byMonth)) served[`events-${m}.json`] = { schema: 1, events: rows };
});

const META = { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION, "io.modelcontextprotocol/clientCapabilities": {} };
const closed = { ASSETS: assets, NICHE_TOOLS: "off", GITHUB_RANK: "off" };

function rpc(key: string, method: string, params: Record<string, unknown> = {}, env: Record<string, unknown> = closed) {
  const headers: Record<string, string> = { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": method };
  if (method === "tools/call") headers["mcp-name"] = String(params.name);
  return call("/mcp", { key, env: env as never, headers, body: { jsonrpc: "2.0", id: 1, method, params: { ...params, _meta: META } } });
}
async function tool(key: string, name: string, args: Record<string, unknown> = {}) {
  const r = await json(await rpc(key, "tools/call", { name, arguments: args }));
  const text = r.result.content[0].text as string;
  return { isError: r.result.isError as boolean, text, data: r.result.isError ? null : JSON.parse(text) };
}

describe("tools/list on the public server", () => {
  it("an external key sees the catalog tools and the profile tools, not the niche tools", async () => {
    const k = await mkKey("external");
    const names = (await json(await rpc(k.key, "tools/list"))).result.tools.map((t: { name: string }) => t.name);
    expect(names).toEqual(["trending_now", "upcoming_events", "trends_for_profile", "upcoming_for_profile", "resolve_topic"]);
  });

  it("an internal key still sees the niche tools, and they still answer", async () => {
    const k = await mkKey("internal");
    const names = (await json(await rpc(k.key, "tools/list"))).result.tools.map((t: { name: string }) => t.name);
    expect(names).toEqual(["trending_now", "upcoming_events", "trends_for_niche", "upcoming", "niche_from_url", "trends_for_profile", "upcoming_for_profile", "resolve_topic"]);
    const r = await tool(k.key, "trends_for_niche", { niche: "none" });
    expect(r.isError).toBe(true);
    expect(r.text).toContain("Valid niche ids");
  });

  it("calling a niche tool with an external key is an unknown tool; NICHE_TOOLS = on gives it back", async () => {
    const k = await mkKey("external");
    const res = await json(await rpc(k.key, "tools/call", { name: "trends_for_niche", arguments: { niche: "x" } }));
    expect(res.error.message).toContain("Unknown tool");
    const on = (await json(await rpc(k.key, "tools/list", {}, { ...closed, NICHE_TOOLS: "on" }))).result.tools.map((t: { name: string }) => t.name);
    expect(on).toContain("trends_for_niche");
  });

  it("the niche REST routes are 404 for an external key and work for an internal one", async () => {
    const ext = await mkKey("external");
    const int = await mkKey("internal");
    for (const p of ["/v1/niches", "/v1/niches/x/trends", "/v1/evidence/x", "/v1/jobs/j_000000000000"]) {
      expect((await call(p, { key: ext.key, env: closed as never })).status, p).toBe(404);
    }
    expect((await call("/v1/niches", { key: int.key, env: closed as never })).status).toBe(200);
    expect((await call("/v1/keys", { key: ext.key, env: closed as never })).status).toBe(200);
  });
});

describe("trending_now", () => {
  it("defaults to worldwide, 7d, rising only, in catalog order, with minimal rows", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key, "trending_now");
    expect(data).toMatchObject({ region: "global", window: "7d", state: "rising", category: null, data_date: "2026-11-02", generated_at: "2026-11-03T06:43Z", matching: 2 });
    expect(data.items.map((i: { label: string }) => i.label)).toEqual(["Alpha Cup", "Beta Vote"]);
    expect(data.items[0]).toEqual({
      rank: 1, label: "Alpha Cup", qid: "Q10", rise: 3, volume: 50_000, unit: "pageviews/day", state: "rising", sources: ["news", "wikipedia"],
      category: "sports", category_label: "Sports", why: "Wikipedia 1,000 pageviews/day, 3x its 14-day median", link: "https://en.wikipedia.org/wiki/Topic",
    });
    expect(data.note).toContain("none is search volume");
  });

  it("the window picks the rise and drops topics not rising in it", async () => {
    const k = await mkKey();
    const day1 = (await tool(k.key, "trending_now", { window: "24h" })).data;
    expect(day1.items.map((i: { label: string; rise: number }) => [i.label, i.rise])).toEqual([["Alpha Cup", 9.5]]);
    const two = (await tool(k.key, "trending_now", { window: "48h" })).data;
    expect(two.items.map((i: { rise: number }) => i.rise)).toEqual([6, 1.6]);
  });

  it("state=all adds steady, fading and ended rows with their state; a row without a Wikidata id has qid null", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key, "trending_now", { state: "all" });
    expect(data.items.map((i: { label: string; state: string }) => [i.label, i.state])).toEqual([
      ["Alpha Cup", "rising"], ["Beta Vote", "rising"], ["Gamma Book", "steady"], ["Delta Fall", "fading"], ["Epsilon End", "ended"],
    ]);
    expect(data.items[2]).toMatchObject({ qid: null, volume: null, unit: null, rise: null });
  });

  it("filters by category id or label and says what is valid for a wrong one", async () => {
    const k = await mkKey();
    expect((await tool(k.key, "trending_now", { category: "politics" })).data.items.map((i: { label: string }) => i.label)).toEqual(["Beta Vote"]);
    expect((await tool(k.key, "trending_now", { category: "Hobbies and Leisure", state: "all" })).data.items).toHaveLength(1);
    const bad = await tool(k.key, "trending_now", { category: "weather" });
    expect(bad.isError).toBe(true);
    expect(bad.text).toContain("invalid_params");
    expect(bad.text).toContain("sports, politics, hobbies-leisure");
  });

  it("reads another region (any case), reports a limited one, and rejects an unknown one", async () => {
    const k = await mkKey();
    const us = (await tool(k.key, "trending_now", { region: "us" })).data;
    expect(us).toMatchObject({ region: "US", region_label: "United States" });
    expect(us.items.map((i: { label: string }) => i.label)).toEqual(["US Only"]);
    expect(us.units).toEqual({ "articles/day": index.units["articles/day"] });
    expect(Object.keys(us.licenses)).toEqual(["news"]);
    const et = (await tool(k.key, "trending_now", { region: "ET" })).data;
    expect(et.items).toEqual([]);
    expect(et.limited_note).toContain("no country data");
    const bad = await tool(k.key, "trending_now", { region: "ZZ" });
    expect(bad.text).toContain("Valid regions: global, US, IN, ET");
  });

  it("returns only the licences and units of the rows it returns", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key, "trending_now", { limit: 1 });
    expect(data.items).toHaveLength(1);
    expect(data.matching).toBe(2);
    expect(Object.keys(data.licenses).sort()).toEqual(["news", "wikipedia"]);
    expect(data.licenses.wikipedia.license).toBe(LIC);
    expect(Object.keys(data.units)).toEqual(["pageviews/day"]);
  });

  it("validates the arguments", async () => {
    const k = await mkKey();
    for (const args of [{ window: "30d" }, { state: "falling" }, { limit: 0 }, { limit: 51 }, { limit: 2.5 }, { region: 5 }, { category: 7 }]) {
      const r = await tool(k.key, "trending_now", args);
      expect(r.isError, JSON.stringify(args)).toBe(true);
      expect(r.text).toContain("invalid_params");
    }
  });

  it("says so when the catalog digests are not on this deployment", async () => {
    const k = await mkKey();
    delete served["index.json"];
    clearShardCache();
    const r = await tool(k.key, "trending_now");
    expect(r.isError).toBe(true);
    expect(r.text).toContain("catalog_unavailable");
  });

  it("counts against the same per-key quota as every other call", async () => {
    const k = await mkKey();
    await tool(k.key, "trending_now");
    const { env } = await import("cloudflare:workers");
    const rows = (await env.DB.prepare("SELECT endpoint, requests FROM usage_daily WHERE key_id = ?").bind(k.id).all<{ endpoint: string; requests: number }>()).results;
    expect(rows).toEqual([{ endpoint: "POST /mcp tools/call", requests: 1 }]);
  });
});

describe("upcoming_events", () => {
  const titles = (d: { items: { title: string }[] }) => d.items.map((i) => i.title);

  it("lists the next 30 days soonest first, drops past events and computes the countdowns", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key, "upcoming_events");
    expect(titles(data)).toEqual(["Near Premiere", "Local Holiday", "Shared Holiday", "US Election"]);
    expect(data.items[0]).toEqual({
      date: day(5), title: "Near Premiere", kind: "film release", category: "sports", category_label: "Sports", regions: ["global"],
      publish_by: day(-9), lag_days: 14, days_until: 5, days_to_publish_by: -9, publish_by_passed: true,
      source: "wikidata", source_url: "https://www.wikidata.org/wiki/Q1", license: "CC0 (Wikidata)",
    });
    expect(data).toMatchObject({ region: "global", days: 30, kind: null, matching: 4 });
    expect(data.note).toContain("default lead time");
  });

  it("days widens or narrows the window", async () => {
    const k = await mkKey();
    expect(titles((await tool(k.key, "upcoming_events", { days: 7 })).data)).toEqual(["Near Premiere"]);
    expect(titles((await tool(k.key, "upcoming_events", { days: 365 })).data)).toContain("Far Final");
  });

  it("a region keeps untagged events and those whose regions list has it", async () => {
    const k = await mkKey();
    expect(titles((await tool(k.key, "upcoming_events", { region: "US" })).data)).toEqual(["Near Premiere", "Shared Holiday", "US Election"]);
    expect(titles((await tool(k.key, "upcoming_events", { region: "IN" })).data)).toEqual(["Near Premiere", "Local Holiday", "Shared Holiday"]);
  });

  it("filters by kind (any case) and limits the count", async () => {
    const k = await mkKey();
    expect(titles((await tool(k.key, "upcoming_events", { kind: "Holiday", days: 60 })).data)).toEqual(["Local Holiday", "Shared Holiday"]);
    const one = (await tool(k.key, "upcoming_events", { limit: 1 })).data;
    expect(one.items).toHaveLength(1);
    expect(one.matching).toBe(4);
    const bad = await tool(k.key, "upcoming_events", { kind: "concert" });
    expect(bad.text).toContain("Valid kinds: election, film release, holiday, sports event");
  });

  it("reads only the month files its window touches", async () => {
    const k = await mkKey();
    await tool(k.key, "upcoming_events", { days: 7 });
    const months = requested.filter((n) => n.startsWith("events-"));
    expect(months.length).toBeGreaterThan(0);
    expect(months.length).toBeLessThanOrEqual(2);
    expect(months).not.toContain(`events-${day(100).slice(0, 7)}.json`);
    expect(requested).not.toContain("trending-global.json");
  });

  it("validates the arguments", async () => {
    const k = await mkKey();
    for (const args of [{ days: 0 }, { days: 366 }, { days: 1.5 }, { limit: 0 }, { region: "ZZ" }, { kind: 3 }]) {
      const r = await tool(k.key, "upcoming_events", args);
      expect(r.isError, JSON.stringify(args)).toBe(true);
      expect(r.text).toContain("invalid_params");
    }
  });
});

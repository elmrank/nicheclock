import { beforeEach, describe, expect, it } from "vitest";
import { MCP_SPEC_VERSION } from "../src/mcp";
import { clearShardCache } from "../src/shards";
import { call, db, json, mkKey } from "./helpers";

/** A fake ASSETS binding holding /catalog-data/google-updates.json in the schema of site/google_updates.py (made-up content). */
const day = (offset: number) => new Date(Date.now() + offset * 86_400_000).toISOString().slice(0, 10);
const G = "https://developers.google.com/search";
const ST = "https://status.search.google.com/incidents";
const attribution = { text: "Source: Google Search Central, CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0/", policy_url: "https://developers.google.com/terms/site-policies" };
const ended = { name: "September 2026 spam update", type: "spam", started: "2026-09-24", ended: "2026-10-08", duration_days: 14, url: `${ST}/A` };
const old = { name: "March 2026 core update", type: "core", started: "2026-03-27", ended: "2026-04-08", duration_days: 12, url: `${ST}/B` };
const discover = { name: "February 2026 Discover update", type: "discover", started: "2026-02-05", ended: "2026-02-27", duration_days: 22, url: `${ST}/C` };
const rollingCore = { name: "November 2026 core update", type: "core", started: "2026-11-01", ended: null, duration_days: null, url: `${ST}/D` };
const items = [
  { date: "2026-10-30", kind: "blog", category: "Crawling", title: "Supporting a shared AI training control", url: `${G}/blog/2026/10/x`, summary: "Today we are announcing plans." },
  { date: "2026-10-26", kind: "docs", category: "Spam policy", title: "Updated guidance on preventing user-generated spam", url: `${G}/docs/monitor-debug/prevent-abuse`, summary: "Updated the guide." },
  { date: "2026-10-23", kind: "docs", category: "Crawling", title: "Updated guidance on robots.txt specifications", url: "https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec", summary: "Added a recommendation." },
  { date: "2026-08-01", kind: "blog", category: "Blog", title: "An older post", url: `${G}/blog/2026/08/y`, summary: "" },
];
function doc(ranking: object[], rolling: object[], over: object = {}) {
  return { schema: 1, data_date: day(0), window: { from: "2025-11-03", to: day(0) }, attribution, rolling, ranking, items, ...over };
}

let served: unknown = null;
const assets = {
  fetch: async (r: Request) =>
    new URL(r.url).pathname === "/catalog-data/google-updates.json" && served !== null
      ? new Response(JSON.stringify(served), { headers: { "content-type": "application/json" } })
      : new Response("not found", { status: 404 }),
} as unknown as Fetcher;

beforeEach(() => {
  clearShardCache();
  served = doc([ended, old, discover], []);
});

const META = { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION, "io.modelcontextprotocol/clientCapabilities": {} };
const env = { ASSETS: assets, NICHE_TOOLS: "off", GITHUB_RANK: "off" };

function rpc(key: string, method: string, params: Record<string, unknown> = {}, e: Record<string, unknown> = env) {
  const headers: Record<string, string> = { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": method };
  if (method === "tools/call") headers["mcp-name"] = String(params.name);
  return call("/mcp", { key, env: e as never, headers, body: { jsonrpc: "2.0", id: 1, method, params: { ...params, _meta: META } } });
}
async function tool(key: string, args: Record<string, unknown> = {}) {
  const r = await json(await rpc(key, "tools/call", { name: "search_updates", arguments: args }));
  const text = r.result.content[0].text as string;
  return { isError: r.result.isError as boolean, text, data: r.result.isError ? null : JSON.parse(text) };
}

describe("search_updates", () => {
  it("is listed for an external key, right after upcoming_events, with its three parameters", async () => {
    const k = await mkKey("external");
    const tools = (await json(await rpc(k.key, "tools/list"))).result.tools as { name: string; inputSchema: { properties: Record<string, unknown> } }[];
    expect(tools.map((t) => t.name).slice(0, 3)).toEqual(["trending_now", "upcoming_events", "search_updates"]);
    expect(Object.keys(tools[2]?.inputSchema.properties ?? {})).toEqual(["since", "kind", "limit"]);
  });

  it("nothing rolling: rolling_now false, items newest first across the three kinds, ranking updates carry status ended", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key);
    expect(data).toMatchObject({ rolling_now: false, rolling_name: null, rolling_started: null, rolling: [], stale: false, since: null, kind: null, matching: 7 });
    expect(data.items.map((i: { date: string }) => i.date)).toEqual(["2026-10-30", "2026-10-26", "2026-10-23", "2026-09-24", "2026-08-01", "2026-03-27", "2026-02-05"]);
    const spam = data.items.find((i: { kind: string }) => i.kind === "ranking");
    expect(spam).toEqual({
      date: "2026-09-24", kind: "ranking", category: "Spam update", title: "September 2026 spam update", url: `${ST}/A`,
      summary: "Rollout started on 2026-09-24 and ended on 2026-10-08, after 14 days.", status: "ended", ended: "2026-10-08", duration_days: 14,
    });
    expect(data.items[0]).toEqual({ date: "2026-10-30", kind: "blog", category: "Crawling", title: "Supporting a shared AI training control", url: `${G}/blog/2026/10/x`, summary: "Today we are announcing plans." });
    expect(data.attribution).toContain("Source: Google Search Central, CC BY 4.0");
  });

  it("a core or spam update that started and has no end date: rolling_now true with name and start, status started", async () => {
    served = doc([rollingCore, ended], [{ name: rollingCore.name, type: "core", started: rollingCore.started, url: rollingCore.url }]);
    const k = await mkKey();
    const { data } = await tool(k.key, { kind: "ranking" });
    expect(data).toMatchObject({ rolling_now: true, rolling_name: "November 2026 core update", rolling_started: "2026-11-01", matching: 2 });
    expect(data.rolling).toEqual([{ name: "November 2026 core update", type: "core", started: "2026-11-01", url: `${ST}/D` }]);
    expect(data.items[0]).toMatchObject({ kind: "ranking", status: "started", ended: null, duration_days: null, category: "Core update" });
    expect(data.items[0].summary).toContain("still rolling out");
    expect(data.items[1]).toMatchObject({ status: "ended" });
  });

  it("a Discover update with no end date is listed as started but does not make rolling_now true", async () => {
    served = doc([{ ...discover, name: "November 2026 Discover update", started: "2026-11-02", ended: null, duration_days: null }, ended], []);
    const k = await mkKey();
    const { data } = await tool(k.key, { kind: "ranking" });
    expect(data.rolling_now).toBe(false);
    expect(data.items[0]).toMatchObject({ status: "started", category: "Discover update" });
  });

  it("kind keeps one kind only", async () => {
    const k = await mkKey();
    expect((await tool(k.key, { kind: "docs" })).data.items.map((i: { kind: string }) => i.kind)).toEqual(["docs", "docs"]);
    expect((await tool(k.key, { kind: "blog" })).data.items).toHaveLength(2);
    expect((await tool(k.key, { kind: "ranking" })).data.items).toHaveLength(3);
    expect((await tool(k.key, { kind: " DOCS " })).data.kind).toBe("docs");
  });

  it("since keeps items on or after it; a ranking update counts when it started, ended or is rolling on or after it", async () => {
    const k = await mkKey();
    expect((await tool(k.key, { since: "2026-10-23" })).data.items.map((i: { date: string }) => i.date)).toEqual(["2026-10-30", "2026-10-26", "2026-10-23"]);
    const r = (await tool(k.key, { since: "2026-10-05", kind: "ranking" })).data;
    expect(r.items.map((i: { title: string }) => i.title)).toEqual(["September 2026 spam update"]);     // started 09-24, ended 10-08: still in range
    expect((await tool(k.key, { since: "2026-10-09", kind: "ranking" })).data.items).toEqual([]);
    served = doc([rollingCore, old], [{ name: rollingCore.name, type: "core", started: "2026-01-01", url: rollingCore.url }]);
    clearShardCache();
    expect((await tool(k.key, { since: "2026-12-01", kind: "ranking" })).data.items.map((i: { title: string }) => i.title)).toEqual(["November 2026 core update"]);
  });

  it("limit cuts the list after sorting, matching still counts everything", async () => {
    const k = await mkKey();
    const { data } = await tool(k.key, { limit: 2 });
    expect(data.items).toHaveLength(2);
    expect(data.matching).toBe(7);
    expect(data.items[0].date).toBe("2026-10-30");
  });

  it("says stale when the data is three days old or more", async () => {
    served = doc([ended], [], { data_date: day(-3) });
    const k = await mkKey();
    expect((await tool(k.key)).data).toMatchObject({ stale: true, data_date: day(-3) });
    served = doc([ended], [], { data_date: day(-2) });
    clearShardCache();
    expect((await tool(k.key)).data.stale).toBe(false);
  });

  it("refuses bad arguments with a message that names the fix", async () => {
    const k = await mkKey();
    for (const [args, msg] of [
      [{ since: "yesterday" }, "since must be a date"],
      [{ since: "2026-02-30" }, "real calendar date"],
      [{ since: 20260901 }, "since must be a date"],
      [{ kind: "video" }, "kind must be one of ranking, docs, blog"],
      [{ limit: 0 }, "limit must be an integer between 1 and 100"],
      [{ limit: 101 }, "limit must be an integer between 1 and 100"],
      [{ limit: "5" }, "limit must be an integer between 1 and 100"],
    ] as [Record<string, unknown>, string][]) {
      const r = await tool(k.key, args);
      expect(r.isError, JSON.stringify(args)).toBe(true);
      expect(r.text).toContain(msg);
    }
  });

  it("answers a tool error when the data file is not published", async () => {
    served = null;
    const k = await mkKey();
    const r = await tool(k.key);
    expect(r.isError).toBe(true);
    expect(r.text).toContain("updates_unavailable");
  });

  it("returns none of the internal fields", async () => {
    const k = await mkKey();
    const { text } = await tool(k.key);
    for (const bad of ["impact", "alignment", "tags", "notify", "source_status", "first_seen", "run"]) expect(text).not.toContain(`"${bad}"`);
  });

  it("uses the same auth and the same metering as the other tools", async () => {
    expect((await rpc("nk_" + "0".repeat(40), "tools/call", { name: "search_updates", arguments: {} })).status).toBe(401);
    const k = await mkKey();
    await tool(k.key);
    await tool(k.key);
    const rows = (await db().prepare("SELECT endpoint, requests FROM usage_daily WHERE key_id = ?").bind(k.id).all<{ endpoint: string; requests: number }>()).results;
    expect(rows).toEqual([{ endpoint: "POST /mcp tools/call", requests: 2 }]);
    const limited = { ...env, MINUTE_LIMIT: "2" };
    expect((await rpc(k.key, "tools/call", { name: "search_updates", arguments: {} }, limited)).status).toBe(429);   // the minute counter already holds the two calls above
  });
});

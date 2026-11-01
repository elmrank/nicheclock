import { describe, expect, it } from "vitest";
import { MCP_SPEC_VERSION } from "../src/mcp";
import { regionKeeps } from "../src/catalog";
import { call, json, mkKey } from "./helpers";
import { iso, put, ymd } from "./fixtures";

const hn = { source: "hn", source_url: "https://news.ycombinator.com/item?id=1", attribution: "Hacker News (Y Combinator)" };
const niche = "region-niche";

async function seed() {
  const at = iso(-3_600_000);
  const t = (n: string, title: string, extra: object) => ({ id: `${niche}~${n}`, title, score: 50, why: "", url: null, at, sources: ["hn"], evidence_count: 1, ...hn, ...extra });
  await put(`catalog/${niche}/trends.json`, {
    schema: 1, niche, generated_at: at,
    items: [
      t("aaaaaa01", "US story", { region: "US", region_precision: "exact", regions: ["US"] }),
      t("aaaaaa02", "GB story", { region: "GB", region_precision: "approx", regions: ["GB"] }),
      t("aaaaaa03", "Global story", { region: "global", region_precision: "global", regions: [] }),
      t("aaaaaa04", "US+GB story", { region: "multi", region_precision: "exact", regions: ["GB", "US"] }),
      t("aaaaaa05", "FR+GB story", { region: "multi", region_precision: "exact", regions: ["FR", "GB"] }),
      t("aaaaaa06", "Old export without region fields", {}),
      t("aaaaaa07", "Lowercase tags", { region: "us", region_precision: "exact", regions: ["us"] }),
      t("aaaaaa08", "Garbage region", { region: "United States", region_precision: "bogus", regions: ["x", 3] }),
    ],
  });
  const ev = (n: string, title: string, extra: object) => ({ id: `${niche}~e${n}`, kind: "release", title, date: ymd(5), date_precision: "day", publish_by: ymd(2), url: null, source: "federal_register", source_url: "https://www.federalregister.gov/d/1", license: "Public domain (17 U.S.C. 105)", ...extra });
  await put(`catalog/${niche}/upcoming.json`, {
    schema: 1, niche, generated_at: at,
    items: [ev("000001", "US event", { region: "US", region_precision: "exact" }), ev("000002", "GB event", { region: "GB", region_precision: "exact" }), ev("000003", "Global event", {})],
  });
}

const titles = (r: { items: { title: string }[] }) => r.items.map((i) => i.title).sort();

describe("?region= on REST", () => {
  it("keeps the country + global items, multi items that include it, and treats untagged items as global", async () => {
    await seed();
    const k = await mkKey();
    const all = await json(await call(`/v1/niches/${niche}/trends`, { key: k.key }));
    expect(all.region).toBeNull();
    expect(all.items).toHaveLength(8);
    const us = await json(await call(`/v1/niches/${niche}/trends?region=US`, { key: k.key }));
    expect(us.region).toBe("US");
    expect(titles(us)).toEqual(["Garbage region", "Global story", "Lowercase tags", "Old export without region fields", "US story", "US+GB story"]);
    const gb = await json(await call(`/v1/niches/${niche}/trends?region=gb`, { key: k.key })); // case-insensitive
    expect(gb.region).toBe("GB");
    expect(titles(gb)).toEqual(["FR+GB story", "GB story", "Garbage region", "Global story", "Old export without region fields", "US+GB story"]);
    const fr = await json(await call(`/v1/niches/${niche}/trends?region=FR`, { key: k.key }));
    expect(titles(fr)).toEqual(["FR+GB story", "Garbage region", "Global story", "Old export without region fields"]);
    const none = await json(await call(`/v1/niches/${niche}/trends?region=VN`, { key: k.key }));
    expect(titles(none)).toEqual(["Garbage region", "Global story", "Old export without region fields"]);
  });

  it("region=global (or empty) means no filter", async () => {
    await seed();
    const k = await mkKey();
    for (const q of ["region=global", "region=GLOBAL", "region="]) {
      expect((await json(await call(`/v1/niches/${niche}/trends?${q}`, { key: k.key }))).items).toHaveLength(8);
    }
  });

  it("returns normalized region fields on every item", async () => {
    await seed();
    const k = await mkKey();
    const r = await json(await call(`/v1/niches/${niche}/trends`, { key: k.key }));
    const by = Object.fromEntries(r.items.map((i: { title: string }) => [i.title, i]));
    expect(by["US+GB story"]).toMatchObject({ region: "multi", region_precision: "exact", regions: ["GB", "US"] });
    expect(by["Lowercase tags"]).toMatchObject({ region: "US", regions: ["US"] });
    expect(by["Garbage region"]).toMatchObject({ region: "global", region_precision: "global", regions: [] });
    expect(by["Old export without region fields"]).toMatchObject({ region: "global", region_precision: "global", regions: [] });
  });

  it("filters upcoming events the same way", async () => {
    await seed();
    const k = await mkKey();
    const us = await json(await call(`/v1/niches/${niche}/upcoming?region=US&days=30`, { key: k.key }));
    expect(titles(us)).toEqual(["Global event", "US event"]);
    expect(us.items.find((i: { title: string }) => i.title === "US event")).toMatchObject({ region: "US", region_precision: "exact" });
    expect((await json(await call(`/v1/niches/${niche}/upcoming?days=30`, { key: k.key }))).items).toHaveLength(3);
  });

  it("rejects malformed regions with 400", async () => {
    await seed();
    const k = await mkKey();
    for (const q of ["USA", "1", "U", "U-S", "%20%20x"]) {
      const r = await call(`/v1/niches/${niche}/trends?region=${q}`, { key: k.key });
      expect(r.status, q).toBe(400);
      expect((await json(r)).error.code).toBe("invalid_params");
    }
    expect((await call(`/v1/niches/${niche}/upcoming?region=ZZZ`, { key: k.key })).status).toBe(400);
  });

  it("regionKeeps follows the documented rule", () => {
    const item = (region: string) => ({ region, region_precision: "exact" as const });
    expect(regionKeeps(item("US"), [], null)).toBe(true);
    expect(regionKeeps(item("US"), [], "US")).toBe(true);
    expect(regionKeeps(item("GB"), [], "US")).toBe(false);
    expect(regionKeeps(item("global"), [], "US")).toBe(true);
    expect(regionKeeps(item("multi"), ["GB", "US"], "US")).toBe(true);
    expect(regionKeeps(item("multi"), [], "US")).toBe(false);
  });
});

describe("region argument on MCP", () => {
  const META = { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION, "io.modelcontextprotocol/clientCapabilities": {} };
  const callTool = (key: string, name: string, args: object, id = 1) =>
    call("/mcp", {
      key,
      headers: { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/call", "mcp-name": name },
      body: { jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args, _meta: META } },
    });

  it("tools/list advertises an optional region on trends_for_niche and upcoming", async () => {
    const k = await mkKey();
    const r = await json(await call("/mcp", { key: k.key, body: { jsonrpc: "2.0", id: 1, method: "tools/list" } }));
    for (const t of r.result.tools.filter((x: { name: string }) => x.name === "trends_for_niche" || x.name === "upcoming")) {
      expect(t.inputSchema.properties.region.type).toBe("string");
      expect(t.inputSchema.required).toEqual(["niche"]);
    }
  });

  it("trends_for_niche and upcoming filter by region", async () => {
    await seed();
    const k = await mkKey();
    const tr = await json(await callTool(k.key, "trends_for_niche", { niche, region: "FR" }));
    expect(tr.result.isError).toBe(false);
    expect(tr.result.structuredContent.region).toBe("FR");
    expect(titles(tr.result.structuredContent)).toEqual(["FR+GB story", "Garbage region", "Global story", "Old export without region fields"]);
    const up = await json(await callTool(k.key, "upcoming", { niche, region: "gb", days: 30 }, 2));
    expect(titles(up.result.structuredContent)).toEqual(["GB event", "Global event"]);
    const none = await json(await callTool(k.key, "trends_for_niche", { niche, region: "global" }, 3));
    expect(none.result.structuredContent.items).toHaveLength(8);
  });

  it("a bad region is a tool error that says what is valid; legacy clients get the same", async () => {
    await seed();
    const k = await mkKey();
    const bad = await json(await callTool(k.key, "trends_for_niche", { niche, region: "Germany" }));
    expect(bad.result.isError).toBe(true);
    expect(bad.result.content[0].text).toContain("two-letter");
    const num = await json(await callTool(k.key, "upcoming", { niche, region: 5 }, 2));
    expect(num.result.isError).toBe(true);
    const legacy = await json(await call("/mcp", { key: k.key, headers: { "mcp-protocol-version": "2025-06-18" }, body: { jsonrpc: "2.0", id: 3, method: "tools/call", params: { name: "trends_for_niche", arguments: { niche, region: "US" } } } }));
    expect(legacy.result.isError).toBe(false);
    expect(titles(JSON.parse(legacy.result.content[0].text))).toContain("US story");
  });
});

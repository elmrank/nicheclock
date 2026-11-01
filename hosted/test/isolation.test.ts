import { describe, expect, it } from "vitest";
import { MCP_SPEC_VERSION } from "../src/mcp";
import { call, json, mkKey } from "./helpers";
import { put, seedCatalog, trendsDoc, upcomingDoc } from "./fixtures";

/**
 * Custom niches (`custom-*`) belong to the key that asked for them. An internal key may own several; no other key,
 * external or internal, may ever see their ids, names, URLs or data, and asking for one must look exactly like asking
 * for an id that never existed (no existence oracle).
 */

const A = "custom-alpha-example-com";
const B = "custom-bravo-example-org";
const C = "custom-charlie-example-net";
const OWNED = [A, B, C];
const GHOST = "custom-never-existed-example-com";

interface Owner {
  owner: { id: string; key: string };
  itemId: string;
}

async function seedOwner(): Promise<Owner> {
  await seedCatalog();
  const owner = await mkKey("internal");
  await put(`custom/${owner.id}/niches.json`, {
    schema: 1,
    niches: OWNED.map((id) => ({ id, name: `${id.slice(7)} (private)`, url: `https://${id.slice(7).replace(/-(com|org|net)$/, ".$1")}/`, sources: ["hn"], updated_at: new Date().toISOString() })),
  });
  for (const id of OWNED) {
    await put(`custom/${owner.id}/${id}/trends.json`, trendsDoc(id));
    await put(`custom/${owner.id}/${id}/upcoming.json`, upcomingDoc(id));
    await put(`custom/${owner.id}/${id}/evidence.json`, { schema: 1, niche: id, generated_at: new Date().toISOString(), items: { [`${id}~bbbbbb02`]: { title: "x", source: "hn", source_url: "https://news.ycombinator.com/item?id=1", attribution: "HN", evidence: [] } } });
  }
  return { owner, itemId: `${A}~bbbbbb02` };
}

const ids = (r: { niches: { id: string }[] }) => r.niches.map((n) => n.id);
const mentionsOwned = (text: string) => OWNED.some((id) => text.includes(id) || text.includes(id.slice(7)));

describe("custom niches never reach another key", () => {
  it("the owner sees all of its custom niches next to the catalog (sanity)", async () => {
    const { owner } = await seedOwner();
    expect(ids(await json(await call("/v1/niches", { key: owner.key })))).toEqual(["birdwatching", ...OWNED, "github-only"]);
    for (const id of OWNED) expect((await call(`/v1/niches/${id}/trends`, { key: owner.key })).status).toBe(200);
  });

  it("a non-internal key's /v1/niches lists no custom id, name or url of another key", async () => {
    await seedOwner();
    for (const scope of ["external", "internal"] as const) {
      const other = await mkKey(scope);
      const r = await call("/v1/niches", { key: other.key });
      expect(r.status).toBe(200);
      const text = await r.text();
      expect(mentionsOwned(text)).toBe(false);
      expect(ids(JSON.parse(text))).toEqual(scope === "external" ? ["birdwatching"] : ["birdwatching", "github-only"]); // internal keys also see the github-only catalog niche
      expect(JSON.parse(text).niches.some((n: { id: string; custom: boolean }) => n.id.startsWith("custom-") || n.custom)).toBe(false);
    }
  });

  it("trends, upcoming and evidence of a foreign custom id answer exactly like an id that never existed", async () => {
    const { itemId } = await seedOwner();
    const other = await mkKey("external");
    const ghostItem = `${GHOST}~bbbbbb02`;
    const pairs: [string, string][] = [
      [`/v1/niches/${A}/trends`, `/v1/niches/${GHOST}/trends`],
      [`/v1/niches/${A}/trends?days=30&region=US`, `/v1/niches/${GHOST}/trends?days=30&region=US`],
      [`/v1/niches/${B}/upcoming?days=100`, `/v1/niches/${GHOST}/upcoming?days=100`],
      [`/v1/evidence/${itemId}`, `/v1/evidence/${ghostItem}`],
    ];
    for (const [foreign, ghost] of pairs) {
      const a = await call(foreign, { key: other.key });
      const b = await call(ghost, { key: other.key });
      expect(a.status, foreign).toBe(404);
      expect(b.status, ghost).toBe(404);
      // the only difference allowed is the id the caller typed itself
      const mask = (text: string, typed: string) => text.replaceAll(typed, "ID");
      expect(mask(await a.text(), (foreign.split("/")[3] ?? "").split("?")[0] ?? "")).toBe(mask(await b.text(), (ghost.split("/")[3] ?? "").split("?")[0] ?? ""));
    }
  });

  it("an unknown id never lists another key's ids, over REST and over MCP", async () => {
    await seedOwner();
    const other = await mkKey("external");
    const rest = await call("/v1/niches/definitely-unknown/trends", { key: other.key });
    expect(rest.status).toBe(404);
    expect(mentionsOwned(await rest.text())).toBe(false);
    const meta = { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION, "io.modelcontextprotocol/clientInfo": { name: "t", version: "1" }, "io.modelcontextprotocol/clientCapabilities": {} };
    for (const niche of ["definitely-unknown", A, GHOST]) {
      for (const tool of ["trends_for_niche", "upcoming"]) {
        const r = await call("/mcp", {
          key: other.key,
          headers: { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/call", "mcp-name": tool, accept: "application/json, text/event-stream" },
          body: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: tool, arguments: { niche }, _meta: meta } },
        });
        const body = await json(r);
        expect(body.result.isError, `${tool} ${niche}`).toBe(true);
        const text: string = body.result.content[0].text;
        expect(text).toContain("Valid niche ids: birdwatching");
        expect(text.replaceAll(niche, "").includes("custom-")).toBe(false); // the only custom text is the id the caller typed
        if (niche !== A) expect(mentionsOwned(text)).toBe(false);
      }
    }
  });

  it("the owner's job is invisible to other keys (REST)", async () => {
    const { owner } = await seedOwner();
    const other = await mkKey("external");
    const posted = await json(await call("/v1/jobs", { key: owner.key, body: { url: "https://alpha.example.com/" } }));
    expect((await call(`/v1/jobs/${posted.job.id}`, { key: other.key })).status).toBe(404);
    const again = await call("/v1/jobs", { key: other.key, body: { url: "https://alpha.example.com/" } });
    expect(again.status).toBe(202); // its own job, not the owner's: created, not "already queued"
    expect((await json(again)).job.id).not.toBe(posted.job.id);
  });

  it("ids that try to reach another key's prefix are rejected, not resolved", async () => {
    const { owner } = await seedOwner();
    const other = await mkKey("external");
    for (const bad of [`custom-x%2F..%2F..%2F${owner.id}%2F${A}`, `..%2Fcustom%2F${owner.id}%2F${A}`, `${owner.id}`, `custom-alpha-example-com%00`]) {
      const r = await call(`/v1/niches/${bad}/trends`, { key: other.key });
      expect([400, 404], bad).toContain(r.status);
      expect(mentionsOwned(await r.text()), bad).toBe(false);
    }
  });

  it("a catalog entry cannot claim a custom- id and shadow or expose one", async () => {
    const { owner } = await seedOwner();
    await put("catalog/niches.json", {
      schema: 1,
      niches: [
        { id: "birdwatching", name: "birdwatching", sources: ["hn"], updated_at: new Date().toISOString() },
        { id: C, name: "forged catalog entry", sources: ["hn"], updated_at: new Date().toISOString() },
      ],
    });
    await put(`catalog/${C}/trends.json`, trendsDoc(C));
    const other = await mkKey("external");
    expect(ids(await json(await call("/v1/niches", { key: other.key })))).toEqual(["birdwatching"]);
    expect((await call(`/v1/niches/${C}/trends`, { key: other.key })).status).toBe(404); // custom ids never read catalog/
    expect(ids(await json(await call("/v1/niches", { key: owner.key })))).toEqual(["birdwatching", ...OWNED]); // the owner's own entry wins
  });
});

import { describe, expect, it } from "vitest";
import { MCP_SPEC_VERSION, decodeHeaderValue } from "../src/mcp";
import { call, json, mkKey } from "./helpers";
import { seedCatalog } from "./fixtures";

const META = {
  "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION,
  "io.modelcontextprotocol/clientInfo": { name: "test", version: "1" },
  "io.modelcontextprotocol/clientCapabilities": {},
};

function modern(key: string, method: string, params: Record<string, unknown> = {}, headers: Record<string, string> = {}, id: number = 1) {
  const h: Record<string, string> = {
    "mcp-protocol-version": MCP_SPEC_VERSION,
    "mcp-method": method,
    accept: "application/json, text/event-stream",
    ...headers,
  };
  if (method === "tools/call" && !("mcp-name" in headers)) h["mcp-name"] = String(params.name);
  return call("/mcp", { key, body: { jsonrpc: "2.0", id, method, params: { ...params, _meta: META } }, headers: h });
}

function legacy(key: string, method: string, params: unknown = {}, headers: Record<string, string> = {}, id: number | null = 1) {
  const body: Record<string, unknown> = { jsonrpc: "2.0", method, params };
  if (id !== null) body.id = id;
  return call("/mcp", { key, body, headers });
}

describe("MCP transport", () => {
  it("requires the same Bearer key", async () => {
    const r = await call("/mcp", { body: { jsonrpc: "2.0", id: 1, method: "tools/list" } });
    expect(r.status).toBe(401);
  });

  it("answers GET and DELETE with 405 (stateless: no stream, no session)", async () => {
    const k = await mkKey();
    for (const method of ["GET", "DELETE"]) {
      const r = await call("/mcp", { key: k.key, method });
      expect(r.status).toBe(405);
      expect(r.headers.get("allow")).toBe("POST");
    }
  });

  it("rejects a foreign Origin with 403 and allows our own and header-less clients", async () => {
    const k = await mkKey();
    const body = { jsonrpc: "2.0", id: 1, method: "ping" };
    expect((await call("/mcp", { key: k.key, body, headers: { origin: "https://evil.example" } })).status).toBe(403);
    expect((await call("/mcp", { key: k.key, body, headers: { origin: "https://nicheclock.elmrank.com" } })).status).toBe(200);
    expect((await call("/mcp", { key: k.key, body, env: { ALLOWED_ORIGINS: "https://inspector.example" }, headers: { origin: "https://inspector.example" } })).status).toBe(200);
  });

  it("rejects bad JSON (-32700) and batches (-32600)", async () => {
    const k = await mkKey();
    const bad = await call("/mcp", { key: k.key, body: "{nope", headers: { "content-type": "application/json" } });
    expect(bad.status).toBe(400);
    expect((await json(bad)).error.code).toBe(-32700);
    const batch = await call("/mcp", { key: k.key, body: [{ jsonrpc: "2.0", id: 1, method: "ping" }] });
    expect(batch.status).toBe(400);
    expect((await json(batch)).error.code).toBe(-32600);
  });

  it("meters MCP calls per method", async () => {
    const k = await mkKey();
    await modern(k.key, "tools/list");
    await legacy(k.key, "tools/list");
    const { env } = await import("cloudflare:workers");
    const rows = (await env.DB.prepare("SELECT endpoint, requests FROM usage_daily WHERE key_id = ?").bind(k.id).all<{ endpoint: string; requests: number }>()).results;
    expect(rows).toEqual([{ endpoint: "POST /mcp tools/list", requests: 2 }]);
  });
});

describe(`MCP ${MCP_SPEC_VERSION} (stateless, per-request _meta)`, () => {
  it("server/discover lists versions and capabilities", async () => {
    const k = await mkKey();
    const r = await json(await modern(k.key, "server/discover"));
    expect(r.result).toMatchObject({ resultType: "complete", capabilities: { tools: {} } });
    expect(r.result.supportedVersions[0]).toBe(MCP_SPEC_VERSION);
    expect(r.result._meta["io.modelcontextprotocol/serverInfo"].name).toBe("nicheclock-hosted");
  });

  it("tools/list returns the four tools with schemas, in a stable order", async () => {
    const k = await mkKey();
    const res = await modern(k.key, "tools/list");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    const r = await json(res);
    expect(r.jsonrpc).toBe("2.0");
    expect(r.id).toBe(1);
    expect(r.result.resultType).toBe("complete");
    expect(r.result.ttlMs).toBeGreaterThan(0);
    expect(r.result.cacheScope).toBeTruthy();
    expect(r.result.tools.map((t: { name: string }) => t.name)).toEqual(["trends_for_niche", "upcoming", "niche_from_url", "github_rising"]);
    for (const t of r.result.tools) {
      expect(t.inputSchema.type).toBe("object");
      expect(t.description.length).toBeGreaterThan(20);
    }
  });

  it("tools/call trends_for_niche returns filtered content (same allow-list as REST)", async () => {
    await seedCatalog();
    const k = await mkKey("external");
    const r = await json(await modern(k.key, "tools/call", { name: "trends_for_niche", arguments: { niche: "birdwatching", days: 7 } }));
    expect(r.result.isError).toBe(false);
    expect(r.result.resultType).toBe("complete");
    const data = JSON.parse(r.result.content[0].text);
    expect(data.items.map((i: { source: string }) => i.source).sort()).toEqual(["gdelt", "hn", "wikimedia_pageviews"]);
    expect(r.result.structuredContent.items).toHaveLength(3);
    expect(JSON.stringify(r)).not.toMatch(/google_trends|apple/i);
  });

  it("tools/call upcoming works and honours scope", async () => {
    await seedCatalog();
    const ext = await json(await modern((await mkKey("external")).key, "tools/call", { name: "upcoming", arguments: { niche: "birdwatching", days: 30 } }));
    expect(JSON.parse(ext.result.content[0].text).items.map((i: { title: string }) => i.title)).toEqual(["Rule comment deadline"]);
    const int = await json(await modern((await mkKey("internal")).key, "tools/call", { name: "upcoming", arguments: { niche: "birdwatching", days: 30 } }));
    expect(JSON.parse(int.result.content[0].text).items.length).toBe(3);
  });

  it("unknown niche is a tool execution error that lists valid ids; bad days too", async () => {
    await seedCatalog();
    const k = await mkKey();
    const r = await json(await modern(k.key, "tools/call", { name: "trends_for_niche", arguments: { niche: "nope" } }));
    expect(r.result.isError).toBe(true);
    expect(r.result.content[0].text).toContain("birdwatching");
    const d = await json(await modern(k.key, "tools/call", { name: "upcoming", arguments: { niche: "birdwatching", days: 0 } }));
    expect(d.result.isError).toBe(true);
  });

  it("unknown tool is a JSON-RPC -32602 error", async () => {
    const k = await mkKey();
    const r = await json(await modern(k.key, "tools/call", { name: "nope", arguments: {} }));
    expect(r.error.code).toBe(-32602);
  });

  it("niche_from_url queues a job; the same url returns the existing one", async () => {
    const k = await mkKey();
    const first = await json(await modern(k.key, "tools/call", { name: "niche_from_url", arguments: { url: "https://www.Example-Site.com/blog?x=1#top" } }));
    expect(first.result.isError).toBe(false);
    const d1 = first.result.structuredContent;
    expect(d1.created).toBe(true);
    expect(d1.job).toMatchObject({ status: "queued", niche_id: "custom-example-site-com" });
    const again = await json(await modern(k.key, "tools/call", { name: "niche_from_url", arguments: { url: "https://example-site.com/" } }, {}, 2));
    expect(again.result.structuredContent.created).toBe(false);
    expect(again.result.structuredContent.job.id).toBe(d1.job.id);
    const bad = await json(await modern(k.key, "tools/call", { name: "niche_from_url", arguments: { url: "http://127.0.0.1/" } }, {}, 3));
    expect(bad.result.isError).toBe(true);
    expect(bad.result.content[0].text).toContain("invalid_url");
  });

  it("validates headers against the body: 400 -32020", async () => {
    const k = await mkKey();
    const noMethod = await call("/mcp", { key: k.key, headers: { "mcp-protocol-version": MCP_SPEC_VERSION }, body: { jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: META } } });
    expect(noMethod.status).toBe(400);
    expect((await json(noMethod)).error.code).toBe(-32020);
    const wrongMethod = await modern(k.key, "tools/list", {}, { "mcp-method": "tools/call" });
    expect((await json(wrongMethod)).error.code).toBe(-32020);
    const wrongName = await modern(k.key, "tools/call", { name: "upcoming", arguments: {} }, { "mcp-name": "trends_for_niche" });
    expect(wrongName.status).toBe(400);
    expect((await json(wrongName)).error.code).toBe(-32020);
    const versionMismatch = await modern(k.key, "tools/list", {}, { "mcp-protocol-version": "2025-11-25" });
    expect((await json(versionMismatch)).error.code).toBe(-32020);
    // Base64 sentinel encoding of Mcp-Name is decoded before comparing
    const enc = `=?base64?${btoa("upcoming")}?=`;
    const okEnc = await modern(k.key, "tools/call", { name: "upcoming", arguments: { niche: "x" } }, { "mcp-name": enc });
    expect(okEnc.status).toBe(200);
    expect(decodeHeaderValue(enc)).toBe("upcoming");
  });

  it("missing capabilities -> -32602 400; unsupported version -> -32022 400 with the supported list", async () => {
    const k = await mkKey();
    const headers = { "mcp-protocol-version": MCP_SPEC_VERSION, "mcp-method": "tools/list" };
    const noCaps = await call("/mcp", { key: k.key, headers, body: { jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: { "io.modelcontextprotocol/protocolVersion": MCP_SPEC_VERSION } } } });
    expect(noCaps.status).toBe(400);
    expect((await json(noCaps)).error.code).toBe(-32602);
    const future = await call("/mcp", {
      key: k.key,
      headers: { "mcp-protocol-version": "2999-01-01", "mcp-method": "tools/list" },
      body: { jsonrpc: "2.0", id: 1, method: "tools/list", params: { _meta: { ...META, "io.modelcontextprotocol/protocolVersion": "2999-01-01" } } },
    });
    expect(future.status).toBe(400);
    const err = (await json(future)).error;
    expect(err.code).toBe(-32022);
    expect(err.data.supported).toContain(MCP_SPEC_VERSION);
    expect(err.data.requested).toBe("2999-01-01");
    const headerOnly = await call("/mcp", { key: k.key, headers: { "mcp-protocol-version": MCP_SPEC_VERSION }, body: { jsonrpc: "2.0", id: 1, method: "tools/list" } });
    expect(headerOnly.status).toBe(400);
  });

  it("unknown method -> 404 + -32601", async () => {
    const k = await mkKey();
    const r = await modern(k.key, "resources/list");
    expect(r.status).toBe(404);
    expect((await json(r)).error.code).toBe(-32601);
  });
});

describe("MCP earlier revisions (initialize handshake, still stateless)", () => {
  it("initialize negotiates a version, issues no session id; initialized is accepted with 202", async () => {
    const k = await mkKey();
    const init = await legacy(k.key, "initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "c", version: "1" } });
    expect(init.status).toBe(200);
    expect(init.headers.get("mcp-session-id")).toBeNull();
    const r = await json(init);
    expect(r.result.protocolVersion).toBe("2025-06-18");
    expect(r.result.capabilities.tools).toBeDefined();
    expect(r.result.serverInfo.name).toBe("nicheclock-hosted");
    const unknown = await json(await legacy(k.key, "initialize", { protocolVersion: "1999-01-01" }));
    expect(unknown.result.protocolVersion).toBe("2025-11-25");
    const note = await legacy(k.key, "notifications/initialized", {}, { "mcp-protocol-version": "2025-06-18" }, null);
    expect(note.status).toBe(202);
    expect(await note.text()).toBe("");
  });

  it("tools/list and tools/call work with the legacy shapes", async () => {
    await seedCatalog();
    const k = await mkKey();
    const h = { "mcp-protocol-version": "2025-06-18" };
    const list = await json(await legacy(k.key, "tools/list", {}, h));
    expect(list.result.tools.map((t: { name: string }) => t.name)).toEqual(["trends_for_niche", "upcoming", "niche_from_url", "github_rising"]);
    expect(list.result.resultType).toBeUndefined();
    const call1 = await json(await legacy(k.key, "tools/call", { name: "trends_for_niche", arguments: { niche: "birdwatching" } }, h, 2));
    expect(call1.result.isError).toBe(false);
    expect(JSON.parse(call1.result.content[0].text).items).toHaveLength(3);
    const ping = await json(await legacy(k.key, "ping", {}, h, 3));
    expect(ping.result).toEqual({});
    const nope = await json(await legacy(k.key, "resources/list", {}, h, 4));
    expect(nope.error.code).toBe(-32601);
  });

  it("an unsupported MCP-Protocol-Version header is refused", async () => {
    const k = await mkKey();
    const r = await legacy(k.key, "tools/list", {}, { "mcp-protocol-version": "1999-01-01" });
    expect(r.status).toBe(400);
    expect((await json(r)).error.code).toBe(-32022);
  });
});

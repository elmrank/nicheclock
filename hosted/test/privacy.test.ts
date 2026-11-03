import { afterEach, describe, expect, it, vi } from "vitest";
import { clearShardCache } from "../src/shards";
import { assets } from "./assets";
import { call, db, mkKey } from "./helpers";

/** Condition (b): no logging of URLs, words, handles or profiles anywhere in these handlers. */
const src = import.meta.glob("../src/*.ts", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const HANDLERS = ["suggest.ts", "suggest-site.ts", "suggest-youtube.ts", "suggest-resolve.ts", "suggest-net.ts", "suggest-limits.ts", "profile-tools.ts", "shards.ts", "robotstxt.ts"];

afterEach(() => vi.restoreAllMocks());

describe("no console in the handlers of the three buttons and the profile tools", () => {
  it("greps the sources", () => {
    for (const f of HANDLERS) {
      const text = src[`../src/${f}`];
      expect(text, f).toBeDefined();
      expect(text, f).not.toMatch(/\bconsole\s*[.[]/);
    }
    // every suggest*.ts file is in the list above (a new file cannot slip past the check)
    const suggest = Object.keys(src).map((k) => k.replace("../src/", "")).filter((k) => k.startsWith("suggest"));
    for (const f of suggest) expect(HANDLERS, f).toContain(f);
  });
});

const S = { site: "https://zzsentinel-host-7731.example.org", word: "zzsentinelword", handle: "@zzsentinelhandle", profile: "zzsentinelprofile" };
const ALL = [S.site, "zzsentinel-host-7731", S.word, S.handle, S.profile];
const env = { ASSETS: assets, BURST: undefined, YOUTUBE_API_KEY: "test-key-not-real" };
const hdr = { origin: "https://nicheclock.elmrank.com", "cf-connecting-ip": "203.0.113.200" };

describe("what each handler does with a sentinel", () => {
  it("never calls console, never echoes the input, never stores it", async () => {
    clearShardCache();
    const spies = (["log", "info", "warn", "error", "debug", "trace"] as const).map((m) => vi.spyOn(console, m));
    const outgoing: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      outgoing.push(`${String(input)} ${JSON.stringify((init as RequestInit | undefined)?.headers ?? {})}`);
      throw new Error(`zzsentinel network ${String(input)}`);       // an error text that holds the input: it must not get out
    });
    const bodies: string[] = [];
    const run = async (path: string, body: object, e: object = env) => {
      const r = await call(path, { body, headers: hdr, env: e });
      const text = await r.text();
      bodies.push(text);
      return r.status;
    };
    await run("/v1/resolve", { text: S.word });
    await run("/v1/suggest/site", { url: S.site });
    await run("/v1/suggest/site", { url: "http://127.0.0.1/" + S.word });
    await run("/v1/suggest/youtube", { channel: S.handle });
    await run("/v1/suggest/youtube", { channel: "https://youtube.com/" + S.handle });
    await run("/v1/resolve", "{ not json " + S.word as unknown as object);
    const k = await mkKey();
    for (const path of ["/v1/profile/trends", "/v1/profile/upcoming", "/v1/profile/resolve"]) {
      const r = await call(path, { key: k.key, body: { profile: { name: S.profile, topics: [{ text: S.word }], region: "US", sites: [{ url: S.site }], channels: [{ platform: "youtube", handle: S.handle }] }, text: S.word }, env });
      bodies.push(await r.text());
    }
    for (const s of spies) expect(s).not.toHaveBeenCalled();
    // the replies of the three buttons never hold their own input (profile tools may say which of the caller's own texts they could not find)
    for (const b of bodies.slice(0, 6)) for (const w of ALL) expect(b).not.toContain(w);
    // nothing is stored: the two tables hold no sentinel, and no row that could be read back as one
    const rate = await db().prepare("SELECT bucket FROM suggest_rate").all<{ bucket: string }>();
    expect(rate.results.length).toBeGreaterThan(0);
    for (const row of rate.results) {
      expect(row.bucket).toMatch(/^(resolve|site|youtube|host):[0-9a-f]{16}$/);
      for (const w of ALL) expect(row.bucket).not.toContain(w);
    }
    // the fetches that left are the ones the feature needs; the profile and the other buttons' inputs are in none of them
    const toWikidata = outgoing.filter((o) => o.includes("wikidata.org"));
    for (const o of toWikidata) {
      expect(o).not.toContain(S.site);
      expect(o).not.toContain(S.handle);
      expect(o).not.toContain(S.profile);
    }
    const toSite = outgoing.filter((o) => o.includes("example.org"));
    for (const o of toSite) {
      expect(o).not.toContain(S.word);
      expect(o).not.toContain(S.handle);
    }
  });

  it("answers an unexpected failure with a fixed 500 and no text of the input", async () => {
    const spies = (["log", "error", "warn"] as const).map((m) => vi.spyOn(console, m));
    const BURST = { limit: async () => { throw new Error(`zzsentinel ${S.word}`); } } as unknown as RateLimit;
    const r = await call("/v1/resolve", { body: { text: S.word }, headers: hdr, env: { ASSETS: assets, BURST } });
    expect(r.status).toBe(500);
    const text = await r.text();
    expect(text).not.toContain("zzsentinel");
    expect(JSON.parse(text).error.code).toBe("internal");
    for (const s of spies) expect(s).not.toHaveBeenCalled();
  });
});

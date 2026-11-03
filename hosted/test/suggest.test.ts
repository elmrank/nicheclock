import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { extractPhrases, cleanTitles, slugWords } from "../site/trending/phrases.js";
import { isAllowed, parseRobots, patternMatches, rulesFor } from "../src/robotstxt";
import { clearShardCache } from "../src/shards";
import { checkTarget, privateIpv4, privateIpv6, USER_AGENT, fetchLimited, newBudget } from "../src/suggest-net";
import { locAt, locOffsets, pageTitle, pickPages, sampleEvenly, sitemapLocs } from "../src/suggest-site";
import { parseChannel } from "../src/suggest-youtube";
import { assets } from "./assets";
import { call, db, json } from "./helpers";

beforeEach(async () => {
  clearShardCache();
  await db().batch([db().prepare("DELETE FROM suggest_rate"), db().prepare("DELETE FROM youtube_units_daily")]);
});
afterEach(() => vi.restoreAllMocks());
const noBurst = { ASSETS: assets, BURST: undefined };
let ipN = 0;
const ip = () => ({ "cf-connecting-ip": `203.0.113.${(ipN = (ipN % 250) + 1)}` });

describe("target check (what Read my site may reach)", () => {
  it("accepts public http(s) host names", () => {
    expect(checkTarget("example.org").origin).toBe("https://example.org");
    expect(checkTarget("https://www.example.org/blog/x?y=1#z").host).toBe("www.example.org");
    expect(checkTarget("http://example.org").origin).toBe("http://example.org");
    expect(checkTarget(" https://example.org:443/ ").host).toBe("example.org");
  });

  it("refuses IP literals in every notation, private ranges and internal names", () => {
    const refused = [
      "http://127.0.0.1", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1", "169.254.169.254", "100.64.0.1", "100.127.255.255", "0.0.0.0", "224.0.0.1",
      "http://[::1]/", "http://[fd00::1]/", "http://[fe80::1]/", "http://[::ffff:127.0.0.1]/", "http://[2606:4700::1111]/", "http://8.8.8.8/",
      "http://0x7f.1/", "http://2130706433/", "http://017700000001/", "http://127.1/",
      "localhost", "http://localhost:80", "intranet", "printer.local", "db.internal", "metadata.google.internal", "router.home.arpa", "x.localhost", "site.test", "a.lan", "x.corp",
      "127.0.0.1.nip.io", "10.0.0.1.sslip.io", "foo.localtest.me",
      "ftp://example.org", "file:///etc/passwd", "javascript:alert(1)", "https://user:pw@example.org", "https://example.org:8080", "", "   ", "not a url at all", "https://exa mple.org",
    ];
    for (const r of refused) expect(() => checkTarget(r), r).toThrow();
    expect(() => checkTarget(123)).toThrow();
    expect(() => checkTarget("a".repeat(3000) + ".org")).toThrow();
  });

  it("names the range of an IP literal", () => {
    expect(privateIpv4("10.0.0.1")).toBe("private");
    expect(privateIpv4("127.5.5.5")).toBe("loopback");
    expect(privateIpv4("169.254.1.1")).toBe("link-local");
    expect(privateIpv4("100.64.1.1")).toBe("cgnat");
    expect(privateIpv4("100.63.1.1")).toBe("public");
    expect(privateIpv4("172.15.0.1")).toBe("public");
    expect(privateIpv4("172.32.0.1")).toBe("public");
    expect(privateIpv4("example.org")).toBeNull();
    expect(privateIpv6("[::1]")).toBe("loopback");
    expect(privateIpv6("[fd12::1]")).toBe("private");
    expect(privateIpv6("[fe80::1]")).toBe("link-local");
    expect(privateIpv6("[::ffff:10.0.0.1]")).toBe("private");
    expect(privateIpv6("[2606:4700::1]")).toBe("public");
  });

  it("never puts the input in the error message", () => {
    for (const bad of ["http://127.0.0.1/zzsentinel", "zzsentinel.local", "https://zzsentinel:pw@example.org"]) {
      try {
        checkTarget(bad);
        throw new Error("did not throw");
      } catch (e) {
        expect(String((e as Error).message)).not.toMatch(/zzsentinel|127\.0\.0\.1/);
      }
    }
  });
});

describe("robots.txt (RFC 9309)", () => {
  const rules = (txt: string, token = "nicheclock-suggest") => rulesFor(parseRobots(txt), token);

  it("takes the group that names the bot, else *, and merges groups of the same name", () => {
    const t = "User-agent: *\nDisallow: /\n\nUser-agent: nicheclock-suggest\nDisallow: /private/\n\nUser-agent: NicheClock-Suggest\nDisallow: /tmp/\n";
    const r = rules(t);
    expect(isAllowed(r, "/blog/post")).toBe(true);
    expect(isAllowed(r, "/private/x")).toBe(false);
    expect(isAllowed(r, "/tmp/x")).toBe(false);
    expect(isAllowed(rules(t, "otherbot"), "/blog/post")).toBe(false);
    expect(isAllowed(rules("User-agent: nicheclock\nDisallow: /\n"), "/x")).toBe(false);
  });

  it("longest match wins and Allow wins a tie; * and $ work; empty Disallow allows", () => {
    const r = rules("User-agent: *\nDisallow: /a/\nAllow: /a/b/\nDisallow: /*.pdf$\nDisallow: /tie\nAllow: /tie\nDisallow:\n");
    expect(isAllowed(r, "/a/x")).toBe(false);
    expect(isAllowed(r, "/a/b/x")).toBe(true);
    expect(isAllowed(r, "/doc.pdf")).toBe(false);
    expect(isAllowed(r, "/doc.pdf?x=1")).toBe(true);
    expect(isAllowed(r, "/tie")).toBe(true);
    expect(isAllowed(rules("User-agent: *\nDisallow:\n"), "/anything")).toBe(true);
    expect(isAllowed(rules("User-agent: *\nDisallow: /\n"), "/robots.txt")).toBe(true);
    expect(patternMatches("/a*b*c$", "/aXXbYYc")).toBe(true);
    expect(patternMatches("/a*b*c$", "/aXXbYYcZ")).toBe(false);
    expect(patternMatches("/p%7e", "/p%7E")).toBe(true);
  });

  it("reads comments, a BOM, odd spacing and Sitemap lines", () => {
    const r = parseRobots("\uFEFFUser-Agent : *  # all\r\nDISALLOW: /x # no\r\nSitemap: https://example.org/s.xml\nnonsense\n");
    expect(isAllowed(rulesFor(r, "nicheclock-suggest"), "/x")).toBe(false);
    expect(r.sitemaps).toEqual(["https://example.org/s.xml"]);
  });
});

describe("phrases", () => {
  it("finds the topic words of titles, drops the site name and keeps the longer phrase", () => {
    const titles = ["Sourdough starter feeding schedule | Example Bakery", "How to bake sourdough bread | Example Bakery", "Sourdough starter troubleshooting | Example Bakery", "Rye sourdough bread recipe | Example Bakery"];
    const cleaned = cleanTitles(titles);
    expect(cleaned.every((t: string) => !/example bakery/i.test(t))).toBe(true);
    const { phrases, of } = extractPhrases(cleaned.map((text: string) => ({ text })));
    const texts = phrases.map((p: { text: string }) => p.text);
    expect(of).toBe(4);
    expect(texts).toContain("sourdough starter");
    expect(texts).toContain("sourdough bread");
    expect(texts).not.toContain("example");
    expect(texts).not.toContain("how");
  });

  it("counts a tag double and reads words from an address", () => {
    const { phrases } = extractPhrases([{ text: "Opening ideas", tags: ["chess"] }, { text: "Endgame basics", tags: [] }]);
    expect(phrases.find((p: { text: string }) => p.text === "chess")?.with).toBe(2);
    expect(slugWords("https://example.org/blog/2026/11/backyard-bird-feeders.html")).toBe("backyard bird feeders");
    expect(slugWords("https://example.org/posts/12345")).toBe("");
  });
});

describe("site pieces", () => {
  it("reads locs (CDATA, entities), titles and samples evenly", () => {
    expect(sitemapLocs("<urlset><url><loc> https://example.org/a?x=1&amp;y=2 </loc></url><url><loc><![CDATA[https://example.org/b]]></loc></url></urlset>")).toEqual(["https://example.org/a?x=1&y=2", "https://example.org/b"]);
    expect(pageTitle("<html><head><title>  Backyard &amp; birds\n</title></head>")).toBe("Backyard & birds");
    expect(pageTitle("<body><h1>Only <b>h1</b></h1>")).toBe("Only h1");
    expect(sampleEvenly([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 5)).toEqual([1, 3, 5, 7, 9]);
    expect(sampleEvenly([1, 2], 5)).toEqual([1, 2]);
  });

  it("picks 40 pages evenly without parsing every address, stepping past tag, category and file addresses", () => {
    const loc = (p: string) => `<url><loc>https://www.example.org${p}</loc></url>`;
    const xml = `<urlset>${Array.from({ length: 400 }, (_v, i) => (i % 10 === 0 ? loc(`/tag/topic-${i}/`) : i % 7 === 0 ? loc(`/files/doc-${i}.pdf`) : i % 11 === 0 ? `<url><loc>https://elsewhere.example.net/x-${i}</loc></url>` : loc(`/posts/bird-feeders-${i}`))).join("")}</urlset>`;
    const offs = locOffsets(xml);
    expect(offs.length / 2).toBe(400);
    expect(locAt(xml, offs, 1)).toBe("https://www.example.org/posts/bird-feeders-1");
    const pages = pickPages(xml, offs, "example.org");
    expect(pages.length).toBe(40);
    expect(new Set(pages).size).toBe(40);
    for (const p of pages) expect(p).toMatch(/^https:\/\/www\.example\.org\/posts\/bird-feeders-\d+$/);
    expect(pickPages(xml, offs, "other.example.org")).toEqual([]);
    expect(pickPages("<urlset></urlset>", locOffsets("<urlset></urlset>"), "example.org")).toEqual([]);
  });
});

/* ---------- Read my site with a fake network ---------- */

interface Site {
  robots?: { status: number; body?: string; location?: string };
  sitemap?: string | Uint8Array;
  sitemapPath?: string;
  pages?: Record<string, { status?: number; body?: string; location?: string; type?: string }>;
  host?: string;
}
const HOST = "example.org";
const calls: { url: string; ua: string | null; redirect: string | undefined }[] = [];

function urlset(n: number, topic = (i: number) => (i % 2 ? `sourdough-starter-feeding-${i}` : `backyard-bird-feeders-${i}`)): string {
  return `<?xml version="1.0"?><urlset>${Array.from({ length: n }, (_v, i) => `<url><loc>https://${HOST}/posts/${topic(i)}</loc></url>`).join("")}</urlset>`;
}

function fakeNet(site: Site) {
  calls.length = 0;
  const robots = site.robots ?? { status: 404 };
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const headers = new Headers((init as RequestInit | undefined)?.headers);
    calls.push({ url, ua: headers.get("user-agent"), redirect: (init as RequestInit | undefined)?.redirect });
    const u = new URL(url);
    if (u.pathname === "/robots.txt") {
      const redirected = u.searchParams.has("v");      // a robots.txt that redirects once: the second request answers 200
      if (robots.location && redirected) return new Response("", { status: 200 });
      return new Response(robots.body ?? "", { status: robots.status, headers: robots.location ? { location: robots.location } : {} });
    }
    if (u.pathname === (site.sitemapPath ?? "/sitemap.xml") && site.sitemap !== undefined) return new Response(site.sitemap as BodyInit, { status: 200, headers: { "content-type": "application/xml" } });
    const p = site.pages?.[u.pathname];
    if (p) {
      const h: Record<string, string> = { "content-type": p.type ?? "text/html; charset=utf-8" };
      if (p.location) h.location = p.location;
      return new Response(p.body ?? `<html><head><title>${decodeURIComponent(u.pathname.split("/").pop() ?? "").replace(/-/g, " ")} | Example Site</title></head></html>`, { status: p.status ?? 200, headers: h });
    }
    if (u.pathname.startsWith("/posts/")) {
      return new Response(`<html><head><title>${decodeURIComponent(u.pathname.split("/").pop() ?? "").replace(/-/g, " ")} | Example Site</title></head></html>`, { status: 200, headers: { "content-type": "text/html" } });
    }
    return new Response("not found", { status: 404 });
  });
}

/** Each read starts with empty counters, except the tests of the limits themselves (keep = true). */
const read = async (url: unknown, extra: Record<string, string> = {}, env: object = noBurst, keep = false) => {
  if (!keep) await db().prepare("DELETE FROM suggest_rate").run();
  return readRaw(url, extra, env);
};
const readRaw = (url: unknown, extra: Record<string, string> = {}, env: object = noBurst) =>
  call("/v1/suggest/site", { body: { url }, headers: { origin: "https://nicheclock.elmrank.com", ...ip(), ...extra }, env });

describe("POST /v1/suggest/site", () => {
  it("reads robots.txt, one sitemap and at most 8 titles, and returns phrases only", async () => {
    fakeNet({ sitemap: urlset(120) });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(200);
    const out = await json(r);
    expect(out.ok).toBe(true);
    expect(out.pages_in_sitemap).toBe(120);
    expect(out.pages_read).toBe(40);
    expect(out.titles_read).toBe(8);
    const texts = out.phrases.map((p: { text: string }) => p.text);
    expect(texts).toContain("backyard bird feeders");
    expect(texts).toContain("sourdough starter feeding");
    expect(JSON.stringify(out)).not.toMatch(/example\.org|<html|https?:/);
    expect(calls.length).toBe(1 + 1 + 8);
    expect(calls.length).toBeLessThanOrEqual(26);
    expect(calls[0]?.url).toBe(`https://${HOST}/robots.txt`);
    for (const c of calls) {
      expect(c.ua).toBe(USER_AGENT);
      expect(c.redirect).toBe("manual");
      expect(new URL(c.url).host).toBe(HOST);
    }
    expect(USER_AGENT).toBe("nicheclock-suggest/1.0 (+https://nicheclock.elmrank.com/bot; oss@elmrank.com)");
  });

  it("refuses when robots.txt is 5xx or unreachable, and fetches nothing else", async () => {
    for (const status of [500, 503]) {
      fakeNet({ robots: { status }, sitemap: urlset(30) });
      const r = await read(`https://${HOST}`);
      expect(r.status).toBe(502);
      expect((await json(r)).error.code).toBe("robots_unreadable");
      expect(calls.length).toBe(1);
    }
    calls.length = 0;
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(502);
  });

  it("treats 4xx as 'no rules' and 401/403 as a refusal", async () => {
    fakeNet({ robots: { status: 404 }, sitemap: urlset(30) });
    expect((await read(`https://${HOST}`)).status).toBe(200);
    fakeNet({ robots: { status: 403 }, sitemap: urlset(30) });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(403);
    expect(calls.length).toBe(1);
  });

  it("stops when robots.txt disallows the bot, by name or by *", async () => {
    for (const body of ["User-agent: *\nDisallow: /\n", "User-agent: nicheclock-suggest\nDisallow: /\n\nUser-agent: *\nAllow: /\n"]) {
      fakeNet({ robots: { status: 200, body }, sitemap: urlset(30) });
      const r = await read(`https://${HOST}`);
      expect(r.status).toBe(403);
      expect((await json(r)).error.code).toBe("robots_blocked");
      expect(calls.length).toBe(1);
    }
  });

  it("does not fetch a page robots.txt disallows, and uses the Sitemap line", async () => {
    fakeNet({ robots: { status: 200, body: `User-agent: *\nDisallow: /posts/backyard\nSitemap: https://${HOST}/maps/all.xml\n` }, sitemap: urlset(60), sitemapPath: "/maps/all.xml" });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(200);
    expect(calls.some((c) => c.url.includes("/maps/all.xml"))).toBe(true);
    expect(calls.some((c) => c.url.includes("/posts/backyard"))).toBe(false);
  });

  it("follows one same-host redirect and refuses a second hop or another host", async () => {
    // robots.txt redirects to the same host once: fine
    fakeNet({ robots: { status: 301, location: `https://${HOST}/robots.txt?v=2` }, sitemap: urlset(30) });
    expect((await read(`https://${HOST}`)).status).toBe(200);
    // robots.txt redirects to another host: refused, nothing more is fetched, and the other host is never contacted
    fakeNet({ robots: { status: 302, location: "https://elsewhere.example.net/robots.txt" }, sitemap: urlset(30) });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(502);
    expect(calls.map((c) => new URL(c.url).host)).toEqual([HOST]);
    // a page that redirects twice: that title is skipped, the call still succeeds
    fakeNet({ sitemap: urlset(40), pages: { "/posts/sourdough-starter-feeding-1": { status: 301, location: `https://${HOST}/posts/x-1` }, "/posts/x-1": { status: 301, location: `https://${HOST}/posts/x-2` } } });
    expect((await read(`https://${HOST}`)).status).toBe(200);
    expect(calls.every((c) => new URL(c.url).host === HOST)).toBe(true);
  });

  it("keeps to 26 subrequests even when every fetch is redirected once", async () => {
    fakeNet({ robots: { status: 301, location: `https://${HOST}/robots.txt?v=2` }, sitemap: urlset(80) });
    const spy = vi.spyOn(globalThis, "fetch");
    const base = spy.getMockImplementation() as (i: RequestInfo | URL, o?: RequestInit) => Promise<Response>;
    spy.mockImplementation(async (input, init) => {
      const u = new URL(String(input));
      if (u.pathname.startsWith("/posts/") && !u.searchParams.has("r")) return new Response(null, { status: 301, headers: { location: `${u.origin}${u.pathname}?r=1` } });
      return base(input, init);
    });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(200);
    expect(calls.length).toBeLessThanOrEqual(26);
  });

  it("reads at most 300 KB of the sitemap and 64 KB of a page", async () => {
    const big = urlset(4000, (i) => `${"long-slug-padding-".repeat(10)}sourdough-bread-${i}`);
    expect(big.length).toBeGreaterThan(400_000);
    fakeNet({ sitemap: big });
    const out = await json(await read(`https://${HOST}`));
    expect(out.pages_in_sitemap).toBeGreaterThan(1000);
    expect(out.pages_in_sitemap).toBeLessThan(2000);
    // a body past the cap is cut at the cap, and the rest is never read
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("a".repeat(200_000), { status: 200, headers: { "content-type": "text/html" } }));
    const b = await fetchLimited(newBudget(), new URL("https://example.org/p"), 64 * 1024);
    expect(b.status).toBe(200);
    expect(b.bytes.length).toBe(64 * 1024);
    expect(b.truncated).toBe(true);
  });

  it("follows a sitemap index to one child and reads a gzipped sitemap", async () => {
    const idx = `<sitemapindex><sitemap><loc>https://${HOST}/image-sitemap.xml</loc></sitemap><sitemap><loc>https://${HOST}/post-sitemap.xml</loc></sitemap></sitemapindex>`;
    const n = fakeNet({ sitemap: idx });
    n.mockImplementation(async (input) => {
      const u = new URL(String(input));
      calls.push({ url: String(input), ua: USER_AGENT, redirect: "manual" });
      if (u.pathname === "/robots.txt") return new Response("", { status: 404 });
      if (u.pathname === "/sitemap.xml") return new Response(idx, { status: 200 });
      if (u.pathname === "/post-sitemap.xml") {
        const gz = await new Response(new Blob([urlset(50)]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
        return new Response(gz, { status: 200, headers: { "content-type": "application/gzip" } });
      }
      if (u.pathname.startsWith("/posts/")) return new Response("<title>t</title>", { status: 200, headers: { "content-type": "text/html" } });
      return new Response("", { status: 404 });
    });
    const r = await read(`https://${HOST}`);
    expect(r.status).toBe(200);
    expect(calls.some((c) => c.url.includes("image-sitemap"))).toBe(false);
    expect(calls.some((c) => c.url.includes("post-sitemap"))).toBe(true);
  });

  it("answers a fixed sentence when the site refuses, has no sitemap, or is not allowed", async () => {
    fakeNet({ sitemap: urlset(30), pages: { "/sitemap.xml": { status: 429, type: "text/plain" } } });
    // a 429 on the sitemap ends the call (never retried)
    const spy = vi.spyOn(globalThis, "fetch");
    spy.mockImplementation(async (input) => {
      const u = new URL(String(input));
      calls.push({ url: String(input), ua: null, redirect: undefined });
      return new Response("", { status: u.pathname === "/robots.txt" ? 404 : 429 });
    });
    calls.length = 0;
    const r1 = await read(`https://${HOST}`);
    expect(r1.status).toBe(502);
    expect((await json(r1)).error.code).toBe("site_refused");
    expect(calls.length).toBe(2);
    fakeNet({});
    const r2 = await read(`https://${HOST}`);
    expect(r2.status).toBe(422);
    expect((await json(r2)).error.code).toBe("no_sitemap");
    for (const bad of ["http://127.0.0.1/", "localhost", "https://user:pw@example.org"]) {
      const r = await read(bad);
      expect(r.status).toBe(400);
    }
    expect((await read(`https://${HOST}`, { "content-type": "text/plain" })).status).toBe(415);
    expect((await read(`https://${HOST}`, { origin: "https://evil.example" })).status).toBe(403);
    expect((await call("/v1/suggest/site", { method: "GET", env: noBurst })).status).toBe(405);
  });

  it("limits a caller to 5 reads an hour and a site to one read a minute", async () => {
    fakeNet({ sitemap: urlset(30) });
    const same = { "cf-connecting-ip": "198.51.100.7" };
    const statuses: number[] = [];
    for (let i = 0; i < 7; i++) statuses.push((await read(`https://site${i}.example.org`, same, noBurst, true)).status);
    // site{i}.example.org has no fake sitemap host match, so the read ends in an error, but it is counted: the 6th and 7th are 429
    expect(statuses.slice(5)).toEqual([429, 429]);
    const r = await read(`https://site0.example.org`, { "cf-connecting-ip": "198.51.100.8" }, noBurst, true);
    expect(r.status).toBe(429);
    expect((await json(r)).error.message).toMatch(/read a moment ago/);
    expect(Number(r.headers.get("retry-after"))).toBeGreaterThan(0);
  });

  it("is stopped first by the rate-limiting binding when it says no", async () => {
    fakeNet({ sitemap: urlset(30) });
    const BURST = { limit: vi.fn(async () => ({ success: false })) } as unknown as RateLimit;
    const r = await read(`https://${HOST}`, ip(), { ASSETS: assets, BURST });
    expect(r.status).toBe(429);
    expect(calls.length).toBe(0);
  });
});

/* ---------- Look up a word ---------- */

function fakeWikidata(search: object[], sitelinks: Record<string, boolean>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const u = new URL(String(input));
    calls.push({ url: String(input), ua: null, redirect: undefined });
    if (u.searchParams.get("action") === "wbsearchentities") return Response.json({ search });
    return Response.json({ entities: Object.fromEntries(Object.entries(sitelinks).map(([k, v]) => [k, v ? { sitelinks: { enwiki: { title: "x" } } } : {}])) });
  });
}
const lookup = (text: unknown, extra: Record<string, string> = {}) => call("/v1/resolve", { body: { text }, headers: { origin: "https://nicheclock.elmrank.com", ...ip(), ...extra }, env: noBurst });

describe("POST /v1/resolve", () => {
  it("answers from the concept list first, with no network", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    const out = await json(await lookup("  Chess "));
    expect(out.topic).toMatchObject({ qid: "Q718", label: "chess", source: "nicheclock-list" });
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends only the word to Wikidata when the list does not hold it, preferring a page with an English article", async () => {
    calls.length = 0;
    fakeWikidata(
      [{ id: "Q111", label: "zzrare", description: "a record label" }, { id: "Q222", label: "zzrare", description: "a strategy game" }],
      { Q111: false, Q222: true },
    );
    const out = await json(await lookup("zzrare"));
    expect(out.topic).toMatchObject({ qid: "Q222", source: "wikidata", confidence: 0.9 });
    expect(out.topic.alternatives[0].qid).toBe("Q111");
    expect(calls.length).toBe(2);
    for (const c of calls) {
      expect(new URL(c.url).host).toBe("www.wikidata.org");
      expect(c.url).not.toMatch(/example\.org|nicheclock/);
    }
    expect(calls[0]?.url).toContain("search=zzrare");
  });

  it("says nothing was found, refuses a bad body and limits to 20 an hour", async () => {
    fakeWikidata([], {});
    expect((await json(await lookup("zznothing"))).topic).toBeNull();
    expect((await lookup("")).status).toBe(400);
    expect((await lookup("x".repeat(101))).status).toBe(400);
    expect((await lookup(5)).status).toBe(400);
    const same = { "cf-connecting-ip": "198.51.100.77" };
    const statuses: number[] = [];
    for (let i = 0; i < 22; i++) statuses.push((await lookup("chess", same)).status);
    expect(statuses.slice(0, 20).every((s) => s === 200)).toBe(true);
    expect(statuses.slice(20)).toEqual([429, 429]);
  });

  it("reports Wikidata being down as a fixed message", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("zzsentinel boom"));
    const r = await lookup("zzsentinelword");
    expect(r.status).toBe(502);
    expect(JSON.stringify(await json(r))).not.toMatch(/zzsentinel/);
  });
});

/* ---------- Read my channel ---------- */

const KEY = "test-key-not-a-real-google-key";
const yt = { ASSETS: assets, BURST: undefined, YOUTUBE_API_KEY: KEY };
const CH = "UC" + "a".repeat(22);

function fakeYoutube(opts: { quota?: boolean } = {}) {
  calls.length = 0;
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const u = new URL(String(input));
    calls.push({ url: String(input), ua: null, redirect: undefined });
    if (opts.quota) return Response.json({ error: { errors: [{ reason: "quotaExceeded" }] } }, { status: 403 });
    if (u.pathname.endsWith("/channels")) return Response.json({ items: [{ contentDetails: { relatedPlaylists: { uploads: "UUabcdefghijklmnop" } } }] });
    if (u.pathname.endsWith("/playlistItems")) {
      return Response.json({ items: Array.from({ length: 12 }, (_v, i) => ({ snippet: { title: i % 2 ? `Sicilian defense explained part ${i}` : `Endgame rook technique lesson ${i}`, resourceId: { videoId: `vid${String(i).padStart(8, "0")}` } } })) });
    }
    return Response.json({ items: Array.from({ length: 12 }, (_v, i) => ({ id: `vid${String(i).padStart(8, "0")}`, snippet: { tags: ["chess", "sicilian defense"] } })) });
  });
}
const channel = (c: unknown, extra: Record<string, string> = {}, env: object = yt) => call("/v1/suggest/youtube", { body: { channel: c }, headers: { origin: "https://nicheclock.elmrank.com", ...ip(), ...extra }, env });

describe("POST /v1/suggest/youtube", () => {
  it("is off, and says so, until the secret YOUTUBE_API_KEY is set", async () => {
    const off = await json(await call("/v1/suggest/status", { env: { ASSETS: assets } }));
    expect(off).toEqual({ resolve: true, site: true, youtube: false });
    const r = await channel("@example-chess", {}, { ASSETS: assets, BURST: undefined });
    expect(r.status).toBe(404);
    expect((await json(r)).error.code).toBe("not_available");
    expect((await json(await call("/v1/suggest/status", { env: yt }))).youtube).toBe(true);
  });

  it("spends 3 units, reads titles and tags, and never returns the key", async () => {
    fakeYoutube();
    const r = await channel("https://www.youtube.com/@example-chess");
    expect(r.status).toBe(200);
    const text = await r.text();
    expect(text).not.toContain(KEY);
    const out = JSON.parse(text);
    expect(out.videos_read).toBe(12);
    const texts = out.phrases.map((p: { text: string }) => p.text);
    expect(texts).toContain("sicilian defense");
    expect(texts).toContain("chess");
    expect(calls.map((c) => new URL(c.url).pathname)).toEqual(["/youtube/v3/channels", "/youtube/v3/playlistItems", "/youtube/v3/videos"]);
    for (const c of calls) {
      expect(new URL(c.url).host).toBe("www.googleapis.com");
      expect(c.url).toContain(`key=${KEY}`);
    }
    expect(calls[0]?.url).toContain("forHandle=%40example-chess");
    const row = await db().prepare("SELECT units FROM youtube_units_daily").first<{ units: number }>();
    expect(row?.units).toBeGreaterThanOrEqual(3);
  });

  it("accepts a channel id and refuses anything else", async () => {
    expect(parseChannel("@example-chess")).toEqual({ forHandle: "@example-chess" });
    expect(parseChannel(CH)).toEqual({ id: CH });
    expect(parseChannel(`https://youtube.com/channel/${CH}`)).toEqual({ id: CH });
    expect(parseChannel("youtube.com/@example-chess/videos")).toEqual({ forHandle: "@example-chess" });
    for (const bad of ["", "chess", "https://evil.example/@x", "https://youtube.com/watch?v=abc", "UCshort", "@a", "ftp://youtube.com/@abc", 5, "@ bad handle"]) expect(() => parseChannel(bad), String(bad)).toThrow();
  });

  it("allows 5 reads a day per address and stops at the day's budget of 2,000 units", async () => {
    fakeYoutube();
    const same = { "cf-connecting-ip": "198.51.100.99" };
    const statuses: number[] = [];
    for (let i = 0; i < 6; i++) statuses.push((await channel("@example-chess", same)).status);
    expect(statuses).toEqual([200, 200, 200, 200, 200, 429]);
    await db().prepare("UPDATE youtube_units_daily SET units = 1999").run();
    const r = await channel("@example-chess");
    expect(r.status).toBe(429);
    expect((await json(r)).error.code).toBe("quota_exhausted");
    expect((await db().prepare("SELECT units FROM youtube_units_daily").first<{ units: number }>())?.units).toBe(1999);
  });

  it("turns Google's quotaExceeded into the same sentence", async () => {
    fakeYoutube({ quota: true });
    const r = await channel("@example-chess");
    expect(r.status).toBe(429);
    expect((await json(r)).error.code).toBe("quota_exhausted");
  });
});

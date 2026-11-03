/**
 * POST /v1/suggest/site  {"url": "https://example.org"}   ("Read my site", design 7.1 with the hard caps of the plan)
 *
 *   robots.txt (RFC 9309) -> one sitemap (read at most 300 KB; an index is followed to one child) -> at most 8 page titles (each at most 64 KB)
 *   = at most 26 subrequests. The words of 40 sampled addresses and the 8 titles become phrases (phrases.js). Nothing is stored; the reply holds
 *   no address, no title and no HTML, only phrases and counts. Phrases are not looked up here: the page matches them to its own concept list,
 *   so no phrase of the person's site goes to a third party unless they press "Look up".
 *
 * CPU (10 ms on the free plan): a 300 KB sitemap holds about 3,000 addresses and `new URL()` on each of them cost 10 to 25 ms, so the
 * addresses are never parsed as a whole. The offsets of the <loc> texts are found with indexOf, 40 are picked evenly (stepping on past
 * a tag, category or file address) and only those are read. docs/personalization.md has the measurement on the deployed Worker.
 */
import type { Env } from "./env";
import { ApiError } from "./http";
import { extractPhrases, cleanTitles, slugWordsOfPath } from "../site/trending/phrases.js";
import { ROBOTS_MAX_BYTES, isAllowed, parseRobots, rulesFor, type Rule } from "./robotstxt";
import { checkCaller, checkHost, spendSiteRead } from "./suggest-limits";
import { PRODUCT_TOKEN, checkTarget, fetchLimited, gunzipCapped, newBudget, sameSite, type Budget, type Fetched, type Target } from "./suggest-net";

export const SITEMAP_MAX_BYTES = 300 * 1024;
export const PAGE_MAX_BYTES = 64 * 1024;
export const MAX_TITLES = 8;
export const SAMPLE_URLS = 40;
const MAX_LOCS = 20_000;
const MAX_STEP = 30;
const SKIP_CHILD = /image|video|author|user|tag|taxonom|categor|attachment/i;
const SKIP_PATH = /\/(tag|tags|category|categories|author|authors|page|feed|wp-json|wp-admin|wp-content|search|cart|checkout|login)(\/|$)/i;
const SKIP_EXT = /\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|gz|mp4|mp3|xml|json|css|js|txt|rss|atom)$/i;
const TRY_SITEMAPS = ["/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"];
const LOC = /^(https?):\/\/([^/?#:@\s]+)(?::(\d+))?(\/[^?#\s]*)?/i;
const TAIL = " Type your topics instead.";

const fail = (status: number, code: string, message: string) => new ApiError(status, code, message + TAIL);

function decodeEntities(s: string): string {
  if (!s.includes("&")) return s;
  return s.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (_m, e: string) => {
    const l = e.toLowerCase();
    if (l === "amp") return "&";
    if (l === "lt") return "<";
    if (l === "gt") return ">";
    if (l === "quot") return '"';
    if (l === "apos") return "'";
    if (l === "nbsp") return " ";
    const code = l.startsWith("#x") ? Number.parseInt(l.slice(2), 16) : Number.parseInt(l.slice(1), 10);
    return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : " ";
  });
}

/** Offsets of the text between every <loc> and </loc>: [start0, end0, start1, end1, ...]. indexOf only, no regex and no address parsing. */
export function locOffsets(xml: string, max = MAX_LOCS): number[] {
  const out: number[] = [];
  let at = xml.indexOf("<loc>");
  while (at >= 0 && out.length < max * 2) {
    const start = at + 5;
    const end = xml.indexOf("</loc>", start);
    if (end < 0) break;
    out.push(start, end);
    at = xml.indexOf("<loc>", end + 6);
  }
  return out;
}

/** The i-th <loc> text: white space and CDATA removed, entities decoded */
export function locAt(xml: string, offs: number[], i: number): string {
  let s = xml.slice(offs[2 * i] as number, offs[2 * i + 1] as number).trim();
  if (s.startsWith("<![CDATA[")) s = s.slice(9, s.endsWith("]]>") ? -3 : undefined).trim();
  return decodeEntities(s);
}

/** the <loc> values of a sitemap or an index */
export function sitemapLocs(xml: string, max = MAX_LOCS): string[] {
  const offs = locOffsets(xml, max);
  return Array.from({ length: offs.length / 2 }, (_v, i) => locAt(xml, offs, i));
}

/** the <title> (else the first <h1>) of the head of a page, tags and entities removed, at most 200 characters */
export function pageTitle(html: string): string {
  const t = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(html) ?? /<h1[^>]*>([\s\S]*?)<\/h1>/i.exec(html);
  if (!t) return "";
  return decodeEntities((t[1] as string).replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim().slice(0, 200);
}

/** n addresses, evenly spaced through the list (deterministic, like the self-hosted reader) */
export function sampleEvenly<T>(items: T[], n: number): T[] {
  if (items.length <= n) return items.slice();
  const out: T[] = [];
  for (let i = 0; i < n; i++) out.push(items[Math.floor((i * items.length) / n)] as T);
  return out;
}

/** a page of the site worth reading: same site, not a tag, category, feed or file address, with words in its last segment */
function pageAddress(loc: string, host: string): string | null {
  const m = LOC.exec(loc);
  if (!m) return null;
  if (!sameSite((m[2] as string).toLowerCase(), host)) return null;
  const path = m[4] ?? "/";
  if (path === "/" || SKIP_PATH.test(path) || SKIP_EXT.test(path) || !slugWordsOfPath(path)) return null;
  return loc;
}

/** Up to 40 page addresses, evenly through the list: from each evenly spaced position the first acceptable entry at or after it. */
export function pickPages(xml: string, offs: number[], host: string, want = SAMPLE_URLS): string[] {
  const n = offs.length / 2;
  const take = Math.min(want, n);
  const used = new Set<number>();
  const out: string[] = [];
  for (let k = 0; k < take; k++) {
    for (let i = Math.floor((k * n) / take), step = 0; i < n && step < MAX_STEP; i++, step++) {
      if (used.has(i)) continue;
      const page = pageAddress(locAt(xml, offs, i), host);
      if (page) {
        used.add(i);
        out.push(page);
        break;
      }
    }
  }
  return out;
}

async function robotsFor(budget: Budget, t: Target): Promise<{ rules: Rule[]; sitemaps: string[] }> {
  const r = await fetchLimited(budget, new URL(`${t.origin}/robots.txt`), ROBOTS_MAX_BYTES);
  if (r.status >= 500 || r.redirectRefused || r.status === 429) {
    throw fail(502, "robots_unreadable", "robots.txt could not be read, so nothing was fetched.");
  }
  if (r.status === 401 || r.status === 403) throw fail(403, "robots_blocked", "The site does not let bots read it (robots.txt answered 401 or 403), so nothing was fetched.");
  if (r.status >= 400) return { rules: [], sitemaps: [] };      // RFC 9309 2.3.1.3: a 4xx means there are no rules
  const robots = parseRobots(new TextDecoder().decode(r.bytes));
  return { rules: rulesFor(robots, PRODUCT_TOKEN), sitemaps: robots.sitemaps };
}

async function readXml(budget: Budget, url: URL): Promise<Fetched & { text: string }> {
  const r = await fetchLimited(budget, url, SITEMAP_MAX_BYTES);
  let bytes = r.bytes;
  if (r.status === 200 && bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) bytes = (await gunzipCapped(bytes, SITEMAP_MAX_BYTES)).bytes;
  return { ...r, text: r.status === 200 ? new TextDecoder().decode(bytes) : "" };
}

const refused = () => fail(502, "site_refused", "The site refused the request (a 403 or 429 answer); nothing was retried.");

/** the sitemap text and the offsets of its addresses: one sitemap, or an index followed to one child */
async function readSitemap(budget: Budget, t: Target, rules: Rule[], listed: string[]): Promise<{ xml: string; offs: number[] }> {
  const sameHost = (u: URL) => (u.protocol === "https:" || u.protocol === "http:") && sameSite(u.hostname.toLowerCase(), t.host);
  const candidates: URL[] = [];
  for (const s of listed) {
    try {
      const u = new URL(s);
      if (sameHost(u)) candidates.push(u);
    } catch {
      /* not an address */
    }
  }
  if (!candidates.length) for (const p of TRY_SITEMAPS) candidates.push(new URL(`${t.origin}${p}`));
  for (const u of candidates.slice(0, 3)) {
    if (!isAllowed(rules, u.pathname + u.search)) continue;
    const r = await readXml(budget, u);
    if (r.status === 403 || r.status === 429) throw refused();
    if (r.status !== 200 || !r.text) continue;
    let xml = r.text;
    let offs = locOffsets(xml);
    if (/<sitemapindex[\s>]/i.test(xml.slice(0, 2048))) {
      let child: URL | null = null;
      for (let i = 0; i < Math.min(offs.length / 2, 50) && !child; i++) {
        const c = locAt(xml, offs, i);
        try {
          const cu = new URL(c);
          if (!SKIP_CHILD.test(c) && sameHost(cu) && isAllowed(rules, cu.pathname + cu.search)) child = cu;
        } catch {
          /* not an address */
        }
      }
      if (!child) continue;
      const cr = await readXml(budget, child);
      if (cr.status === 403 || cr.status === 429) throw refused();
      xml = cr.status === 200 ? cr.text : "";
      offs = locOffsets(xml);
    }
    if (offs.length) return { xml, offs };
  }
  throw fail(422, "no_sitemap", "No sitemap with pages was found (robots.txt, /sitemap.xml, /sitemap_index.xml, /wp-sitemap.xml).");
}

export interface SiteReply {
  ok: true;
  pages_in_sitemap: number;
  pages_read: number;
  titles_read: number;
  phrases: { text: string; pages_with: number }[];
}

/** The whole read. */
export async function readSite(input: unknown, env: Env, req: Request, now = new Date()): Promise<SiteReply> {
  const t = checkTarget(input);
  await checkCaller(env, req, "site");
  await checkHost(env, t.host);
  if (!(await spendSiteRead(env, now))) throw fail(429, "quota_exhausted", "Site reading is used up for today.");
  const budget = newBudget(now.getTime());

  const robots = await robotsFor(budget, t);
  if (!isAllowed(robots.rules, "/")) throw fail(403, "robots_blocked", "The robots.txt of this site asks bots not to read it, so nothing was fetched.");

  const { xml, offs } = await readSitemap(budget, t, robots.rules, robots.sitemaps);
  const sample = pickPages(xml, offs, t.host);
  if (sample.length < 3) throw fail(422, "too_few_pages", "The sitemap lists too few pages to read.");

  // the titles of 8 pages, evenly through the sample, each at most 64 KB, one after the other (the site is not hit in parallel)
  const step = Math.max(1, Math.floor(sample.length / MAX_TITLES));
  const want = Array.from({ length: Math.min(MAX_TITLES, sample.length) }, (_v, i) => i * step);
  const titles: { i: number; title: string }[] = [];
  for (const i of want) {
    let u: URL;
    try {
      u = new URL(sample[i] as string);
    } catch {
      continue;
    }
    if (!isAllowed(robots.rules, u.pathname + u.search)) continue;
    let r: Fetched;
    try {
      r = await fetchLimited(budget, u, PAGE_MAX_BYTES);
    } catch (e) {
      if (e instanceof ApiError && e.code === "too_many_steps") break;
      throw e;
    }
    if (r.status === 403 || r.status === 429) throw refused();
    if (r.status !== 200 || !/html/.test(r.contentType)) continue;
    const title = pageTitle(new TextDecoder().decode(r.bytes));
    if (title) titles.push({ i, title });
  }
  const cleaned = cleanTitles(titles.map((x) => x.title));
  const withTitle = new Map<number, string>();
  titles.forEach((x, k) => {
    if (cleaned[k]) withTitle.set(x.i, cleaned[k] as string);
  });
  const docs = sample.map((u, i) => ({ text: withTitle.get(i) ?? slugWordsOfPath(LOC.exec(u)?.[4] ?? "") }));
  const { phrases } = extractPhrases(docs);
  return {
    ok: true,
    pages_in_sitemap: offs.length / 2,
    pages_read: sample.length,
    titles_read: withTitle.size,
    phrases: phrases.map((p: { text: string; with: number }) => ({ text: p.text, pages_with: p.with })),
  };
}

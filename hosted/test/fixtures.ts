/** R2 fixture data: allowed + disallowed + malformed items, in the layout of SCHEMA.md. */
import { env } from "cloudflare:workers";

const H = 3_600_000;
export const iso = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString();
export const ymd = (offsetDays: number) => new Date(Date.now() + offsetDays * 86_400_000).toISOString().slice(0, 10);

const prov = {
  hn: { source: "hn", source_url: "https://news.ycombinator.com/item?id=1", attribution: "Hacker News (Y Combinator)" },
  wiki: { source: "wikimedia_pageviews", source_url: "https://en.wikipedia.org/wiki/Merlin_Bird_ID_(app)", license: "CC0-1.0" },
  gdelt: { source: "gdelt", source_url: "https://api.gdeltproject.org/api/v2/doc/doc?query=x", attribution: "GDELT Project" },
  github: { source: "apple", source_url: "https://apple.example/x", license: "internal" },
  gtrends: { source: "google_trends", source_url: "https://trends.google.com/", attribution: "Google" },
  apple: { source: "apple", source_url: "https://apps.apple.com/us/app/x/id1", attribution: "Apple Marketing Tools RSS" },
  nager: { source: "nager", source_url: "https://date.nager.at/", license: "MIT" },
  fr: { source: "federal_register", source_url: "https://www.federalregister.gov/d/2026-1", license: "Public domain (17 U.S.C. 105)" },
};

export function trendsDoc(niche: string) {
  const at = iso(-2 * H);
  const mk = (n: string, title: string, score: number, p: object, extra: object = {}) => ({
    id: `${niche}~${n}`, title, score, why: "x", url: "https://example.org/a", at, sources: [(p as { source: string }).source], evidence_count: 2, ...p, ...extra,
  });
  return {
    schema: 1,
    niche,
    generated_at: iso(-H),
    items: [
      mk("aaaaaa01", "Merlin Bird ID sequel rumors", 80, prov.hn, { sources: ["hn", "apple", "google_trends"], why: "2 sources; google_trends #3; hn 120 pts", secret_field: "LEAK" }),
      mk("aaaaaa02", "Bird watching roguelike surge", 70, prov.wiki),
      mk("aaaaaa03", "Bird feeder news volume", 60, prov.gdelt),
      mk("bbbbbb01", "Apple-only item", 90, prov.github),
      mk("bbbbbb02", "Google Trends term", 85, prov.gtrends),
      mk("bbbbbb03", "App sale", 75, prov.apple),
      mk("bbbbbb04", "No license item", 50, { source: "hn", source_url: "https://news.ycombinator.com/item?id=2" }),
      mk("bbbbbb05", "No source_url item", 50, { source: "hn", license: "x" }),
      mk("eeeeee01", "Reddit thread", 52, { source: "reddit", source_url: "https://reddit.com/r/x", attribution: "Reddit" }),
      mk("ffffff01", "Hybrid topic scored with internal signals", 55, prov.hn, { scope: "internal", sources: ["hn", "google_trends"] }),
      mk("cccccc01", "Old item", 40, prov.hn, { at: iso(-20 * 24 * H) }),
      { id: "other-niche~dddddd01", title: "wrong niche prefix", score: 1, at, ...prov.hn },
      "not an object",
    ],
  };
}

export function evidenceDoc(niche: string) {
  const sig = (p: object, term: string) => ({ ...p, term, seen_at: iso(-H), score: 3, rank: 1 });
  return {
    schema: 1,
    niche,
    generated_at: iso(-H),
    items: {
      [`${niche}~aaaaaa01`]: {
        title: "Merlin Bird ID sequel rumors",
        ...prov.hn,
        evidence: [sig(prov.hn, "merlin bird id sequel"), sig(prov.github, "merlin-bird-id-mod"), sig(prov.gtrends, "merlin bird id 2"), sig({ source: "gdelt" }, "no provenance")],
      },
      [`${niche}~bbbbbb01`]: { title: "Apple-only item", ...prov.github, evidence: [sig(prov.github, "repo")] },
      [`${niche}~ffffff01`]: { title: "Hybrid topic scored with internal signals", scope: "internal", ...prov.hn, evidence: [sig(prov.hn, "hybrid")] },
      [`${niche}~bbbbbb03`]: { title: "App sale", ...prov.apple, evidence: [sig(prov.apple, "app chart")] },
    },
  };
}

export function upcomingDoc(niche: string) {
  const ev = (n: string, title: string, days: number, p: object) => ({
    id: `${niche}~e${n}`, kind: "release", title, date: ymd(days), date_precision: "day", publish_by: ymd(days - 3), url: "https://example.org/e", ...p,
  });
  return {
    schema: 1,
    niche,
    generated_at: iso(-H),
    items: [
      ev("000001", "Rule comment deadline", 5, prov.fr),
      ev("000002", "Far away event", 100, prov.fr),
      ev("000003", "Spring Migration Fest", 6, prov.apple),
      ev("000004", "Public holiday", 7, prov.nager),
      ev("000005", "Past event", -2, prov.fr),
      ev("000006", "RSS event", 8, { source: "rss", source_url: "https://example.org/rss", attribution: "x" }),
    ],
  };
}

export async function put(key: string, value: unknown): Promise<void> {
  await env.BUCKET.put(key, JSON.stringify(value), { httpMetadata: { contentType: "application/json" } });
}

export async function seedCatalog(niche = "birdwatching"): Promise<void> {
  await put("catalog/niches.json", {
    schema: 1,
    generated_at: iso(-H),
    niches: [
      { id: niche, name: "birdwatching", sources: ["hn", "wikimedia_pageviews", "gdelt", "apple"], updated_at: iso(-H), counts: { trends: 3, upcoming: 2 }, ignored: "x" },
      { id: "github-only", name: "only apple", sources: ["apple", "google_trends"], updated_at: iso(-H) },
    ],
  });
  await put(`catalog/${niche}/trends.json`, trendsDoc(niche));
  await put(`catalog/${niche}/evidence.json`, evidenceDoc(niche));
  await put(`catalog/${niche}/upcoming.json`, upcomingDoc(niche));
}

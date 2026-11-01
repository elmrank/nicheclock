#!/usr/bin/env node
// Seed LOCAL dev state (no Cloudflare account, no login): migrations, a fixture catalog in local R2,
// and one internal + one external key. Then run `npm run dev` and curl http://localhost:8787.
//   node scripts/dev-seed.mjs            -> prints the two keys to stderr
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const STATE = ".wrangler/state";
const BUCKET = "nicheclock-data";
const wr = (...a) => execFileSync("npx", ["wrangler", ...a], { stdio: ["ignore", "ignore", "inherit"] });
const day = (n) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);
const iso = (ms) => new Date(Date.now() + ms).toISOString();

const prov = {
  hn: { source: "hn", source_url: "https://news.ycombinator.com/item?id=1", attribution: "Hacker News (Y Combinator) via Algolia HN Search API" },
  wiki: { source: "wikimedia_pageviews", source_url: "https://en.wikipedia.org/wiki/Merlin_Bird_ID_(app)", license: "CC0-1.0" },
  fr: { source: "federal_register", source_url: "https://www.federalregister.gov/d/2026-00001", license: "Public domain (17 U.S.C. 105)" },
  apple: { source: "apple", source_url: "https://apps.apple.com/us/app/example/id1426210", attribution: "Apple Marketing Tools RSS (internal use only)" },
  github: { source: "github", source_url: "https://github.com/example/repo", license: "MIT" }, // present in R2 on purpose: must be dropped when served
};
const niche = "birdwatching";
const item = (h, title, score, p) => ({ id: `${niche}~${h}`, title, score, why: `${p.source}`, url: p.source_url, at: iso(-3_600_000), sources: [p.source], evidence_count: 1, ...p });
const files = {
  "catalog/niches.json": { schema: 1, generated_at: iso(0), niches: [{ id: niche, name: "birdwatching", sources: ["hn", "wikimedia_pageviews", "federal_register", "apple", "github"], updated_at: iso(0), counts: { trends: 4, upcoming: 2 } }] },
  [`catalog/${niche}/trends.json`]: { schema: 1, niche, generated_at: iso(0), items: [item("a1b2c3d4e5f6", "Merlin Bird ID sequel rumors", 82.5, prov.hn), item("a1b2c3d4e5f7", "Snow Goose views x3", 71, prov.wiki), item("a1b2c3d4e5f8", "Feeder bird watching sale", 64, prov.apple), item("a1b2c3d4e5f9", "bird watching-repo trending", 90, prov.github)] },
  [`catalog/${niche}/evidence.json`]: { schema: 1, niche, generated_at: iso(0), items: { [`${niche}~a1b2c3d4e5f6`]: { title: "Merlin Bird ID sequel rumors", ...prov.hn, evidence: [{ ...prov.hn, term: "merlin bird id sequel", seen_at: iso(-3_600_000), score: 120, rank: 4 }] } } },
  [`catalog/${niche}/upcoming.json`]: { schema: 1, niche, generated_at: iso(0), items: [{ id: `${niche}~e00000000001`, kind: "release", title: "Comment deadline for a rule on game ratings", date: day(9), date_precision: "day", publish_by: day(6), url: prov.fr.source_url, ...prov.fr }, { id: `${niche}~e00000000002`, kind: "event", title: "Spring Migration Fest", date: day(12), date_precision: "day", publish_by: day(9), url: prov.apple.source_url, ...prov.apple }] },
};

const dir = mkdtempSync(join(tmpdir(), "nc-seed-"));
wr("d1", "migrations", "apply", "nicheclock", "--local", "--persist-to", STATE);
for (const [key, data] of Object.entries(files)) {
  const f = join(dir, key.replaceAll("/", "__"));
  writeFileSync(f, JSON.stringify(data));
  wr("r2", "object", "put", `${BUCKET}/${key}`, "--file", f, "--content-type", "application/json", "--local", "--persist-to", STATE);
}
for (const [scope, email] of [["internal", "dev-internal@example.org"], ["external", "dev-external@example.org"]]) {
  const sql = join(dir, `${scope}.sql`);
  const out = execFileSync("node", ["scripts/mint-key.mjs", "--scope", scope, "--email", email], { stdio: ["ignore", "pipe", "inherit"] });
  writeFileSync(sql, out);
  wr("d1", "execute", "nicheclock", "--local", "--persist-to", STATE, "--file", sql);
}
mkdirSync(STATE, { recursive: true });
console.error("seeded .wrangler/state (local D1 + R2). Keys are printed above; start the Worker with: npm run dev");

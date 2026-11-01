/**
 * Hosted source allow-list, enforced when SERVING (not only when exporting): anything else that
 * ends up in R2 is dropped. Terms per source: see SCHEMA.md.
 */
import type { Scope } from "./env";

/** Redistributable, commercial use allowed. */
export const PUBLIC_SOURCES: readonly string[] = [
  "wikimedia_pageviews", // Wikimedia pageviews, CC0
  "wikipedia", //          Wikipedia facts (title/date/link only; text is CC BY-SA and never returned)
  "wikidata", //           Wikidata facts, CC0
  "gdelt", //              GDELT DOC 2.0, must cite
  "hn", //                 Hacker News (Firebase / Algolia)
  "federal_register", //   US government works, 17 U.S.C. 105
  "github", //             official GitHub REST API, read-only (public repo metadata) + aggregate star/fork counts of public events (GH Archive); ledger: nicheclock/catalog/data/sources.json
];

/**
 * Internal use only (not redistribution; for the google_trends,
 * github and apple additions): visible to scope internal and admin keys, never to external ones.
 */
export const INTERNAL_SOURCES: readonly string[] = ["nager", "google_trends", "apple"];

/** Ids that may exist locally but are NEVER served to anyone: used to scrub free text such as `why`. */
export const NEVER_SERVED_SOURCES: readonly string[] = ["google_news", "reddit"];

export function allowedSources(scope: Scope): ReadonlySet<string> {
  return new Set(scope === "external" ? PUBLIC_SOURCES : [...PUBLIC_SOURCES, ...INTERNAL_SOURCES]);
}

/**
 * search_updates: the MCP tool over the Google Search updates page (/google-updates/).
 *
 * It reads /catalog-data/google-updates.json through the assets binding: the file the site build validated (site/google_updates.py) and
 * published next to the page, about 50 KB, parsed once per isolate (shards.ts getJson). Nothing is stored and no caller data is read.
 * `rolling` was derived when the file was exported (one place for that logic); this file only reads it.
 */
import type { Env } from "./env";
import { ApiError } from "./http";
import { getJson } from "./shards";

const FILE = "/catalog-data/google-updates.json";
const KINDS = ["ranking", "docs", "blog"] as const;
type Kind = (typeof KINDS)[number];
const DAY_MS = 86_400_000;
const STALE_DAYS = 3;
const TYPE_LABEL: Record<string, string> = { core: "Core update", spam: "Spam update", discover: "Discover update", other: "Ranking update" };

interface Ranking { name: string; type: string; started: string; ended: string | null; duration_days: number | null; url: string }
interface Rolling { name: string; type: string; started: string; url: string }
interface Change { date: string; kind: "docs" | "blog"; category: string; title: string; url: string; summary: string }
interface Data {
  schema: number;
  data_date: string;
  window: { from: string; to: string };
  attribution: { text: string; license_url: string; policy_url: string };
  rolling: Rolling[];
  ranking: Ranking[];
  items: Change[];
}

function bad(message: string): ApiError {
  return new ApiError(400, "invalid_params", message);
}

function dayArg(v: unknown): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v.trim())) throw bad("since must be a date such as 2026-09-01");
  const s = v.trim();
  if (new Date(`${s}T00:00:00Z`).toISOString().slice(0, 10) !== s) throw bad("since must be a real calendar date such as 2026-09-01");
  return s;
}

function limitArg(v: unknown): number {
  if (v === undefined) return 20;
  if (typeof v !== "number" || !Number.isInteger(v) || v < 1 || v > 100) throw bad("limit must be an integer between 1 and 100");
  return v;
}

function kindArg(v: unknown): Kind | null {
  if (v === undefined || v === null) return null;
  const k = typeof v === "string" ? v.trim().toLowerCase() : "";
  const hit = KINDS.find((x) => x === k);
  if (!hit) throw bad(`kind must be one of ${KINDS.join(", ")}`);
  return hit;
}

function rankingSummary(r: Ranking): string {
  if (!r.ended) return `Rollout started on ${r.started}. Google has not published an end date, so it is still rolling out.`;
  return `Rollout started on ${r.started} and ended on ${r.ended}, after ${r.duration_days} day${r.duration_days === 1 ? "" : "s"}.`;
}

export async function searchUpdates(env: Env, args: Record<string, unknown>, now: Date): Promise<unknown> {
  const since = dayArg(args.since);
  const kind = kindArg(args.kind);
  const limit = limitArg(args.limit);
  const data = await getJson<Data>(env, FILE);
  if (!data) throw new ApiError(503, "updates_unavailable", "the Google Search updates data is not published on this deployment");

  const rows: Record<string, unknown>[] = [];
  if (kind === null || kind === "ranking") {
    for (const r of data.ranking) {
      // a ranking update is in range when it started, ended or is still rolling on or after `since`
      if (since !== null && r.started < since && !(r.ended === null || r.ended >= since)) continue;
      rows.push({
        date: r.started,
        kind: "ranking",
        category: TYPE_LABEL[r.type] ?? "Ranking update",
        title: r.name,
        url: r.url,
        summary: rankingSummary(r),
        status: r.ended === null ? "started" : "ended",
        ended: r.ended,
        duration_days: r.duration_days,
      });
    }
  }
  if (kind !== "ranking") {
    for (const c of data.items) {
      if ((kind !== null && c.kind !== kind) || (since !== null && c.date < since)) continue;
      rows.push({ date: c.date, kind: c.kind, category: c.category, title: c.title, url: c.url, summary: c.summary });
    }
  }
  rows.sort((a, b) => (a.date === b.date ? 0 : (a.date as string) < (b.date as string) ? 1 : -1));

  const first = data.rolling[0];
  const age = Math.floor((Date.parse(`${now.toISOString().slice(0, 10)}T00:00:00Z`) - Date.parse(`${data.data_date}T00:00:00Z`)) / DAY_MS);
  return {
    rolling_now: first !== undefined,
    rolling_name: first?.name ?? null,
    rolling_started: first?.started ?? null,
    rolling: data.rolling.map((r) => ({ name: r.name, type: r.type, started: r.started, url: r.url })),
    rolling_note: "rolling_now is true while a core or spam update has started and Google has published no end date. Other ranking updates are listed with status started or ended.",
    data_date: data.data_date,
    stale: age >= STALE_DAYS,
    since,
    kind,
    matching: rows.length,
    items: rows.slice(0, limit),
    attribution: `${data.attribution.text} (${data.attribution.license_url}). Summaries are Google's own wording, shortened; the title links to the original page. Ranking update names, dates and links come from the Google Search Status Dashboard (https://status.search.google.com/).`,
  };
}

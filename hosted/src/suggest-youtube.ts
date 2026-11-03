/**
 * POST /v1/suggest/youtube  {"channel": "@example-chess"}   ("Read my channel", design 7.2)
 *
 * Off unless the Worker secret YOUTUBE_API_KEY is set (the key of a Google Cloud project with billing OFF; docs/personalization.md says how
 * to make it). Three calls of the YouTube Data API v3, 1 unit each, so 3 units a click:
 *   channels.list (forHandle or id)  ->  playlistItems.list (the 50 latest uploads)  ->  videos.list (the creator's own tags)
 * `search.list` (100 units) is never used. The day's budget is 2,000 units, one D1 row per UTC day (suggest-limits.ts), and 5 reads a day
 * per address. Nothing from the API is stored; titles and tags become phrases (phrases.js; a tag counts double) for the person who pressed
 * the button. The key is only ever sent to googleapis.com, in the request URL of those calls; it is not in the reply, and nothing here logs.
 */
import type { Env } from "./env";
import { ApiError } from "./http";
import { extractPhrases, cleanTitles } from "../site/trending/phrases.js";
import { checkCaller, spendYoutubeUnits } from "./suggest-limits";

export const UNITS_PER_READ = 3;
export const DAILY_UNIT_CAP = 2000;
const API = "https://www.googleapis.com/youtube/v3/";
const TAIL = " Type your topics instead.";

export const youtubeEnabled = (env: Env): boolean => typeof env.YOUTUBE_API_KEY === "string" && env.YOUTUBE_API_KEY.length > 0;

type ChannelRef = { forHandle: string } | { id: string };

export function parseChannel(input: unknown): ChannelRef {
  const bad = () => new ApiError(400, "invalid_channel", `Use a channel handle such as @example-chess, a channel link, or a channel id that starts with UC.${TAIL}`);
  if (typeof input !== "string") throw bad();
  const raw = input.trim();
  if (!raw || raw.length > 200 || /[\u0000-\u001f\u007f\s]/.test(raw)) throw bad();
  if (/^@[\p{L}\p{N}._-]{3,30}$/u.test(raw)) return { forHandle: raw };
  if (/^UC[A-Za-z0-9_-]{22}$/.test(raw)) return { id: raw };
  let u: URL;
  try {
    u = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    throw bad();
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") throw bad();
  if (!["youtube.com", "www.youtube.com", "m.youtube.com"].includes(u.hostname.toLowerCase())) throw bad();
  const seg = u.pathname.split("/").filter(Boolean);
  let first = seg[0] ?? "";
  try {
    first = decodeURIComponent(first);
  } catch {
    throw bad();
  }
  if (/^@[\p{L}\p{N}._-]{3,30}$/u.test(first)) return { forHandle: first };
  if (first === "channel" && /^UC[A-Za-z0-9_-]{22}$/.test(seg[1] ?? "")) return { id: seg[1] as string };
  throw bad();
}

interface YtItem {
  contentDetails?: { relatedPlaylists?: { uploads?: unknown } };
  snippet?: { title?: unknown; tags?: unknown; resourceId?: { videoId?: unknown } };
}
interface YtReply {
  items?: YtItem[];
  error?: { errors?: { reason?: unknown }[] };
}

async function yt(env: Env, resource: string, params: Record<string, string>): Promise<YtReply> {
  const qs = new URLSearchParams({ ...params, key: env.YOUTUBE_API_KEY as string });
  let res: Response;
  try {
    res = await fetch(`${API}${resource}?${qs}`, { headers: { accept: "application/json" }, signal: AbortSignal.timeout(6000) });
  } catch {
    throw new ApiError(502, "youtube_unavailable", `YouTube could not be reached just now.${TAIL}`);
  }
  let body: YtReply = {};
  try {
    body = (await res.json()) as YtReply;
  } catch {
    /* no body */
  }
  if (res.status === 403 && body.error?.errors?.some((e) => e.reason === "quotaExceeded" || e.reason === "dailyLimitExceeded" || e.reason === "rateLimitExceeded")) {
    throw new ApiError(429, "quota_exhausted", `Channel reading is used up for today.${TAIL}`);
  }
  if (res.status !== 200) throw new ApiError(502, "youtube_unavailable", `YouTube could not answer just now.${TAIL}`);
  return body;
}

export interface YoutubeReply {
  ok: true;
  videos_read: number;
  phrases: { text: string; pages_with: number }[];
}

export async function readChannel(input: unknown, env: Env, req: Request, now = new Date()): Promise<YoutubeReply> {
  if (!youtubeEnabled(env)) throw new ApiError(404, "not_available", "Reading a channel is not switched on.");
  const ref = parseChannel(input);
  await checkCaller(env, req, "youtube");
  if (!(await spendYoutubeUnits(env, UNITS_PER_READ, DAILY_UNIT_CAP, now))) throw new ApiError(429, "quota_exhausted", `Channel reading is used up for today.${TAIL}`);

  const ch = await yt(env, "channels", { part: "contentDetails", ...("forHandle" in ref ? { forHandle: ref.forHandle } : { id: ref.id }) });
  const uploads = ch.items?.[0]?.contentDetails?.relatedPlaylists?.uploads;
  if (typeof uploads !== "string" || !/^[A-Za-z0-9_-]{10,64}$/.test(uploads)) throw new ApiError(404, "channel_not_found", `No channel was found for that handle.${TAIL}`);
  const list = await yt(env, "playlistItems", { part: "snippet", playlistId: uploads, maxResults: "50" });
  const videos: { id: string; title: string }[] = [];
  for (const it of list.items ?? []) {
    const id = it.snippet?.resourceId?.videoId;
    if (typeof id === "string" && /^[A-Za-z0-9_-]{6,20}$/.test(id) && typeof it.snippet?.title === "string") videos.push({ id, title: it.snippet.title });
  }
  if (!videos.length) throw new ApiError(422, "no_videos", `That channel has no public videos to read.${TAIL}`);
  const meta = await yt(env, "videos", { part: "snippet", id: videos.map((v) => v.id).join(",") });
  const tags = new Map<string, string[]>();
  (meta.items as (YtItem & { id?: unknown })[] | undefined)?.forEach((it) => {
    if (typeof it.id === "string" && Array.isArray(it.snippet?.tags)) tags.set(it.id, (it.snippet.tags as unknown[]).filter((t): t is string => typeof t === "string").slice(0, 30));
  });
  const titles = cleanTitles(videos.map((v) => v.title));
  const docs = videos.map((v, i) => ({ text: titles[i] || v.title, tags: tags.get(v.id) ?? [] }));
  const { phrases } = extractPhrases(docs);
  return { ok: true, videos_read: videos.length, phrases: phrases.map((p: { text: string; with: number }) => ({ text: p.text, pages_with: p.with })) };
}

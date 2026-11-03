/**
 * The three public buttons of the profile page, and the switch that tells the page which of them exist:
 *
 *   GET  /v1/suggest/status    {resolve, site, youtube}   youtube is true only when the secret YOUTUBE_API_KEY is set
 *   POST /v1/resolve           {"text": "..."}            "Look up a word"       (sends this word to nicheclock)
 *   POST /v1/suggest/site      {"url": "..."}             "Read my site"         (sends this address to nicheclock)
 *   POST /v1/suggest/youtube   {"channel": "..."}         "Read my channel"      (sends this handle to nicheclock)
 *
 * No key and no account: the limits are per address (suggest-limits.ts). Same origin only (a foreign Origin is refused and the body must be
 * application/json, so another site cannot make a visitor's browser call these). No CORS headers. Privacy (condition (b)): nothing in these
 * handlers logs a URL, word, handle or profile; there is no console call in src/suggest*.ts (test/privacy.test.ts greps for it); every failure
 * is a fixed code and message that never contains the input; an unexpected error is turned into a fixed 500 here, before it can reach any
 * generic logger.
 */
import type { Env } from "./env";
import { ApiError, byteLength, errorReply, jsonReply, toResponse } from "./http";
import { originAllowed } from "./mcp";
import { checkCaller } from "./suggest-limits";
import { cleanText, resolveText } from "./suggest-resolve";
import { readSite } from "./suggest-site";
import { readChannel, youtubeEnabled } from "./suggest-youtube";

const MAX_BODY = 2048;

export const isSuggestPath = (p: string): boolean => p === "/v1/resolve" || p === "/v1/suggest/status" || p === "/v1/suggest/site" || p === "/v1/suggest/youtube";

async function body(req: Request): Promise<Record<string, unknown>> {
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) throw new ApiError(415, "unsupported_media_type", "send application/json");
  const text = await req.text();
  if (byteLength(text) > MAX_BODY) throw new ApiError(413, "too_large", "request body too large");
  let v: unknown;
  try {
    v = JSON.parse(text);
  } catch {
    throw new ApiError(400, "invalid_json", "body is not valid JSON");
  }
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new ApiError(400, "invalid_json", "body must be a JSON object");
  return v as Record<string, unknown>;
}

export async function handleSuggest(req: Request, env: Env, path: string, now = new Date()): Promise<Response> {
  try {
    if (path === "/v1/suggest/status") {
      if (req.method !== "GET") throw new ApiError(405, "method_not_allowed", "method not allowed");
      return toResponse(jsonReply({ resolve: true, site: true, youtube: youtubeEnabled(env) }));
    }
    if (req.method !== "POST") throw new ApiError(405, "method_not_allowed", "method not allowed", { allow: "POST" });
    if (!originAllowed(req, env) || req.headers.get("sec-fetch-site") === "cross-site") throw new ApiError(403, "forbidden", "same-origin requests only");
    const b = await body(req);
    if (path === "/v1/resolve") {
      const text = cleanText(b.text);
      await checkCaller(env, req, "resolve", now);
      const topic = await resolveText(env, text);
      return toResponse(jsonReply({ ok: true, topic }));
    }
    if (path === "/v1/suggest/site") return toResponse(jsonReply(await readSite(b.url, env, req, now)));
    return toResponse(jsonReply(await readChannel(b.channel, env, req, now)));
  } catch (e) {
    if (e instanceof ApiError) return toResponse(errorReply(e));
    return toResponse(errorReply(new ApiError(500, "internal", "internal error")));
  }
}

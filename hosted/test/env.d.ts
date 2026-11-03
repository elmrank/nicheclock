/// <reference types="vite/client" />
import type { D1Migration } from "@cloudflare/vitest-plugin";
import type { Env } from "../src/env";

declare global {
  namespace Cloudflare {
    interface Env {
      DB: D1Database;
      BUCKET: R2Bucket;
      ASSETS: Fetcher;
      PUBLIC_BASE_URL: string;
      DAILY_LIMIT: string;
      MINUTE_LIMIT: string;
      MAX_CUSTOM_NICHES: string;
      MAX_KEYS_PER_EMAIL: string;
      JOBS_DAILY_CAP: string;
      ALLOWED_ORIGINS: string;
      KEY_SIGNUP?: string;
      SITE_GATE?: string;
      ASSETS: Fetcher;
      GITHUB_CLIENT_ID?: string;
      GITHUB_CLIENT_SECRET?: string;
      SESSION_SECRET?: string;
      TEST_MIGRATIONS: D1Migration[];
    }
  }
}

export type { Env };

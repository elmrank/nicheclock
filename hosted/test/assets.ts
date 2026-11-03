/** A fake ASSETS binding that serves the files of test/shards/ (built from the page's fixture by scripts/make-test-shards.py). */
const files = import.meta.glob("./shards/*.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

export const shardFiles: Record<string, string> = Object.fromEntries(Object.entries(files).map(([k, v]) => [k.replace("./shards/", ""), v]));

export const assets = {
  fetch: async (r: Request) => {
    const p = new URL(r.url).pathname;
    const name = p.startsWith("/catalog-data/shards/") ? p.slice("/catalog-data/shards/".length) : "";
    const body = shardFiles[name];
    return body === undefined ? new Response("not found", { status: 404 }) : new Response(body, { headers: { "content-type": "application/json" } });
  },
} as unknown as Fetcher;

/** the page's own fixture pool, for the parity check (the whole pool, as the browser has it) */
const fixture = import.meta.glob("../site/trending/fixture/match/*.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
export const fixtureJson = (name: string) => JSON.parse(fixture[`../site/trending/fixture/match/${name}`] as string);

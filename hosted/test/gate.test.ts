import { describe, expect, it } from "vitest";
import { call } from "./helpers";

const assets = { fetch: async (r: Request) => new Response(`asset ${new URL(r.url).pathname}`, { headers: { "content-type": "text/plain" } }) } as unknown as Fetcher;
const env = { ASSETS: assets, BASIC_PASSWORD: "s3cret:pw", BASIC_USER: "example-user" };
const basic = (u: string, p: string) => ({ authorization: `Basic ${btoa(`${u}:${p}`)}` });

describe("static site Basic gate", () => {
  it("answers 401 with a challenge when no or wrong credentials are sent", async () => {
    for (const headers of [{}, basic("example-user", "nope"), basic("other", "s3cret:pw"), { authorization: "Basic %%%" }]) {
      const r = await call("/docs/", { headers, env });
      expect(r.status).toBe(401);
      expect(r.headers.get("www-authenticate")).toMatch(/^Basic /);
      expect(r.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    }
  });

  it("serves the asset after a valid login, for pages, images and the sitemap alike", async () => {
    for (const p of ["/", "/docs/img/report.png", "/sitemap.xml", "/privacy/"]) {
      const r = await call(p, { headers: basic("example-user", "s3cret:pw"), env });
      expect(r.status).toBe(200);
      expect(await r.text()).toBe(`asset ${p}`);
    }
  });

  it("keeps robots.txt public and leaves the API on its Bearer auth", async () => {
    expect((await call("/robots.txt", { env })).status).toBe(200);
    const api = await call("/v1/niches", { headers: basic("example-user", "s3cret:pw"), env });
    expect(api.status).toBe(401);
    expect((await api.json<any>()).error.code).toBe("unauthorized");
    expect((await call("/healthz", { env })).status).toBe(200);
  });

  it("fails closed when the password or the user name is not set", async () => {
    const r = await call("/", { headers: basic("example-user", ""), env: { ASSETS: assets, BASIC_PASSWORD: undefined, BASIC_USER: "example-user" } });
    expect(r.status).toBe(503);
    // no default user name: a password without BASIC_USER refuses everyone, whatever name is sent
    for (const user of ["", "example-user", "admin"]) {
      const r2 = await call("/", { headers: basic(user, "s3cret:pw"), env: { ASSETS: assets, BASIC_PASSWORD: "s3cret:pw" } });
      expect(r2.status).toBe(503);
    }
  });

  it("lets only the opened URLs be indexed (the homepage, /docs/what-is-nicheclock/, /docs/answers/ and /google-updates/): they answer index, follow, everything else noindex", async () => {
    const ok = { headers: basic("example-user", "s3cret:pw"), env };
    for (const p of ["/", "/docs/what-is-nicheclock/", "/docs/answers/", "/google-updates/"]) expect((await call(p, ok)).headers.get("x-robots-tag"), p).toBe("index, follow");
    for (const p of ["/terms/", "/privacy/", "/docs/", "/docs/api/", "/docs/what-is-nicheclock", "/docs/answers", "/google-updates", "/catalog-data/google-updates.json", "/demo/", "/catalog-data/trending.json", "/sitemap.xml", "/robots.txt", "/assets/x.js", "/missing", "/index.html"]) {
      expect((await call(p, ok)).headers.get("x-robots-tag"), p).toBe("noindex, nofollow");
    }
    // a failed login on "/" is a 401, never "index"
    expect((await call("/", { env })).headers.get("x-robots-tag")).toBe("noindex, nofollow");
  });

  it("keeps key issuance closed unless KEY_SIGNUP is on", async () => {
    for (const p of ["/keys", "/auth/github/login", "/keys/new"]) {
      const r = await call(p, { env: { ...env, KEY_SIGNUP: "off" } });
      expect(r.status, p).toBe(404);
      expect(r.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    }
  });

  it("SITE_GATE=off serves the static site without a login and still keeps the API on its Bearer auth", async () => {
    const open = { env: { ...env, SITE_GATE: "off", BASIC_PASSWORD: undefined } };
    const r = await call("/docs/", open);
    expect(r.status).toBe(200);
    expect(r.headers.get("x-robots-tag")).toBe("noindex, nofollow");
    expect((await call("/", open)).headers.get("x-robots-tag")).toBe("index, follow");
    expect((await call("/v1/niches", open)).status).toBe(401);
    // anything but "off" keeps the gate closed
    expect((await call("/docs/", { env: { ...env, SITE_GATE: "on" } })).status).toBe(401);
  });
});

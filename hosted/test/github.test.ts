import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Env } from "../src/env";
import { primaryVerifiedEmail, readSession, signSession } from "../src/github";
import { sha256Hex } from "../src/http";
import { BASE, call, db, json } from "./helpers";

const e = env as unknown as Env;

afterEach(() => vi.restoreAllMocks());

function mockGithub(opts: { emails?: unknown; token?: string | null; id?: number; login?: string } = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ url, init });
    if (url === "https://github.com/login/oauth/access_token") {
      return Response.json(opts.token === null ? { error: "bad_verification_code" } : { access_token: opts.token ?? "gho_fake" });
    }
    if (url === "https://api.github.com/user") return Response.json({ id: opts.id ?? 4242, login: opts.login ?? "octo" });
    if (url === "https://api.github.com/user/emails") {
      return Response.json(opts.emails ?? [
        { email: "secondary@example.org", primary: false, verified: true },
        { email: "Octo@Example.org", primary: true, verified: true },
      ]);
    }
    return new Response("unexpected " + url, { status: 500 });
  });
  return calls;
}

async function signIn(opts: Parameters<typeof mockGithub>[0] = {}) {
  const calls = mockGithub(opts);
  const login = await call("/auth/github/login");
  const state = new URL(login.headers.get("location") as string).searchParams.get("state") as string;
  const stateCookie = (login.headers.get("set-cookie") as string).split(";")[0] as string;
  const cb = await call(`/auth/github/callback?code=abc&state=${state}`, { headers: { cookie: stateCookie } });
  return { calls, cb, state };
}

function sessionCookie(res: Response): string {
  const sc = res.headers.getSetCookie().find((c) => c.startsWith("nk_session=") && !c.startsWith("nk_session=;"));
  return (sc as string).split(";")[0] as string;
}

describe("GitHub OAuth key issuance", () => {
  it("/auth/github/login redirects to GitHub with client id, scope user:email, state and a state cookie", async () => {
    const r = await call("/auth/github/login");
    expect(r.status).toBe(302);
    const u = new URL(r.headers.get("location") as string);
    expect(u.origin + u.pathname).toBe("https://github.com/login/oauth/authorize");
    expect(u.searchParams.get("client_id")).toBe("test-client-id");
    expect(u.searchParams.get("scope")).toBe("user:email");
    expect(u.searchParams.get("redirect_uri")).toBe("https://nicheclock.elmrank.com/auth/github/callback");
    const cookie = r.headers.get("set-cookie") as string;
    expect(cookie).toContain(`nk_oauth=${u.searchParams.get("state")}`);
    expect(cookie).toMatch(/HttpOnly/);
    expect(cookie).toMatch(/SameSite=Lax/);
    expect(cookie).toMatch(/Secure/);
  });

  it("answers 503 when the OAuth secrets are not configured", async () => {
    const r = await call("/auth/github/login", { env: { GITHUB_CLIENT_SECRET: undefined } });
    expect(r.status).toBe(503);
  });

  it("callback: rejects a missing/mismatched state without calling GitHub", async () => {
    const calls = mockGithub();
    const r1 = await call("/auth/github/callback?code=abc&state=zzz");
    expect(r1.status).toBe(400);
    const r2 = await call("/auth/github/callback?code=abc&state=zzz", { headers: { cookie: "nk_oauth=other" } });
    expect(r2.status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it("callback: takes the VERIFIED PRIMARY email, sets a session, discards the GitHub token", async () => {
    const { calls, cb } = await signIn();
    expect(cb.status).toBe(303);
    expect(cb.headers.get("location")).toBe("/keys");
    const tokenCall = calls.find((c) => c.url.endsWith("/access_token"));
    expect(JSON.parse(tokenCall?.init?.body as string)).toMatchObject({ client_id: "test-client-id", client_secret: "test-client-secret", code: "abc" });
    const session = await readSession(e, new Request(BASE, { headers: { cookie: sessionCookie(cb) } }));
    expect(session?.s).toMatchObject({ gid: 4242, email: "octo@example.org", login: "octo" });
    expect(sessionCookie(cb)).not.toContain("gho_fake");
  });

  it("callback: no verified primary email -> 403 and no session", async () => {
    const { cb } = await signIn({ emails: [{ email: "a@example.org", primary: true, verified: false }, { email: "b@example.org", primary: false, verified: true }] });
    expect(cb.status).toBe(403);
    expect(cb.headers.getSetCookie().some((c) => c.startsWith("nk_session=") && !c.startsWith("nk_session=;"))).toBe(false);
    expect(primaryVerifiedEmail([{ email: "a@example.org", primary: true, verified: true }])).toBe("a@example.org");
    expect(primaryVerifiedEmail(null)).toBeNull();
  });

  it("callback: GitHub refusing the code -> 400", async () => {
    const { cb } = await signIn({ token: null });
    expect(cb.status).toBe(400);
  });

  it("key page: create (shown once), list, revoke, 2-key limit", async () => {
    const { cb } = await signIn({ id: 9001, login: "pagetest", emails: [{ email: "Page@Example.org", primary: true, verified: true }] });
    const cookie = sessionCookie(cb);
    const page = await call("/keys", { headers: { cookie } });
    expect(page.status).toBe(200);
    expect(page.headers.get("content-security-policy")).toContain("default-src 'none'");
    const html = await page.text();
    expect(html).toContain("page@example.org");
    const csrf = /name="csrf" value="([0-9a-f]+)"/.exec(html)?.[1] as string;
    expect(csrf).toBeTruthy();

    const form = (path: string, token: string | null) => {
      const body = new URLSearchParams(token === null ? {} : { csrf: token });
      return call(path, { method: "POST", body: body.toString(), headers: { cookie, "content-type": "application/x-www-form-urlencoded" } });
    };
    expect((await form("/keys/new", null)).status).toBe(403); // no CSRF token
    expect((await form("/keys/new", "0".repeat(32))).status).toBe(403);
    expect((await call("/keys/new", { method: "POST", body: new URLSearchParams({ csrf }).toString(), headers: { "content-type": "application/x-www-form-urlencoded" } })).status).toBe(401); // no session

    const created = await form("/keys/new", csrf);
    expect(created.status).toBe(200);
    const createdHtml = await created.text();
    const key = /<pre>(nk_[A-Za-z0-9_-]+)<\/pre>/.exec(createdHtml)?.[1] as string;
    expect(key).toBeTruthy();
    expect(createdHtml).toContain("shown once");
    const row = await db().prepare("SELECT owner_github_id, email, hash, scope, plan FROM keys WHERE hash = ?").bind(await sha256Hex(key)).first<Record<string, unknown>>();
    expect(row).toMatchObject({ owner_github_id: 9001, email: "page@example.org", scope: "external", plan: "free" });
    expect((await call("/v1/keys", { key })).status).toBe(200); // the key works

    const list = await (await call("/keys", { headers: { cookie } })).text();
    expect(list).not.toContain(key); // never shown again
    const keyId = /<code>(k_[0-9a-f]{16})<\/code>/.exec(list)?.[1] as string;
    expect(list).toContain(keyId);

    expect((await form("/keys/new", csrf)).status).toBe(200); // second key
    const third = await form("/keys/new", csrf);
    expect(third.status).toBe(409); // limit of 2 active keys

    const rev = await form(`/keys/${keyId}/revoke`, csrf);
    expect(rev.status).toBe(303);
    expect((await call("/v1/keys", { key })).status).toBe(401);
    expect((await form("/keys/new", csrf)).status).toBe(200); // slot freed
    // someone else's key id
    expect((await form("/keys/k_0000000000000000/revoke", csrf)).status).toBe(404);
  });

  it("a forged or expired session cookie is not accepted", async () => {
    const good = await signSession(e, { gid: 1, login: "x", email: "x@example.org", exp: Math.floor(Date.now() / 1000) + 60 });
    const [payload] = good.split(".");
    expect(await readSession(e, new Request(BASE, { headers: { cookie: `nk_session=${good}` } }))).not.toBeNull();
    expect(await readSession(e, new Request(BASE, { headers: { cookie: `nk_session=${payload}.AAAA` } }))).toBeNull();
    const expired = await signSession(e, { gid: 1, login: "x", email: "x@example.org", exp: Math.floor(Date.now() / 1000) - 5 });
    expect(await readSession(e, new Request(BASE, { headers: { cookie: `nk_session=${expired}` } }))).toBeNull();
    const r = await call("/keys", { headers: { cookie: `nk_session=${payload}.AAAA` } });
    expect(await r.text()).toContain("Sign in with GitHub");
  });

  it("serves the sign-in page without a session", async () => {
    const r = await call("/keys");
    expect(r.status).toBe(200);
    expect(await r.text()).toContain("/auth/github/login");
    expect((await json(await call("/healthz"))).ok).toBe(true);
  });
});

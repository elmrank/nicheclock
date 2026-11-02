/* Proof that a "Your niche" profile stays in the browser (docs/personalization.md, section "Privacy").
   Puppeteer drives headless Chromium through the whole flow on the built site with sentinel strings that appear nowhere else,
   records every request, response and WebSocket frame (HAR 1.2), and asserts that no sentinel left the browser.

     node hosted/site/trending/test-privacy.js [--out DIR]       needs puppeteer-core (hosted devDependency) and a Chrome:
                                                                  CHROME=/path/to/chrome, else the one Playwright installed.
   Writes DIR/nicheclock-privacy.har and DIR/nicheclock-privacy.json (default /tmp/nicheclock-privacy). Exit code 1 on any failure.

   Allowed by the criterion (no user profile leaves the browser): GETs of the page's own static files, including the pool and the concept index (no query
   string, no profile in them). Everything else, in particular any POST, any other origin, any URL, header, body or frame that holds
   a sentinel, fails. The three "Sends ..." buttons (read my site, read my channel, look up a word) are not part of this build. */
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const argOut = process.argv.indexOf("--out");
const OUT = argOut > 0 ? process.argv[argOut + 1] : "/tmp/nicheclock-privacy";
fs.mkdirSync(OUT, { recursive: true });

const SENTINELS = { topic: "zzsentinelchess", site: "https://sentinel-host-7731.example.org", handle: "@zzsentinelhandle", name: "zzsentinelname", x: "@zzsentinelxhandle" };
const ALL = Object.values(SENTINELS).concat(["sentinel-host-7731"]);

function chromePath() {
  if (process.env.CHROME) return process.env.CHROME;
  const base = path.join(os.homedir(), ".cache", "ms-playwright");
  const dirs = fs.existsSync(base) ? fs.readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort() : [];
  for (const d of dirs.reverse()) { const p = path.join(base, d, "chrome-linux64", "chrome"); if (fs.existsSync(p)) return p; }
  throw new Error("no Chrome found: set CHROME=/path/to/chrome");
}

/* build the site from the data the page is normally built from, into a temp dir */
const DIST = fs.mkdtempSync(path.join(os.tmpdir(), "nc-privacy-"));
const b = spawnSync("python3", [path.join(here, "build.py"), "--out", DIST], { encoding: "utf-8" });
if (b.status !== 0) { console.error(b.stdout + b.stderr); process.exit(1); }

const headersBlock = (() => {
  const t = fs.readFileSync(path.join(here, "..", "static", "_headers"), "utf-8");
  const block = t.split("\n/*\n")[1].split(/^\//m)[0];
  return block.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]);
})();
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml", ".txt": "text/plain" };
const serverLog = [];
const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    serverLog.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString("utf-8") });
    if (req.method !== "GET") { res.writeHead(405); res.end(); return; }
    const p = decodeURIComponent(new URL(req.url, "http://x").pathname);
    let f = path.join(DIST, p.endsWith("/") ? p + "index.html" : p);
    if (!f.startsWith(DIST) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); res.end("not found"); return; }
    const h = { "content-type": TYPES[path.extname(f)] || "application/octet-stream", "cache-control": "no-store" };
    headersBlock.forEach(([k, v]) => { h[k.toLowerCase()] = v; });
    res.writeHead(200, h);
    res.end(fs.readFileSync(f));
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const ORIGIN = "http://127.0.0.1:" + server.address().port;

const browser = await puppeteer.launch({ executablePath: chromePath(), headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const client = await page.createCDPSession();
await client.send("Network.enable");
const dl = fs.mkdtempSync(path.join(os.tmpdir(), "nc-dl-"));
await (await browser.target().createCDPSession()).send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dl });

const entries = new Map(), frames = [], timeline = [];
client.on("Network.requestWillBeSent", (e) => {
  entries.set(e.requestId, { startedDateTime: new Date().toISOString(), request: { method: e.request.method, url: e.request.url, httpVersion: "HTTP/1.1", headers: Object.entries(e.request.headers).map(([name, value]) => ({ name, value: String(value) })),
    queryString: [...new URL(e.request.url).searchParams].map(([name, value]) => ({ name, value })), cookies: [], headersSize: -1, bodySize: e.request.postData ? e.request.postData.length : 0, postData: e.request.postData ? { mimeType: "text/plain", text: e.request.postData } : undefined },
    response: { status: 0, statusText: "", httpVersion: "HTTP/1.1", headers: [], cookies: [], content: { size: 0, mimeType: "" }, redirectURL: "", headersSize: -1, bodySize: -1 }, cache: {}, timings: { send: 0, wait: 0, receive: 0 }, time: 0 });
  timeline.push({ step: currentStep, method: e.request.method, url: e.request.url });
});
client.on("Network.requestWillBeSentExtraInfo", (e) => { const en = entries.get(e.requestId); if (en) en.extra = Object.entries(e.headers).map(([name, value]) => ({ name, value: String(value) })); });
client.on("Network.responseReceived", (e) => { const en = entries.get(e.requestId); if (en) { en.response.status = e.response.status; en.response.statusText = e.response.statusText || ""; en.response.headers = Object.entries(e.response.headers).map(([name, value]) => ({ name, value: String(value) })); en.response.content.mimeType = e.response.mimeType || ""; } });
client.on("Network.loadingFinished", (e) => { const en = entries.get(e.requestId); if (en) { en.response.bodySize = e.encodedDataLength; en.response.content.size = e.encodedDataLength; } });
client.on("Network.webSocketFrameSent", (e) => frames.push(e.response.payloadData));
client.on("Network.webSocketFrameReceived", (e) => frames.push(e.response.payloadData));
client.on("Network.webSocketCreated", (e) => frames.push("WS " + e.url));
const cspViolations = [];
page.on("console", (m) => { if (process.env.DEBUG) console.log("console:", m.type(), m.text().slice(0, 200)); if (/Content Security Policy|Refused to/i.test(m.text())) cspViolations.push(m.text()); });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));

let currentStep = "start";
const step = (s) => { currentStep = s; console.log("step: " + s); };
const poolRequests = () => timeline.filter((t) => /\/catalog-data\/match\/pool-/.test(t.url)).length;
const matchRequests = () => timeline.filter((t) => /\/catalog-data\/match\//.test(t.url)).map((t) => t.url.split("/").pop());
const failures = [];
const check = (name, fn) => { try { fn(); console.log("ok  " + name); } catch (e) { failures.push(name + ": " + e.message); console.log("FAIL " + name + ": " + e.message); } };

let racePassed = false;
try {
  step("race: click Your niche while the data files are still loading");
  const rp = await browser.newPage();
  await rp.setRequestInterception(true);
  rp.on("request", (r) => { if (/catalog-data\/meta\.json/.test(r.url())) setTimeout(() => r.continue(), 1500); else r.continue(); });
  await rp.goto(ORIGIN + "/", { waitUntil: "domcontentloaded" });
  await rp.waitForSelector("#nichebtn");                 // the bar is static HTML: a fast clicker can use it before any data file arrives
  await rp.click("#nichebtn");
  racePassed = await rp.waitForSelector("#topic", { timeout: 15000 }).then(() => true, () => false);
  await rp.close();

  step("load the page (no niche opened)");
  await page.goto(ORIGIN + "/#/trending", { waitUntil: "networkidle0" });
  await page.waitForSelector("table.trend");
  const before = { pool: poolRequests(), match: matchRequests(), niche: timeline.some((t) => /\/assets\/niche\./.test(t.url)) };

  step("open Your niche");
  await page.click("#nichebtn");
  await page.waitForSelector("#topic");
  await new Promise((r) => setTimeout(r, 800));
  const afterProfile = { pool: poolRequests(), match: matchRequests() };

  step("type a word that is not in the list, then a real topic");
  await page.type("#topic", SENTINELS.topic);
  await page.waitForSelector("#sug .note");
  await page.$eval("#topic", (e) => { e.focus(); e.select(); });
  await page.type("#topic", "chess");
  await page.waitForSelector('#sug [role="option"]');
  await page.keyboard.press("Enter");
  await page.waitForSelector(".chips-in .chip");
  await page.type("#pname", SENTINELS.name);
  await page.type("#site", SENTINELS.site);
  await page.type('[aria-label="X handle"]', SENTINELS.x);
  await page.type('[aria-label="YouTube channel"]', SENTINELS.handle);
  await page.evaluate(() => { document.querySelectorAll(".days label").forEach((l) => { if (/Tue|Fri/.test(l.textContent)) l.querySelector("input").click(); }); });
  await page.type("#perweek", "2");

  step("Show my niche");
  await page.click('[data-fk="save"]');
  await page.waitForSelector("tr.fitrow", { timeout: 20000 });
  const afterSave = { pool: poolRequests(), match: matchRequests() };
  const store = await page.evaluate(() => ({ keys: Object.keys(localStorage), session: sessionStorage.length, cookie: document.cookie, idb: indexedDB.databases ? null : "unsupported", raw: localStorage.getItem("nicheclock.profiles.v1") }));
  store.idb = await page.evaluate(async () => (indexedDB.databases ? (await indexedDB.databases()).length : 0));

  step("switch to Upcoming and change the region");
  await page.evaluate(() => { [...document.querySelectorAll('button[role="tab"]')].find((b) => /Upcoming/.test(b.textContent)).click(); });
  await page.waitForSelector(".mine");
  await new Promise((r) => setTimeout(r, 500));
  await page.evaluate(() => { [...document.querySelectorAll('button[role="tab"]')].find((b) => /Trending/.test(b.textContent)).click(); });
  await page.waitForSelector("tr.fitrow", { timeout: 20000 });
  const regions = await page.evaluate(() => [...document.querySelectorAll('select[aria-label="Region"] option')].map((o) => o.value));
  if (regions.indexOf("IN") >= 0) { await page.select('select[aria-label="Region"]', "IN"); await page.waitForSelector(".mine"); await new Promise((r) => setTimeout(r, 800)); }

  step("export, reload, import, delete");
  await page.click("#nichebtn");
  await page.waitForSelector("#topic");
  await page.evaluate(() => { [...document.querySelectorAll("button")].find((b) => b.textContent === "Export JSON").click(); });
  for (let i = 0; i < 50 && !fs.readdirSync(dl).some((f) => f.endsWith(".json")); i++) await new Promise((r) => setTimeout(r, 100));
  const files = fs.readdirSync(dl).filter((f) => f.endsWith(".json"));
  const exported = files.length ? fs.readFileSync(path.join(dl, files[0]), "utf-8") : "";
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("#topic");
  const input = await page.$('input[type="file"]');
  await input.uploadFile(path.join(dl, files[0] || "none"));
  await page.waitForFunction(() => /Imported/.test(document.querySelector(".msg").textContent), { timeout: 5000 });
  const del = async () => { await page.evaluate(() => { [...document.querySelectorAll("button")].find((b) => b.textContent === "Delete this profile").click(); }); await page.waitForFunction(() => /Deleted/.test(document.querySelector(".msg").textContent), { timeout: 5000 }); };
  await del();                                                 // the import added a second profile: the first delete leaves one
  const afterFirstDelete = await page.evaluate(() => JSON.parse(localStorage.getItem("nicheclock.profiles.v1")).profiles.length);
  await del();
  const afterDelete = await page.evaluate(() => ({ keys: Object.keys(localStorage), cookie: document.cookie }));

  /* ---- assertions ---- */
  const all = Array.from(entries.values());
  const blob = (en) => JSON.stringify([en.request.url, en.request.headers, en.extra || [], en.request.postData || ""]).toLowerCase();
  check("a click on Your niche before the data files have loaded still opens the profile page (hash route vs. app ready)", () => { assert.ok(racePassed); });
  check("before the niche is opened: no pool, no concept file, no niche module", () => { assert.equal(before.pool, 0); assert.deepEqual(before.match, []); assert.equal(before.niche, false); });
  check("the profile page fetches the topic list but no pool", () => { assert.equal(afterProfile.pool, 0); assert.ok(afterProfile.match.some((n) => /^concepts\./.test(n))); });
  check("saving fetches exactly one pool (the profile's country)", () => { assert.equal(afterSave.pool, 1); });
  check("every request is a GET to the page's own origin", () => {
    const bad = all.filter((en) => en.request.method !== "GET" || new URL(en.request.url).origin !== ORIGIN).map((en) => en.request.method + " " + en.request.url);
    assert.deepEqual(bad, []);
  });
  check("the server saw only GETs (and the same URLs)", () => { assert.deepEqual(serverLog.filter((s) => s.method !== "GET").map((s) => s.method + " " + s.url), []); });
  check("pool and concept GETs carry no query string", () => { all.filter((en) => /catalog-data\/match\//.test(en.request.url)).forEach((en) => assert.equal(new URL(en.request.url).search, "", en.request.url)); });
  check("no sentinel in any request URL, header or body (browser view)", () => { ALL.forEach((s) => all.forEach((en) => assert.ok(!blob(en).includes(s.toLowerCase()), s + " in " + en.request.url))); });
  check("no sentinel in anything the server received", () => { ALL.forEach((s) => serverLog.forEach((r) => assert.ok(!JSON.stringify(r).toLowerCase().includes(s.toLowerCase()), s + " in " + r.url))); });
  check("no WebSocket was opened and no frame holds a sentinel", () => { assert.deepEqual(frames, []); });
  check("no cookie is set or sent", () => {
    assert.equal(store.cookie, ""); assert.equal(afterDelete.cookie, "");
    all.forEach((en) => { assert.ok(!en.response.headers.some((h) => h.name.toLowerCase() === "set-cookie")); assert.ok(!(en.extra || []).some((h) => h.name.toLowerCase() === "cookie")); });
  });
  check("localStorage holds exactly one key; sessionStorage and IndexedDB are empty", () => { assert.deepEqual(store.keys, ["nicheclock.profiles.v1"]); assert.equal(store.session, 0); assert.equal(store.idb, 0); });
  check("the profile is in localStorage (the sentinels are stored there, nowhere else)", () => { assert.ok(store.raw.includes(SENTINELS.name) && store.raw.includes(SENTINELS.site) && store.raw.includes(SENTINELS.x)); assert.ok(!store.raw.includes(SENTINELS.topic)); });
  check("the exported file holds the profile (so the sentinels were really entered)", () => { assert.ok(exported.includes(SENTINELS.name) && exported.includes(SENTINELS.site) && exported.includes(SENTINELS.handle)); });
  check("Delete removes one profile; deleting the last clears the key", () => { assert.equal(afterFirstDelete, 1); assert.deepEqual(afterDelete.keys, []); });
  check("the CSP blocked nothing and the page raised no error", () => { assert.deepEqual(cspViolations, []); assert.deepEqual(pageErrors, []); });
  check("the build has no 'Sends' button (read my site / channel / look up): they are not shipped", () => { assert.ok(!all.some((en) => /suggest|resolve/.test(en.request.url))); });

  const har = { log: { version: "1.2", creator: { name: "nicheclock test-privacy.js", version: "1" }, pages: [], entries: all.map((en) => { const { extra, ...e } = en; return e; }) } };
  fs.writeFileSync(path.join(OUT, "nicheclock-privacy.har"), JSON.stringify(har, null, 1));
  fs.writeFileSync(path.join(OUT, "nicheclock-privacy.json"), JSON.stringify({
    origin: "(local static server of the built site)", sentinels: SENTINELS, requests: all.length, methods: [...new Set(all.map((e) => e.request.method))], origins: [...new Set(all.map((e) => new URL(e.request.url).origin))].map(() => "same as the page"),
    poolFetches: { beforeOpen: before.pool, onProfilePage: afterProfile.pool, afterSave: afterSave.pool }, matchFilesFetched: matchRequests(), failures, timeline: timeline.map((t) => t.step + ": " + t.method + " " + t.url.replace(ORIGIN, "")),
  }, null, 1));
} catch (e) {
  failures.push("flow: " + (e && e.stack || e));
  console.error(e);
} finally {
  await browser.close();
  server.close();
  fs.rmSync(DIST, { recursive: true, force: true });
}
if (failures.length) { console.error("\n" + failures.length + " failure(s):\n" + failures.join("\n")); process.exit(1); }
console.log("\nprivacy test passed; artefact: " + path.join(OUT, "nicheclock-privacy.har"));

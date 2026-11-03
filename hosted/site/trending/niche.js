/* niche.js: the "Your niche" page and the "For your niche" views of Trending now and Upcoming.
   Loaded by trending.js with a dynamic import() the first time one of them is opened, so a visitor who never opens it never
   downloads it, the concept index or a pool (docs/personalization.md). It reads static files of this site and writes only
   to localStorage; there is no fetch() to anything but those GET files and no request that carries the profile.

   trending.js passes ctx: {state, setState, data, base, u: {h, spark, pct, ...}, storage, showControl, profileChanged}. */
import { match, rowToEntity, eventToEntity, SORTS, FLAG, yourSlot, prepareAnchors } from "./match.js";
import * as P from "./profile.js";

const cache = { pools: {}, concepts: null, bridges: null, observances: null };
const REGION_LANGS = { US: [["en", "English"]], IN: [["en", "English"], ["hi", "Hindi"]], BR: [["pt", "Portuguese"], ["en", "English"]], ID: [["id", "Indonesian"], ["en", "English"]] };
const DAY_LABEL = { monday: "Mon", tuesday: "Tue", wednesday: "Wed", thursday: "Thu", friday: "Fri", saturday: "Sat", sunday: "Sun" };
const FORMAT_LABEL = { blog: "Blog", video: "Video", short: "Short", newsletter: "Newsletter" };

function getJson(url) {
  return fetch(url).then((r) => { if (!r.ok) throw new Error(url.split("/").pop() + " returned " + r.status); return r.json(); });
}
export function matchInfo(meta) { return (meta && meta.match) || null; }
export function poolRegions(meta) { const m = matchInfo(meta); return m ? Object.keys(m.regions || {}) : []; }

/* ---------- the three buttons that send something to nicheclock (docs/personalization.md, "What each button sends") ----------
   Each POST carries exactly its own field, {text}, {url} or {channel}: same origin, no cookie, no query string, only after a click. */
function post(path, body) {
  const bad = { ok: false, body: {} };
  return fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    .then((r) => r.json().then((j) => ({ ok: r.ok, body: j || {} }), () => bad), () => bad);
}
const fail = (r) => (r.body && r.body.error && r.body.error.message) || "That did not work just now. Type your topics instead.";
let statusP = null;
function loadStatus() {
  if (!statusP) statusP = fetch("/v1/suggest/status").then((r) => (r.ok ? r.json() : Promise.reject(new Error("status")))).catch(() => { statusP = null; return {}; });
  return statusP;
}
/* name (label or alias, lower case) -> {row: the most viewed concept row of the topic list with that name, exact: the name is its label, not an alias} */
function conceptsByName(ix) {
  if (!ix.names) {
    ix.names = new Map();
    const put = (n, r, exact) => { const cur = ix.names.get(n); if (!cur || (exact && !cur.exact) || (exact === cur.exact && r[3] > cur.row[3])) ix.names.set(n, { row: r, exact }); };
    ix.rows.forEach((r, i) => { put(ix.lower[i], r, true); ix.alias[i].forEach((a) => put(a, r, false)); });
  }
  return ix.names;
}

function loadConcepts(meta) {
  if (!cache.concepts) {
    cache.concepts = getJson(matchInfo(meta).concepts.file).then((doc) => {
      const rows = doc.rows, labels = new Map(), desc = new Map();
      (doc.labels || []).forEach((r) => labels.set(r[0], r[1]));           // names of the classes and occupations rows point at
      rows.forEach((r) => { labels.set(r[0], r[1]); desc.set(r[0], r[2]); });
      return { rows, labels, desc, lower: rows.map((r) => r[1].toLowerCase()), alias: rows.map((r) => r[4].map((a) => a.toLowerCase())), date: doc.date };
    });
    cache.concepts.catch(() => { cache.concepts = null; });
  }
  return cache.concepts;
}
function loadPool(meta, region) {
  if (!cache.pools[region]) {
    cache.pools[region] = getJson(matchInfo(meta).regions[region].file).then((doc) => ({ doc, entities: doc.rows.map(rowToEntity) }));
    cache.pools[region].catch(() => { delete cache.pools[region]; });
  }
  return cache.pools[region];
}
function loadObservances(meta) {
  const m = matchInfo(meta);
  if (!m || !m.observances) return Promise.resolve({ rows: [] });
  if (!cache.observances) { cache.observances = getJson(m.observances.file).catch(() => ({ rows: [] })); }
  return cache.observances;
}
/* yearly observances (match/observances.json rows) as upcoming events */
function observanceEvents(doc, meta) {
  const cats = {};
  (meta.categories || []).forEach((c) => { cats[c.id] = c.label; });
  return (doc.rows || []).map((r) => ({
    title: r[1], date: r[2], date_precision: "day", publish_by: new Date(Date.parse(r[2] + "T00:00:00Z") - doc.lag_days * 86400000).toISOString().slice(0, 10), lag_days: doc.lag_days,
    kind: "observance", category: r[5], category_label: cats[r[5]] || r[5], region: "global", source: doc.source, source_url: "https://www.wikidata.org/wiki/Q" + r[0], license: doc.license,
    qid: "Q" + r[0], popularity: r[4], recurrence: doc.recurrence, rule: r[3], niche: { h: r[6], f: r[7] } }));
}
function loadBridges(meta) {
  const m = matchInfo(meta);
  if (!m || !m.bridges) return Promise.resolve({ bridges: {} });
  if (!cache.bridges) { cache.bridges = getJson(m.bridges.file).catch(() => ({ bridges: {} })); }
  return cache.bridges;
}

/* typing: up to 6 concepts whose name (or alias) starts with, or has a word starting with, what was typed */
export function suggest(index, text, skip) {
  const q = String(text || "").trim().toLowerCase();
  if (q.length < 2) return [];
  const out = [];
  const rank = (i) => {
    const l = index.lower[i];
    if (l === q) return 0;
    if (l.startsWith(q)) return 1;
    if (l.indexOf(" " + q) >= 0) return 2;
    if (index.alias[i].some((a) => a === q || a.startsWith(q))) return 3;
    return -1;
  };
  for (let tier = 0; tier <= 3 && out.length < 6; tier++) {
    for (let i = 0; i < index.rows.length && out.length < 6; i++) {
      const r = index.rows[i];
      if (skip && skip.has("Q" + r[0])) continue;
      if (rank(i) === tier) out.push({ qid: "Q" + r[0], label: r[1], desc: r[2] });
    }
  }
  return out;
}

/* ---------- shared bits ---------- */

function privacyBox(h) {
  return h("div", { class: "privacy", role: "note" }, [
    h("b", { text: "Stays in this browser. " }),
    "The profile is stored on this device only (localStorage, no cookie). Nothing about it is sent to nicheclock: the page downloads the same public data files as everyone and matches them here. Three buttons are the only exception, and each sends one field, only when you press it: Look up a word sends the word, Read my site sends the address, Read my channel sends the handle. Nothing they receive is stored or logged. Delete the profile here when you are done; a private window forgets it when it closes. ",
    h("details", { class: "check" }, [h("summary", { text: "How to check this in your browser's network tab" }),
      h("p", { text: "Open the developer tools (F12), choose Network, then press Show my niche. You will see GET requests for the pool file and the topic list, with no query string, and no request that carries what you typed. Press one of the three buttons and you will see exactly one POST with only its own field." })])]);
}

/* the free-tier limits, said plainly where the buttons are (numbers: hosted/src/suggest-limits.ts, suggest-youtube.ts, wrangler.toml) */
function limitsBox(h) {
  return h("div", { class: "privacy limits", role: "note" }, [
    h("b", { text: "What the three buttons cost, and their limits. " }),
    "They run on nicheclock's free Cloudflare plan (100,000 requests a day for the whole site and 10 ms of processor time per request) and, for channels, on a free Google quota, so each one has a cap, and some caps are shared by everybody:",
    h("ul", {}, [
      h("li", { text: "Look up a word: 20 an hour from one address. A word that is in the topic list needs no outside call; any other word is also sent to Wikidata." }),
      h("li", { text: "Read my site: 5 an hour from one address, one read of the same site a minute, and at most 26 requests, 300 KB of sitemap and 15 seconds a read. It obeys robots.txt and says who it is (user agent nicheclock-suggest, see /bot)." }),
      h("li", { text: "Read my channel (shown only when it is switched on): 5 a day from one address, and 2,000 units of Google's free YouTube quota a day for everybody (3 units a read, about 666 reads)." })]),
    "When a limit is reached the page says so and typing your topics still works. Nothing is queued and nothing is charged."]);
}
function regionLabel(meta, id) {
  const r = ((meta && meta.regions) || []).filter((x) => x.id === id)[0];
  return r ? r.label : id;
}
function pickRegion(ctx, profile) {
  const have = poolRegions(ctx.data.meta);
  const want = ctx.state.region !== "all" ? ctx.state.region : profile && profile.region;
  return have.indexOf(want) >= 0 ? want : (have.indexOf(profile && profile.region) >= 0 ? profile.region : have[0]);
}
function currentProfile(ctx) {
  const { store } = P.loadStore(ctx.storage);
  return { store, profile: P.activeProfile(store) };
}
function persist(ctx, store) {
  if (!P.saveStore(ctx.storage, store)) return false;
  ctx.profileChanged();
  return true;
}

/* ---------- the profile page ---------- */

export function profilePage(ctx, host, notice) {
  const { h } = ctx.u, meta = ctx.data.meta;
  const info = matchInfo(meta);
  host.textContent = "";
  if (!info) {
    host.appendChild(h("div", { class: "page" }, [h("h1", { text: "Your niche" }), h("p", { class: "lede", text: "The niche data is not part of this build of the site, so a profile cannot be matched yet." })]));
    return;
  }
  const loaded = P.loadStore(ctx.storage);
  let store = loaded.store;
  const existing = P.activeProfile(store);
  const locale = (typeof Intl !== "undefined" && Intl.DateTimeFormat().resolvedOptions().locale) || "en-US";
  const guess = (locale.split("-")[1] || "").toUpperCase();
  const regions = poolRegions(meta);
  const blank = () => ({ schema: P.SCHEMA, id: P.newId(), name: "", updated: "", topics: [], avoid: [], region: regions.indexOf(guess) >= 0 ? guess : regions[0], language: "en", sites: [], channels: [], formats: [], cadence: {} });
  let draft = existing ? JSON.parse(JSON.stringify(existing)) : blank();
  let nameTouched = !!existing;
  let index = null;
  const msg = h("p", { class: "msg", role: "alert" });
  const say = (t, ok) => { msg.textContent = t || ""; msg.className = "msg" + (ok ? " ok" : ""); };

  /* topic input + suggestions + chips */
  const input = h("input", { id: "topic", type: "text", autocomplete: "off", role: "combobox", "aria-expanded": "false", "aria-controls": "sug", "aria-autocomplete": "list", placeholder: "For example: chess", "data-fk": "topic" });
  const sug = h("div", { class: "sugbox", id: "sug", role: "listbox", hidden: true });
  const chips = h("div", { class: "chips-in", "aria-label": "Your topics" });
  const hint = h("p", { class: "hint" });
  let options = [], active = -1;
  const skipSet = () => new Set(draft.topics.map((t) => t.qid));
  const closeSug = () => { sug.hidden = true; sug.textContent = ""; input.setAttribute("aria-expanded", "false"); options = []; active = -1; };
  const addTopic = (t) => {
    const r = P.addTopic(draft, { qid: t.qid, label: t.label, source: "typed" });
    if (r.error) { say(r.error); return; }
    draft = r.profile; input.value = ""; lookupBtn.hidden = true; closeSug(); say(""); drawChips(); if (!nameTouched) nameInput.value = draft.topics[0].label.charAt(0).toUpperCase() + draft.topics[0].label.slice(1) + " niche";
    input.focus();
  };
  function drawSug() {
    sug.textContent = "";
    if (!index) { sug.hidden = false; sug.appendChild(h("div", { class: "note", text: "Loading the topic list\u2026" })); return; }
    options = suggest(index, input.value, skipSet());
    if (!options.length) {
      if (input.value.trim().length < 2) { closeSug(); return; }
      sug.hidden = false; input.setAttribute("aria-expanded", "true");
      sug.appendChild(h("div", { class: "note", text: "Not in the topic list. Try the broader word, such as the sport, the subject or the species group itself, or look the word up." }));
      lookupBtn.textContent = "Look up \u201C" + input.value.trim() + "\u201D (sends this word to nicheclock)"; lookupBtn.hidden = false;
      return;
    }
    sug.hidden = false; input.setAttribute("aria-expanded", "true");
    options.forEach((o, i) => sug.appendChild(h("div", { role: "option", id: "sug-" + i, "aria-selected": String(i === active), onmousedown: (e) => { e.preventDefault(); addTopic(o); } },
      [h("b", { text: o.label }), " ", h("span", { class: "d", text: o.desc })])));
  }
  input.addEventListener("input", () => { active = -1; lookupBtn.hidden = true; drawSug(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (options.length) { active = (active + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length; drawSug(); } }
    else if (e.key === "Enter") { e.preventDefault(); const o = options[active >= 0 ? active : 0]; if (o) addTopic(o); }
    else if (e.key === "Escape") closeSug();
  });
  input.addEventListener("blur", () => setTimeout(closeSug, 120));

  function drawChips() {
    chips.textContent = "";
    draft.topics.forEach((t) => chips.appendChild(h("span", { class: "chip" + (t.role === "main" ? " main" : "") }, [
      t.label, " ",
      h("button", { type: "button", class: "role", title: t.role === "main" ? "The main topic" : "Make this the main topic", "aria-label": t.label + ": " + t.role + (t.role === "main" ? "" : ", press to make it the main topic"), text: t.role,
        onclick: () => { if (t.role !== "main") { draft.topics = draft.topics.map((x) => Object.assign({}, x, { role: x.qid === t.qid ? "main" : "also" })); drawChips(); } } }),
      h("button", { type: "button", "aria-label": "Remove " + t.label, text: "\u00D7", onclick: () => { draft = P.removeTopic(draft, t.qid); drawChips(); input.focus(); } })])));
    hint.textContent = draft.topics.length ? "Add up to " + P.MAX_TOPICS + " topics; the one marked main counts fully, the others count 0.8. The grey line in the list is the Wikidata meaning." : "Type a word and pick the meaning you intend from the list.";
    drawBridges();
  }
  /* step 2: bridges, "people who write about X also cover", for a hobby or field that Wikidata does not tie to its subject */
  const bridgeBox = h("div", { class: "bridges", hidden: true });
  /* "Look up a word": POST /v1/resolve {text}, only after a click */
  const lookupBox = h("div", { class: "found", hidden: true, "aria-live": "polite" });
  const lookupBtn = h("button", { type: "button", class: "btn2", hidden: true, "data-fk": "lookup", onclick: () => doLookup(input.value.trim()) });
  function doLookup(text) {
    lookupBox.hidden = false; lookupBox.textContent = "";
    if (lookupBox.scrollIntoView) lookupBox.scrollIntoView({ block: "nearest" });
    lookupBox.appendChild(h("p", { class: "hint", text: "Looking up the word\u2026" }));
    post("/v1/resolve", { text }).then((r) => {
      lookupBox.textContent = "";
      if (!r.ok) { lookupBox.appendChild(h("p", { class: "msg", role: "alert", text: fail(r) })); return; }
      const t = r.body.topic;
      if (!t) { lookupBox.appendChild(h("p", { class: "hint", text: "Nothing matches that word. Try a broader or more common word." })); return; }
      const one = (c) => h("div", { class: "opt" }, [h("b", { text: c.label }), " ", h("span", { class: "d dim", text: c.description || "" }), " ",
        h("button", { type: "button", class: "btn2", text: "Add", title: "Add " + c.label + " as a topic", onclick: () => { lookupBox.hidden = true; addTopic({ qid: c.qid, label: c.label }); } })]);
      lookupBox.appendChild(h("p", { class: "hint", text: (t.source === "wikidata" ? "From Wikidata. " : "") + (t.ambiguous ? "More than one thing has this name. Pick the meaning you intend:" : "Pick the meaning you intend:") }));
      lookupBox.appendChild(one(t));
      (t.alternatives || []).forEach((c) => lookupBox.appendChild(one(c)));
    });
  }
  function drawBridges() {
    bridgeBox.textContent = "";
    bridgeBox.hidden = true;
    if (!draft.topics.length) return;
    loadBridges(meta).then((b) => {
      loadConcepts(meta).then((ix) => {
        const have = new Set(draft.topics.map((t) => t.qid));
        const seen = new Set();
        const found = [];
        draft.topics.forEach((t) => ((b.bridges || {})[t.qid] || []).forEach((x) => {
          const q = "Q" + x[0];
          if (!have.has(q) && !seen.has(q) && ix.labels.has(x[0])) { seen.add(q); found.push({ qid: q, label: ix.labels.get(x[0]), from: t.label, n: x[1] }); }
        }));
        if (!found.length) return;
        found.sort((p, q) => q.n - p.n);
        bridgeBox.hidden = false;
        bridgeBox.appendChild(h("p", { class: "hint", text: "People who write about " + draft.topics[0].label + " also cover (from the links of its Wikipedia article). Click to add:" }));
        bridgeBox.appendChild(h("div", { class: "chips-in" }, found.slice(0, 6).map((f) => h("button", { type: "button", class: "chip add", title: "Linked from the " + f.from + " article; " + f.n + " pages in the data are about it", text: "+ " + f.label, onclick: () => addTopic(f) }))));
      });
    });
  }

  /* the rest of the form */
  const nameInput = h("input", { id: "pname", type: "text", maxlength: "60", value: draft.name, placeholder: "Chess notes", oninput: (e) => { nameTouched = true; draft.name = e.target.value; } });
  const { C, menuEl, icon } = ctx.u;
  const langOpts = () => REGION_LANGS[draft.region] || [["en", "English"]];
  const langHost = h("div", { class: "mhost" });
  const drawLang = () => { langHost.textContent = ""; langHost.appendChild(menuEl(C.menu("lang", "", draft.language, null, langOpts(), null, { always: true, full: true, domId: "lang" }), (v) => { draft.language = v; })); };
  const regionSel = menuEl(C.menu("country", "", draft.region, null, regions.map((r) => [r, regionLabel(meta, r)]), null, { always: true, full: true, domId: "country" }),
    (v) => { draft.region = v; draft.language = langOpts()[0][0]; drawLang(); });
  drawLang();
  const chan = (platform, label, ph) => {
    const cur = draft.channels.filter((c) => c.platform === platform)[0];
    return h("input", { type: "text", "aria-label": label, placeholder: ph, value: cur ? cur.handle : "", maxlength: "120", autocomplete: "off",
      oninput: (e) => { draft.channels = draft.channels.filter((c) => c.platform !== platform).concat(e.target.value.trim() ? [{ platform, handle: e.target.value.trim() }] : []); } });
  };
  const site = h("input", { id: "site", type: "text", placeholder: "https://example.org", value: (draft.sites[0] || {}).url || "", autocomplete: "off", inputmode: "url",
    oninput: (e) => { draft.sites = e.target.value.trim() ? [{ url: e.target.value.trim() }] : []; } });
  /* "Read my site" and "Read my channel": each sends only its own field, and only after a click */
  const found = h("div", { class: "found", hidden: true, "aria-live": "polite" });
  const ytInput = chan("youtube", "YouTube channel", "YouTube @handle");
  function showFound(heading, reply, source, pages) {
    found.hidden = false; found.textContent = "";
    found.appendChild(h("h3", { text: heading }));
    const phrases = reply.phrases || [];
    if (!phrases.length) { found.appendChild(h("p", { class: "hint", text: "No topic word appeared in at least two pages. Type your topics instead." })); return; }
    loadConcepts(meta).then((ix) => {
      const names = conceptsByName(ix);
      let room = P.MAX_TOPICS - draft.topics.length;
      const items = phrases.map((p) => {
        const hit = names.get(p.text), c = hit && hit.row, have = !!c && draft.topics.some((t) => t.qid === "Q" + c[0]);
        // ticked only when the phrase is the topic's own name; a match through an alias ("endgame" for a film) is shown, unticked, for the person to judge
        const box = c && !have ? h("input", { type: "checkbox", "aria-label": "Add " + c[1], checked: hit.exact && room-- > 0 }) : null;
        return { p, c, box, have };
      });
      const list = h("ul", { class: "foundlist" }, items.map((i) => h("li", {}, [
        i.box || (i.have ? h("span", { class: "dim", text: "added " }) : null), h("b", { text: i.c ? i.c[1] : i.p.text }), " ",
        i.c ? h("span", { class: "d dim", text: i.c[2] }) : h("button", { type: "button", class: "btn2", text: "Look up (sends this word to nicheclock)", onclick: () => doLookup(i.p.text) }), " ",
        h("span", { class: "dim", text: "in " + i.p.pages_with + " of " + pages + " " + (source === "youtube" ? "videos" : "pages") })])));
      found.appendChild(list);
      found.appendChild(h("p", { class: "hint", text: "Nothing is added without a tick. A word with no meaning shown is not in the topic list: look it up, or leave it." }));
      found.appendChild(h("button", { type: "button", class: "btn2", "data-fk": "add-ticked", text: "Add ticked", onclick: () => {
        let n = 0;
        for (const i of items) {
          if (!i.box || !i.box.checked) continue;
          const r = P.addTopic(draft, { qid: "Q" + i.c[0], label: i.c[1], source });
          if (r.error) { say(r.error); break; }
          draft = r.profile; n++;
        }
        drawChips(); found.hidden = true; say("Added " + n + " topic" + (n === 1 ? "" : "s") + ".", true);
      } }));
    });
  }
  const readSite = h("button", { type: "button", class: "btn2", "data-fk": "read-site", text: "Read my site (sends this address to nicheclock)", onclick: () => {
    const url = site.value.trim();
    if (!url) { say("Type the address of your site first."); return; }
    say(""); found.hidden = false; found.textContent = "";
    found.appendChild(h("p", { class: "hint", text: "Reading the site: robots.txt, one sitemap, up to 8 page titles. This can take up to 15 seconds\u2026" }));
    readSite.disabled = true;
    post("/v1/suggest/site", { url }).then((r) => {
      readSite.disabled = false;
      if (!r.ok) { found.textContent = ""; found.appendChild(h("p", { class: "msg", role: "alert", text: fail(r) })); return; }
      showFound("Topic words found on your site", r.body, "site", r.body.pages_read);
    });
  } });
  const readYt = h("button", { type: "button", class: "btn2", "data-fk": "read-channel", hidden: true, text: "Read my channel (sends this handle to nicheclock)", onclick: () => {
    const channel = ytInput.value.trim();
    if (!channel) { say("Type your YouTube handle first, for example @example-chess."); return; }
    say(""); found.hidden = false; found.textContent = "";
    found.appendChild(h("p", { class: "hint", text: "Reading your latest videos\u2026" }));
    readYt.disabled = true;
    post("/v1/suggest/youtube", { channel }).then((r) => {
      readYt.disabled = false;
      if (!r.ok) { found.textContent = ""; found.appendChild(h("p", { class: "msg", role: "alert", text: fail(r) })); return; }
      showFound("Topic words found on your channel", r.body, "youtube", r.body.videos_read);
    });
  } });
  loadStatus().then((s) => { if (s && s.youtube) readYt.hidden = false; });
  const fmt = h("fieldset", { class: "checks field" }, [h("legend", { class: "sr", text: "Content types" })].concat(P.FORMATS.map((f) =>
    h("label", { class: "tg" }, [h("input", { type: "checkbox", class: "sr", checked: draft.formats.indexOf(f) >= 0, onchange: (e) => { draft.formats = P.FORMATS.filter((x) => (x === f ? e.target.checked : draft.formats.indexOf(x) >= 0)); } }), icon("check", "tick"), FORMAT_LABEL[f]]))));
  const perWeek = h("input", { id: "perweek", type: "number", min: "1", max: "14", value: (draft.cadence || {}).per_week || "", placeholder: "2",
    oninput: (e) => { const n = Math.round(Number(e.target.value)); draft.cadence = Object.assign({}, draft.cadence, { per_week: n >= 1 && n <= 14 ? n : undefined }); } });
  const days = h("fieldset", { class: "checks field days" }, [h("legend", { class: "sr", text: "Usual days" })].concat(P.DAYS.map((d) =>
    h("label", { class: "tg" }, [h("input", { type: "checkbox", class: "sr", checked: ((draft.cadence || {}).days || []).indexOf(d) >= 0, onchange: (e) => {
      const cur = ((draft.cadence || {}).days || []).filter((x) => x !== d).concat(e.target.checked ? [d] : []);
      draft.cadence = Object.assign({}, draft.cadence, { days: P.DAYS.filter((x) => cur.indexOf(x) >= 0) }); } }), icon("check", "tick"), DAY_LABEL[d]]))));

  const field = (label, id, control, hintText) => h("div", { class: "field" }, [h("label", { for: id, text: label }), control, hintText ? h("p", { class: "hint", text: hintText }) : null]);
  const samples = h("p", { class: "samples dim" }, ["Try a sample: "].concat(P.SAMPLES.map((s) => h("a", { href: "#/niche", "data-fk": "sample-" + s.key, onclick: (e) => {
    e.preventDefault();
    const sp = P.sampleProfile(s.key, draft.region);
    const r = P.upsert(store, sp);
    if (r.error) { say(r.error); return; }
    store = r.store; persist(ctx, store);
    ctx.setState({ tab: "trending", fit: true, region: "all", nsort: "fit", nmode: "both", open: "" }, false);
  }, text: s.key }))));

  function save() {
    draft.name = (nameInput.value || "").trim() || (draft.topics[0] ? draft.topics[0].label + " niche" : "My niche");
    const v = P.validate(Object.assign({}, draft, { cadence: cleanCadence(draft.cadence) }));
    if (!v.ok) { say(v.errors.join(" ")); return null; }
    const r = P.upsert(store, v.profile);
    if (r.error) { say(r.error); return null; }
    store = r.store;
    if (!persist(ctx, store)) { say("This browser would not store the profile (private mode or storage blocked). You can still export it as a file."); return null; }
    return v.profile;
  }
  const cleanCadence = (c) => { const o = {}; if (c && c.per_week >= 1) o.per_week = c.per_week; if (c && c.days && c.days.length) o.days = c.days; return Object.keys(o).length ? o : undefined; };
  const fileInput = h("input", { type: "file", accept: "application/json,.json", class: "sr", tabindex: "-1", "aria-label": "Import a profile file", onchange: (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    f.text().then((txt) => {
      const v = P.importJson(txt);
      if (!v.ok) { say(v.errors.join(" ")); return; }
      const r = P.upsert(store, v.profile);
      if (r.error) { say(r.error); return; }
      store = r.store; persist(ctx, store);
      profilePage(ctx, host, "Imported " + v.profile.name + (v.dropped.length ? " (left out: " + v.dropped.join(", ") + ")" : "") + ".");
    });
  } });
  const switcher = store.profiles.length ? h("div", { class: "field profiles" }, [h("label", { for: "pswitch", text: "Profile" }),
    h("div", { class: "inline2" }, [menuEl(C.menu("pswitch", "", existing ? store.active : "", null,
      (existing ? [] : [["", "(new profile, not saved yet)"]]).concat(store.profiles.map((p) => [p.id, p.name])), null, { always: true, full: true, domId: "pswitch" }),
      (v) => { if (!v) return; store = Object.assign({}, store, { active: v }); persist(ctx, store); profilePage(ctx, host); }),
    h("button", { type: "button", class: "btn2", text: "New profile", onclick: () => { store = Object.assign({}, store, { active: "" }); persist(ctx, store); profilePage(ctx, host); } })])]) : null;

  const page = h("div", { class: "page" }, [
    h("h1", { text: "Your niche" }),
    h("p", { class: "lede", text: "Tell nicheclock what you write about and it lists the rising topics and dated events that fit, with the reason for each. The profile is saved in this browser only." }),
    samples,
    h("p", { class: "hint coverage", text: "Works best for topics that Wikidata describes in detail: sports, games, film, music and species. It is weaker for hobbies and ingredients: in our check on 2 November 2026 (United States), 14 of the top 20 birdwatching rows and 2 of the 9 sourdough rows were on topic, and India and Brazil have too few pages for a small niche. Add what you write about, not only the hobby." }),
    switcher,
    h("div", { class: "field" }, [h("label", { for: "topic", text: "What do you write about?" }), input, sug, h("div", { class: "btnrow" }, [lookupBtn]), lookupBox, chips, hint, bridgeBox]),
    h("div", { class: "row2" }, [field("Country", "country", regionSel), field("Language", "lang", langHost)]),
    h("p", { class: "hint", text: "Only " + regions.map((r) => regionLabel(meta, r)).join(", ") + " have enough pages in Wikimedia's per-country pageview files for a pool; the other countries have too few or none." }),
    h("div", { class: "field" }, [h("label", { for: "pname", text: "Name this profile" }), nameInput]),
    h("div", { class: "cluster" }, [
      h("h2", { text: "Channels and site" }),
      field("Your website", "site", site, "Stored as typed. The button below reads it once: robots.txt, one sitemap and up to 8 page titles, with the user agent nicheclock-suggest (see /bot), and shows the topic words it finds. Nothing is kept."),
      h("div", { class: "btnrow" }, [readSite]),
      h("div", { class: "field" }, [h("span", { class: "lbl", text: "Other channels" }), h("div", { class: "row2" }, [chan("x", "X handle", "X @handle"), chan("tiktok", "TikTok handle", "TikTok @handle")]),
        h("div", { class: "row2" }, [chan("pinterest", "Pinterest handle", "Pinterest @handle"), ytInput]),
        h("div", { class: "btnrow" }, [readYt]),
        h("p", { class: "hint", text: "Stored as typed and shown in the exported file. X, TikTok and Pinterest are never fetched. The YouTube button appears only when channel reading is switched on; it asks YouTube for the titles and tags of your latest 50 videos." })])]),
    found,
    h("div", { class: "cluster" }, [
      h("h2", { text: "What you publish" }), fmt,
      h("div", { class: "row2" }, [field("Posts per week", "perweek", perWeek), h("div", { class: "field" }, [h("span", { class: "lbl", text: "Usual days" }), days])]),
      h("p", { class: "hint", text: "Usual days add a \"Your slot\" column to Upcoming. Without them it shows the publish-by date only." })]),
    h("div", { class: "actions" }, [
      h("button", { type: "button", class: "btn", "data-fk": "save", text: "Show my niche", onclick: () => { const p = save(); if (p) ctx.setState({ tab: "trending", fit: true, region: "all", nsort: "fit", nmode: "both", open: "" }, false); } }),
      h("button", { type: "button", class: "btn2", text: "Export JSON", onclick: () => {
        const p = save(); if (!p) return;
        const a = h("a", { href: URL.createObjectURL(new Blob([P.exportJson(p)], { type: "application/json" })), download: "nicheclock-profile-" + p.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + ".json" });
        document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0); say("Exported " + p.name + ".", true); } }),
      h("button", { type: "button", class: "btn2", text: "Import JSON", onclick: () => fileInput.click() }), fileInput,
      h("button", { type: "button", class: "btn2", text: "Delete this profile", onclick: () => {
        const id = (P.activeProfile(store) || {}).id;
        if (id) { store = P.remove(store, id); if (store.profiles.length) persist(ctx, store); else P.clearStore(ctx.storage); }
        ctx.profileChanged(); profilePage(ctx, host, "Deleted."); } })]),
    msg,
    limitsBox(h),
    privacyBox(h)
  ]);
  host.appendChild(page);
  drawChips();
  loadConcepts(meta).then((ix) => { index = ix; if (document.activeElement === input) drawSug(); }).catch((e) => say("The topic list did not load: " + e.message));
  if (notice) say(notice, true);
  if (loaded.damaged) say("The saved profile could not be read, so it was ignored.");
  if (loaded.blocked) say("This browser blocks storage, so a profile cannot be kept between visits. You can still use it and export it.");
}

/* ---------- For your niche: shared strip ---------- */

function strip(ctx, profile, region) {
  const { h, tags } = ctx.u;
  return h("div", { class: "mine" }, [h("b", { text: profile.name }),
    tags(profile.topics.map((t) => ({ text: t.label, main: t.role === "main" }))),
    h("span", { class: "dim", text: regionLabel(ctx.data.meta, region) + " \u00B7 " + (profile.language === "en" ? "English" : profile.language) }),
    h("span", { class: "grow" }), h("a", { href: "#/niche", text: "Edit niche" })]);
}
function noProfile(ctx) {
  const { h } = ctx.u;
  return ctx.u.emptyState("No niche yet.", "Tell nicheclock what you write about to see the topics that fit. It takes about 30 seconds and stays in this browser.", h("a", { class: "btn", href: "#/niche", text: "Set up your niche" }));
}
/* Fit is a number from 0 to 100 in a numeric column, like Rise */
function fitCell(u, fit) {
  return u.h("td", { class: "num fitc", "data-label": "Fit", title: "Fit for your niche, 0 to 100: how directly Wikidata ties this topic to yours. Not search volume." }, [u.h("span", { class: "rv", text: String(Math.round(fit * 100)) })]);
}
function relationText(rel, anchor, labels) {
  const via = labels.get(rel.via);
  const nameOf = { same: "The topic itself", class: "A kind of " + anchor.label + " (Wikidata class)", occupation: "Occupation: " + anchor.label, named: "Class or occupation named like the topic: " + (via || "?"),
    subject: "Subject, sport, series or part-of tie to " + anchor.label, taxon: "Member of the taxon " + anchor.label, title: "The page title contains \"" + anchor.label + "\"" };
  return nameOf[rel.kind] + ": " + Math.round(rel.score * 100) / 100;
}

/* ---------- For your niche: Trending ---------- */

export function trendingView(ctx, host) {
  const { h } = ctx.u, meta = ctx.data.meta;
  host.textContent = "";
  const { profile } = currentProfile(ctx);
  if (!matchInfo(meta)) { host.appendChild(ctx.u.emptyState("The niche data is not part of this build.")); return; }
  if (!profile) { host.appendChild(noProfile(ctx)); return; }
  const region = pickRegion(ctx, profile);
  host.appendChild(h("p", { class: "state", role: "status", text: "Loading your niche\u2026" }));
  Promise.all([loadPool(meta, region), loadConcepts(meta), loadBridges(meta)]).then((r) => {
    if (!host.isConnected) return;
    draw(ctx, host, profile, region, r[0], r[1], r[2]);
  }).catch((e) => {
    if (!host.isConnected) return;
    host.textContent = "";
    host.appendChild(ctx.u.emptyState("The niche data did not load.", String(e && e.message || e), h("button", { type: "button", class: "btn", text: "Try again", onclick: () => trendingView(ctx, host) })));
  });
}

function draw(ctx, host, profile, region, pool, ix, br) {
  const { h } = ctx.u, meta = ctx.data.meta, st = ctx.state;
  host.textContent = "";
  const res = match(profile, pool.entities, (q) => ix.labels.get(q));
  const mode = st.nmode, sortFn = SORTS[st.nsort] || SORTS.fit;
  const keep = (r) => mode === "both" || (mode === "rising") === !!(r.entity.flags & FLAG.RISING);
  const fits = res.fits.filter(keep).sort(sortFn), weaker = res.weaker.filter(keep).sort(sortFn);
  const nRising = res.fits.filter((r) => r.entity.flags & FLAG.RISING).length;
  const regions = poolRegions(meta);
  const label = regionLabel(meta, region);

  const { C } = ctx.u;
  host.appendChild(ctx.u.filterBar([
    ctx.showControl(),
    C.menu("region", "Region", region, region, regions.map((r) => [r, regionLabel(meta, r)]), (v) => ({ region: v, open: "" }), { always: true, row: 1 }),
    C.seg("nmode", "Rows", mode, "both", [["both", "Both"], ["rising", "Rising"], ["steady", "Steady / Top"]], (v) => ({ nmode: v, open: "" }), { row: 1 }),
    C.menu("nsort", "Sort", st.nsort, "fit", [["fit", "Relevance for you"], ["rise", "Biggest rise"], ["volume", "Highest volume"]], (v) => ({ nsort: v }), { always: true, row: 1, end: true })], { noun: "topics" }));
  host.appendChild(strip(ctx, profile, region));

  const word = profile.topics[0].label;
  host.appendChild(h("div", { class: "mline" }, [h("p", { class: "count", role: "status", text: res.fits.length + " topic" + (res.fits.length === 1 ? "" : "s") + " fit " + word + " in the past 7d (" + label + "): " + nRising + " rising, " + (res.fits.length - nRising) + " steady / top. Fit is a score from 0 to 100 for how directly Wikidata ties the topic to yours; it is not search volume." })]));
  if (res.duplicates) host.appendChild(h("p", { class: "dim small", text: res.duplicates + " duplicate row" + (res.duplicates === 1 ? "" : "s") + " merged by Wikidata id." }));
  if (res.fits.length < 10) {
    host.appendChild(h("p", { class: "limited", role: "note" }, [h("b", { text: res.fits.length + " page" + (res.fits.length === 1 ? "" : "s") + " fit this week. " }),
      res.fits.length < 5 ? "Few topics fit this word: Wikidata does not always tie a hobby or an ingredient to what it is about. Add what you write about, for example the subject itself. " : "A small niche has few pages above the view floor; nothing is padded. ",
      bridgeLine(ctx, profile, ix, br)]));
  }
  if (!res.fits.length) {
    host.appendChild(ctx.u.emptyState("Nothing in the last 7 days fits " + word + " in " + label + ".", res.near.length ? "Closest, below the 35 threshold:" : "Add a second topic or a broader one."));
    if (res.near.length) host.appendChild(tableOf(ctx, res.near.map((r) => Object.assign({ grey: true }, r)), profile, ix, true));
  } else if (!fits.length) {
    host.appendChild(ctx.u.emptyState("No rows in this selection.", "Switch Rows to Both.", h("button", { type: "button", class: "btn", text: "Show both", onclick: () => ctx.setState({ nmode: "both", open: "" }) })));
  } else {
    host.appendChild(tableOf(ctx, fits, profile, ix, false));
  }
  if (weaker.length) {
    host.appendChild(h("details", { class: "weaker", open: weaker.some((r) => "q" + r.entity.qid === st.open) }, [h("summary", { text: "Weaker fits (35 to 49), " + weaker.length + " topics" }), tableOf(ctx, weaker, profile, ix, true)]));
  }
  host.appendChild(h("details", { class: "how" }, [h("summary", { text: "How fit is worked out" }),
    h("p", { text: "Each row is a Wikipedia page that was rising or had at least 200 views on the data day in this country. Wikidata ties it to your topic in one or more ways, each with a fixed score: the same item 1.00; a kind of your topic 0.70 to 0.84; an occupation 0.85 (diluted when a person has many); a class named like your topic 0.60; a subject, sport or series tie 0.80 (0.45 for a person); a taxon group 0.55 to 0.90; the title containing your topic 0.40. Ties combine as 1 minus the product of what is left, a second topic counts 0.8, and a few guards cap people with many occupations and fictional things. There is no model and no search volume; the same data gives the same list." }),
    h("p", { text: "Fit at least 50 is listed; 35 to 49 is under Weaker fits; below 35 is not shown. Click a row to see every tie, its score and the Wikidata item, and to say \"Not what I mean\"." })]));
  host.appendChild(h("p", { class: "dim small stamp", text: "Pageviews of " + pool.doc.date + " (the last full day), " + pool.doc.source + ", " + pool.doc.license + ". Page and class facts: Wikidata (CC0). The profile never leaves this browser." }));
}

function bridgeLine(ctx, profile, ix, br) {
  const { h } = ctx.u;
  const have = new Set(profile.topics.map((t) => t.qid));
  const found = [];
  profile.topics.forEach((t) => ((br.bridges || {})[t.qid] || []).forEach((x) => { const q = "Q" + x[0]; if (!have.has(q) && ix.labels.has(x[0]) && !found.some((f) => f.qid === q)) found.push({ qid: q, label: ix.labels.get(x[0]), n: x[1] }); }));
  if (!found.length) return null;
  found.sort((a, b) => b.n - a.n);
  return h("span", {}, ["People who write about " + profile.topics[0].label + " also cover: "].concat(found.slice(0, 5).map((f) => h("button", { type: "button", class: "chip add", text: "+ " + f.label, title: "Add " + f.label + " to this profile",
    onclick: () => {
      const { store, profile: cur } = currentProfile(ctx);
      if (!cur) return;
      const r = P.addTopic(cur, { qid: f.qid, label: f.label, source: "typed" });
      if (r.error) return;
      const up = P.upsert(store, Object.assign({}, r.profile, { updated: new Date().toISOString() }));
      persist(ctx, up.store);
      ctx.setState({ open: "" });
    } }))));
}

function tableOf(ctx, rows, profile, ix, grey) {
  const { h, spark, pct, compact, riseTd, tierOf, statusCell } = ctx.u, st = ctx.state;
  const anchors = prepareAnchors(profile);
  const body = [];
  rows.forEach((r) => {
    const e = r.entity, id = "q" + e.qid, open = st.open === id, rising = !!(e.flags & FLAG.RISING);
    const desc = ix.desc.get(e.qid);
    const toggleRow = () => ctx.setState({ open: open ? "" : id });
    const capped = e.ratio >= 10;
    const rise = rising
      ? riseTd({ label: "Rise 7d", tier: tierOf(e.ratio), arrow: "up", text: capped ? "+900%+" : pct(e.ratio), sub: capped ? "\u00D7" + Math.round(e.ratio) : null, title: "Wikipedia, last day against the median of the 14 days before" + (capped ? ". The figure is capped at 10x (+900%)." : "") })
      : riseTd({ label: "Rise 7d", mute: true, arrow: "flat", text: "Steady", title: "Steady / Top: not rising." });
    body.push(h("tr", { class: "row fitrow" + (open ? " open" : "") + (grey ? " below" : ""), "data-id": id, onclick: (ev) => { if (!ev.target.closest("a,button,summary")) toggleRow(); } }, [
      h("td", { class: "topic" }, [
        h("button", { type: "button", class: "rowbtn", title: e.title + (desc ? ": " + desc : ""), "data-fk": "row-" + id, "aria-expanded": String(open), "aria-controls": "d-" + id, onclick: toggleRow }, [h("span", { class: "tt", text: e.title })]),
        h("span", { class: "cat why", title: r.why, text: r.why })]),
      rise, fitCell(ctx.u, r.fit),
      h("td", { class: "num vols", "data-label": "Volume" }, [h("span", { class: "rv", text: compact(e.views) }), h("span", { class: "via", text: "Wiki views/day" })]),
      h("td", { class: "sparkc", "data-label": "Trend" }, [Array.isArray(e.series) && e.series.length > 1 ? spark(e.series, "Wikipedia views/day, last " + e.series.length + " days: from " + compact(e.series[0]) + " to " + compact(e.series[e.series.length - 1])) : h("span", { class: "dim", text: "no series" })]),
      statusCell(rising ? "active" : "steady", rising ? "Rising" : "Steady / Top", null, null)]));
    if (open) body.push(h("tr", { class: "detail", id: "d-" + id }, [h("td", { colspan: "6" }, [detail(ctx, r, anchors, ix, desc)])]));
  });
  return h("div", { class: "tablewrap" }, [h("table", { class: "trend niche" }, [
    h("colgroup", {}, ["c-topic", "c-rise", "c-fit", "c-vol", "c-trend", "c-stat"].map((c) => h("col", { class: c }))),
    h("thead", {}, [h("tr", {}, [ctx.u.th("Topic and why it fits", null, "The page and the Wikidata tie that put it in your list"), ctx.u.th("Rise (7d)", null, "The last day against the median of the 14 days before; capped at 10x", "num"),
      ctx.u.th("Fit", null, "Fit for your niche, 0 to 100: how directly Wikidata ties the topic to yours. Not search volume.", "num"), ctx.u.th("Volume", null, "Wikipedia views on the last day in this country", "num"), ctx.u.th("Trend", null, "Daily views of the last 7 days"), ctx.u.th("Status", null, "Rising, or steady / top")])]),
    h("tbody", {}, body)])]);
}

function detail(ctx, r, anchors, ix, desc) {
  const { h } = ctx.u, e = r.entity;
  const per = r.perAnchor.map((p) => h("li", {}, [h("b", { text: p.anchor.label + " " + Math.round(p.fit * 100) + ": " }),
    p.rels.map((x) => relationText(x, p.anchor, ix.labels)).join("; ")]));
  return h("div", { class: "detail-body one" }, [
    h("h3", { text: "Why " + e.title + " fits" }),
    desc ? h("p", { class: "dim", text: desc }) : null,
    h("ul", { class: "rules" }, per),
    h("p", { class: "dim small", text: "Each tie is a fact on Wikidata with a fixed score; they combine as 1 minus the product of what is left, then a second topic counts 0.8. " + (e.flags & FLAG.PERSON ? "This is a person, so a sport or subject tie counts 0.45 and many occupations dilute the occupation tie. " : "") + (e.flags & FLAG.CLADE ? "This is a group above genus in the taxonomy, so a taxon tie counts half. " : "") }),
    h("p", {}, [h("a", { href: "https://www.wikidata.org/wiki/Q" + e.qid, rel: "noopener noreferrer", text: "Wikidata item Q" + e.qid }), " \u00B7 ", h("span", { class: "lic", text: "CC0 (Wikidata); pageviews CC0 (Wikimedia Analytics datasets)" })]),
    h("p", {}, [h("button", { type: "button", class: "btn2", text: "Not what I mean", title: "Never show this topic or anything of its kind for this profile", onclick: () => {
      const { store, profile } = currentProfile(ctx);
      if (!profile || profile.avoid.length >= P.MAX_AVOID) return;
      const next = Object.assign({}, profile, { avoid: profile.avoid.concat([{ qid: "Q" + e.qid, label: e.title }]), updated: new Date().toISOString() });
      persist(ctx, P.upsert(store, next).store);
      ctx.setState({ open: "" });
    } })])]);
}

/* ---------- For your niche: Upcoming ---------- */

export function upcomingView(ctx, host) {
  const { h } = ctx.u, meta = ctx.data.meta, st = ctx.state;
  host.textContent = "";
  const { profile } = currentProfile(ctx);
  if (!matchInfo(meta)) { host.appendChild(ctx.u.emptyState("The niche data is not part of this build.")); return; }
  if (!profile) { host.appendChild(noProfile(ctx)); return; }
  host.appendChild(h("p", { class: "state", role: "status", text: "Loading your niche\u2026" }));
  Promise.all([loadConcepts(meta), loadObservances(meta)]).then((r) => {
    if (!host.isConnected) return;
    const ix = r[0];
    host.textContent = "";
    const region = pickRegion(ctx, profile);
    const today = ctx.u.utcDay(new Date().toISOString());
    const ents = ctx.data.events.concat(observanceEvents(r[1], meta)).map(eventToEntity).filter(Boolean);
    const annotated = ctx.data.events.some((e) => e.niche);
    const res = match(profile, ents, (q) => ix.labels.get(q));
    const open = (e) => !st.openOnly || ctx.u.utcDay(e.publish_by) >= today;
    const future = (e) => ctx.u.utcDay(e.date) >= today;
    const pick = (list) => list.filter((x) => future(x.entity.event) && open(x.entity.event)).sort((a, b) => ctx.u.utcDay(a.entity.event.date) - ctx.u.utcDay(b.entity.event.date) || a.entity.event.title.localeCompare(b.entity.event.title));
    const fits = pick(res.fits), weaker = pick(res.weaker);
    const days = (profile.cadence || {}).days || [];
    host.appendChild(ctx.u.filterBar([ctx.showControl(), ctx.u.C.chip("open", "Publish window open", st.openOnly, (v) => ({ openOnly: v }), { row: 1, def: true })], { noun: "events" }));
    host.appendChild(strip(ctx, profile, region));
    host.appendChild(h("div", { class: "mline" }, [h("p", { class: "count", role: "status", text: fits.length + " dated event" + (fits.length === 1 ? "" : "s") + " fit " + profile.topics[0].label + ", soonest first. Publish by is the event date minus a default lead time per kind of event (not measured for any site). Holidays and Federal Register notices carry no Wikidata id, so they are never matched." })]));
    if (!annotated && !r[1].rows.length) host.appendChild(ctx.u.emptyState("These events carry no matching data.", "The niche step of the daily build did not finish for this build. Try again tomorrow."));
    else if (!fits.length) host.appendChild(ctx.u.emptyState("No upcoming event fits your niche.", "Wikidata lists dated events and yearly observances for sports, films, awards and some hobbies. Add a broader topic, or turn off Publish window open.", st.openOnly ? h("button", { type: "button", class: "btn", text: "Show closed windows too", onclick: () => ctx.setState({ openOnly: false }) }) : h("a", { class: "btn", href: "#/niche", text: "Edit niche" })));
    else host.appendChild(eventTable(ctx, fits, days, ix));
    if (weaker.length) host.appendChild(h("details", { class: "weaker" }, [h("summary", { text: "Weaker fits (35 to 49), " + weaker.length + " events" }), eventTable(ctx, weaker, days, ix)]));
    host.appendChild(h("p", { class: "dim small stamp", text: "Events: Wikidata (CC0). Yearly observances show the next date of a rule on Wikidata (P837), not a one-off announcement." }));
  }).catch((e) => { host.textContent = ""; host.appendChild(ctx.u.emptyState("The niche data did not load.", String(e && e.message || e))); });
}

function eventTable(ctx, rows, days, ix) {
  const { h, dateLabel, utcDay } = ctx.u;
  const today = utcDay(new Date().toISOString()), DAY = ctx.u.DAY;
  const sourceLabel = (id) => { const m = (ctx.data.meta.sources || []).filter((x) => x.id === id)[0]; return m ? m.label : id; };
  const body = rows.map((r) => {
    const e = r.entity.event, left = Math.round((utcDay(e.publish_by) - today) / DAY), urgent = left >= 0 && left <= 7;
    const slot = yourSlot(days, utcDay(e.publish_by), today);
    return h("tr", { class: "row up" }, [
      h("td", { class: "topic", "data-label": "Event" }, [h("span", { class: "tt", title: e.title + " (" + (e.category_label || e.category) + ", " + e.kind + ")", text: e.title }), h("span", { class: "cat why", title: r.why, text: r.why })]),
      fitCell(ctx.u, r.fit),
      h("td", { class: "date", "data-label": "Date" }, [dateLabel(e.date), e.recurrence ? h("span", { class: "when dim", text: "yearly: " + e.rule + " (rule on Wikidata)" }) : null]),
      h("td", { class: "date", "data-label": "Publish by", title: "Event date minus " + e.lag_days + " days, the default for this kind of event", text: dateLabel(e.publish_by) }),
      slot ? h("td", { class: "date slotc", "data-label": "Your slot", title: slot.late ? "The publish-by date has passed" : slot.none ? "None of your usual days falls before the publish-by date" : "The latest of your usual days before the publish-by date" }, [h("span", { class: "slot", text: dateLabel(new Date(slot.ms).toISOString()) + (slot.late ? " (late)" : slot.none ? " (no usual day fits)" : "") })]) : null,
      ctx.u.leftTd(left, urgent),
      h("td", { class: "evsrc", "data-label": "Source" }, [h("a", { href: e.source_url, rel: "noopener noreferrer", title: sourceLabel(e.source), text: sourceLabel(e.source) }), h("span", { class: "lic", title: e.license || false, text: e.license || "licence unknown" })])]);
  });
  const head = ["Event and why it fits", "Fit", "Date", "Publish by"].concat(days.length ? ["Your slot"] : []).concat(["Left", "Source and licence"]);
  return h("div", { class: "tablewrap" }, [h("table", { class: "trend upcoming niche" + (days.length ? " slots" : "") }, [h("colgroup", {}, ["c-ev", "c-fit", "c-date", "c-date"].concat(days.length ? ["c-date"] : []).concat(["c-left", "c-esrc"]).map((c) => h("col", { class: c }))), h("thead", {}, [h("tr", {}, head.map((x) => ctx.u.th(x, null, undefined, x === "Fit" || x === "Left" ? "num" : "")))]), h("tbody", {}, body)])]);
}

/* dispatcher used by trending.js */
export function view(ctx, host) {
  if (ctx.state.tab === "niche") profilePage(ctx, host);
  else if (ctx.state.tab === "upcoming") upcomingView(ctx, host);
  else trendingView(ctx, host);
}

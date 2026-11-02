/* profile.js: the "Your niche" profile (schema nicheclock.profile/1): validation, storage, export and import.
   Pure functions plus a storage wrapper that takes the storage object as an argument (the page passes localStorage), so
   the same code runs in the page, in Node tests and later in the Worker. Nothing here reads the network.

   The profile lives in one localStorage key. No cookie, no IndexedDB, no sessionStorage copy, and the URL hash never
   carries it, so a pasted link cannot hold a profile and the page-view beacon (path only) cannot see one. */

export const SCHEMA = "nicheclock.profile/1";
export const STORAGE_KEY = "nicheclock.profiles.v1";
export const MAX_PROFILES = 5, MAX_TOPICS = 8, MAX_AVOID = 10, MAX_SITES = 3, MAX_CHANNELS = 6, MAX_BYTES = 8192;
export const ROLES = ["main", "also"];
export const SOURCES = ["typed", "site", "youtube", "sample"];
export const PLATFORMS = ["youtube", "x", "tiktok", "pinterest", "blog", "other"];
export const FORMATS = ["blog", "video", "short", "newsletter"];
export const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];

/* The three sample profiles (Wikidata ids checked on the day they were chosen). A hobby or an ingredient is paired with what it
   is about, because Wikidata does not link the two (docs/personalization.md). */
export const SAMPLES = [
  { key: "chess", name: "Sample: chess", topics: [["Q718", "chess", "main"], ["Q103632", "chess opening", "also"]] },
  { key: "birdwatching", name: "Sample: birdwatching", topics: [["Q685629", "birdwatching", "main"], ["Q5113", "bird", "also"]] },
  { key: "sourdough", name: "Sample: sourdough", topics: [["Q904889", "sourdough", "main"], ["Q3360560", "sourdough bread", "also"], ["Q7802", "bread", "also"]] }
];

const QID = /^Q[1-9]\d*$/;
const clean = (s, max) => String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]/g, "").trim().slice(0, max);

export function newId() {
  const c = typeof crypto !== "undefined" && crypto.getRandomValues ? crypto.getRandomValues(new Uint32Array(2)) : [Math.random() * 4e9, Math.random() * 4e9];
  return "p_" + (Number(c[0]).toString(36) + Number(c[1]).toString(36)).slice(0, 8);
}

export function sampleProfile(key, region, now) {
  const s = SAMPLES.filter((x) => x.key === key)[0];
  if (!s) return null;
  return validate({
    schema: SCHEMA, id: newId(), name: s.name, updated: now || new Date().toISOString(),
    topics: s.topics.map((t) => ({ qid: t[0], label: t[1], role: t[2], source: "sample" })), region: region || "US", language: "en"
  }).profile;
}

/* validate(raw) -> {ok, profile, errors[], dropped[]}. Unknown keys are dropped, control characters stripped, sizes capped,
   non-https URLs refused. ok is false only when the profile cannot be used (wrong schema, no valid topic, no region). */
export function validate(raw, opts) {
  const o = opts || {}, errors = [], dropped = [];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { ok: false, profile: null, errors: ["The profile is not an object."], dropped };
  if (typeof raw.schema !== "string" || raw.schema.split("/")[0] !== SCHEMA.split("/")[0]) return { ok: false, profile: null, errors: ["This is not a nicheclock profile."], dropped };
  if (raw.schema !== SCHEMA) return { ok: false, profile: null, errors: ["This profile has a version (" + clean(raw.schema, 40) + ") that this page cannot read."], dropped };
  for (const k of Object.keys(raw)) if (["schema", "id", "name", "updated", "topics", "avoid", "region", "language", "sites", "channels", "formats", "cadence"].indexOf(k) < 0) dropped.push(k);
  const p = { schema: SCHEMA, id: /^p_[a-z0-9]{3,12}$/.test(raw.id || "") ? raw.id : newId(), name: clean(raw.name, 60) || "My niche", updated: o.keepUpdated && typeof raw.updated === "string" ? clean(raw.updated, 30) : (o.now || new Date().toISOString()) };
  p.topics = [];
  for (const t of Array.isArray(raw.topics) ? raw.topics : []) {
    if (!t || typeof t.qid !== "string" || !QID.test(t.qid)) { dropped.push("topic"); continue; }
    if (p.topics.some((x) => x.qid === t.qid)) continue;
    if (p.topics.length >= MAX_TOPICS) { dropped.push("topic (more than " + MAX_TOPICS + ")"); continue; }
    p.topics.push({ qid: t.qid, label: clean(t.label, 80) || t.qid, role: ROLES.indexOf(t.role) >= 0 ? t.role : "also", source: SOURCES.indexOf(t.source) >= 0 ? t.source : "typed" });
  }
  if (!p.topics.length) errors.push("A profile needs at least one topic.");
  else if (!p.topics.some((t) => t.role === "main")) p.topics[0].role = "main";
  p.avoid = [];
  for (const t of Array.isArray(raw.avoid) ? raw.avoid : []) {
    if (t && typeof t.qid === "string" && QID.test(t.qid) && p.avoid.length < MAX_AVOID && !p.avoid.some((x) => x.qid === t.qid)) p.avoid.push({ qid: t.qid, label: clean(t.label, 80) || t.qid });
  }
  p.region = typeof raw.region === "string" && /^[A-Za-z]{2,8}$/.test(raw.region) ? raw.region : "";
  if (!p.region) errors.push("A profile needs a region.");
  p.language = /^[a-z]{2,3}$/.test(raw.language || "") ? raw.language : "en";
  p.sites = [];
  for (const s of Array.isArray(raw.sites) ? raw.sites : []) {
    const u = clean(s && s.url, 300);
    if (!/^https:\/\/[^\s/]+\.[^\s/]+(\/\S*)?$/.test(u)) { if (u) dropped.push("site (not an https address)"); continue; }
    if (p.sites.length < MAX_SITES) p.sites.push({ url: u });
  }
  p.channels = [];
  for (const c of Array.isArray(raw.channels) ? raw.channels : []) {
    const platform = PLATFORMS.indexOf(c && c.platform) >= 0 ? c.platform : "";
    const handle = clean(c && c.handle, 120);
    if (!platform || !handle) continue;
    if (/^(https?|javascript|data):/i.test(handle) && !/^https:\/\//i.test(handle)) { dropped.push("channel (not an https address)"); continue; }
    if (p.channels.length < MAX_CHANNELS) p.channels.push({ platform, handle });
  }
  p.formats = (Array.isArray(raw.formats) ? raw.formats : []).filter((f, i, a) => FORMATS.indexOf(f) >= 0 && a.indexOf(f) === i);
  const cd = raw.cadence && typeof raw.cadence === "object" ? raw.cadence : null;
  if (cd) {
    const per = Math.round(Number(cd.per_week));
    const days = (Array.isArray(cd.days) ? cd.days : []).filter((d, i, a) => DAYS.indexOf(d) >= 0 && a.indexOf(d) === i);
    if ((per >= 1 && per <= 14) || days.length) p.cadence = Object.assign(per >= 1 && per <= 14 ? { per_week: per } : {}, days.length ? { days } : {});
  }
  if (JSON.stringify(p).length > MAX_BYTES) errors.push("The profile is larger than " + MAX_BYTES + " bytes.");
  return { ok: errors.length === 0, profile: errors.length ? null : p, errors, dropped };
}

/* ---- storage ---- */
export function emptyStore() { return { active: "", profiles: [] }; }

/* read the store; a damaged value is treated as empty (never thrown), and `damaged` says so */
export function loadStore(storage) {
  let txt = null;
  try { txt = storage.getItem(STORAGE_KEY); } catch (e) { return { store: emptyStore(), damaged: false, blocked: true }; }
  if (!txt) return { store: emptyStore(), damaged: false };
  try {
    const j = JSON.parse(txt), out = emptyStore();
    for (const raw of Array.isArray(j.profiles) ? j.profiles : []) {
      const v = validate(raw, { keepUpdated: true });
      if (v.ok && out.profiles.length < MAX_PROFILES) out.profiles.push(v.profile);
    }
    out.active = out.profiles.some((p) => p.id === j.active) ? j.active : (out.profiles[0] || {}).id || "";
    return { store: out, damaged: false };
  } catch (e) { return { store: emptyStore(), damaged: true }; }
}
export function saveStore(storage, store) {
  try { storage.setItem(STORAGE_KEY, JSON.stringify(store)); return true; } catch (e) { return false; }
}
export function clearStore(storage) { try { storage.removeItem(STORAGE_KEY); } catch (e) { /* storage blocked: nothing was kept */ } }

export function activeProfile(store) { return store.profiles.filter((p) => p.id === store.active)[0] || null; }

/* add or replace a profile (by id); returns {store, error}. A sixth profile is refused. */
export function upsert(store, profile) {
  const idx = store.profiles.findIndex((p) => p.id === profile.id);
  if (idx < 0 && store.profiles.length >= MAX_PROFILES) return { store, error: "You can keep up to " + MAX_PROFILES + " profiles. Delete one first." };
  const profiles = store.profiles.slice();
  if (idx >= 0) profiles[idx] = profile; else profiles.push(profile);
  return { store: { active: profile.id, profiles }, error: "" };
}
export function remove(store, id) {
  const profiles = store.profiles.filter((p) => p.id !== id);
  return { active: store.active === id ? (profiles[0] || {}).id || "" : store.active, profiles };
}

/* export: the profile as the file the user keeps (pretty, stable key order) */
export function exportJson(profile) { return JSON.stringify(profile, null, 2) + "\n"; }
/* import: text -> {ok, profile, errors}; a new local id is given so an import never overwrites another profile by accident */
export function importJson(text) {
  if (String(text).length > MAX_BYTES * 2) return { ok: false, errors: ["The file is too large for a profile."], dropped: [] };
  let raw;
  try { raw = JSON.parse(text); } catch (e) { return { ok: false, errors: ["The file is not JSON."], dropped: [] }; }
  const v = validate(Object.assign({}, raw, { id: newId() }));
  return v;
}

/* topic list helpers used by the form */
export function addTopic(profile, topic) {
  if (profile.topics.some((t) => t.qid === topic.qid)) return { profile, error: "" };
  if (profile.topics.length >= MAX_TOPICS) return { profile, error: "A profile holds up to " + MAX_TOPICS + " topics." };
  const t = { qid: topic.qid, label: clean(topic.label, 80) || topic.qid, role: profile.topics.length ? "also" : "main", source: topic.source || "typed" };
  return { profile: Object.assign({}, profile, { topics: profile.topics.concat([t]) }), error: "" };
}
export function removeTopic(profile, qid) {
  const topics = profile.topics.filter((t) => t.qid !== qid);
  if (topics.length && !topics.some((t) => t.role === "main")) topics[0] = Object.assign({}, topics[0], { role: "main" });
  return Object.assign({}, profile, { topics });
}

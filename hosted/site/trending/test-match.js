/* Tests for match.js and profile.js: `node hosted/site/trending/test-match.js` (no dependencies). Freezes the weights of
   WEIGHTS: a change here is a change of what a profile sees, and needs a before / after on the judge set. */
import assert from "node:assert/strict";
import {
  KIND, FLAG, WEIGHTS, decodeHandle, rowToEntity, eventToEntity, relations, prepareAnchors, fitOf, match, whyLine, yourSlot, heat, wholeWord
} from "./match.js";
import * as P from "./profile.js";

let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok  " + name); };
const H = (q, kind, sub) => q * 64 + kind * 16 + sub;
const LABELS = { 718: "chess", 10873124: "chess player", 103632: "chess opening", 131436: "puzzle", 5113: "bird", 7802: "bread", 3360560: "sourdough bread", 4970: "bread dish", 4: "writer" };
const labelOf = (q) => LABELS[q];
const chess = { topics: [{ qid: "Q718", label: "chess", role: "main" }] };
const ent = (over) => Object.assign({ qid: 1, title: "Something", views: 500, ratio: 1, handles: [], series: 0, flags: 0 }, over);
const fit = (e, profile = chess) => { const r = fitOf(e, prepareAnchors(profile), new Set((profile.avoid || []).map((a) => Number(a.qid.slice(1)))), labelOf); return Object.assign({}, r, { fit: Math.round(r.fit * 1e9) / 1e9 }); };

test("handle decoding round-trips qid, kind and sub (qids above 2^32 / 64 stay exact)", () => {
  const q = 141756646;
  const d = decodeHandle(H(q, KIND.TAXON, 12));
  assert.deepEqual(d, { qid: q, kind: KIND.TAXON, sub: 12 });
  assert.ok(H(q, 3, 12) > 2 ** 32);
});

test("same: the topic itself fits 1.00", () => {
  assert.equal(fit(ent({ qid: 718, title: "Chess" })).fit, 1);
});

test("class, subject, occupation, taxon score as the table says", () => {
  assert.ok(Math.abs(fit(ent({ handles: [H(718, KIND.CLASS, 1)] })).fit - 0.84) < 1e-9);
  assert.ok(Math.abs(fit(ent({ handles: [H(718, KIND.CLASS, 3)] })).fit - 0.70) < 1e-9);
  assert.ok(Math.abs(fit(ent({ handles: [H(718, KIND.SUBJECT, 0)] })).fit - 0.80) < 1e-9);
  assert.ok(Math.abs(fit(ent({ handles: [H(718, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (1 << 4) })).fit - 0.85) < 1e-9);
  const bird = { topics: [{ qid: "Q5113", label: "bird", role: "main" }] };
  assert.ok(Math.abs(fit(ent({ handles: [H(5113, KIND.TAXON, 1)] }), bird).fit - 0.90) < 1e-9);
  assert.ok(Math.abs(fit(ent({ handles: [H(5113, KIND.TAXON, 5)] }), bird).fit - 0.78) < 1e-9);
  assert.equal(fit(ent({ handles: [H(5113, KIND.TAXON, 12)] }), bird).fit, 0.57);
});

test("a page about a taxon above genus counts half in every tie but the topic itself (clade factor)", () => {
  const bird = { topics: [{ qid: "Q5113", label: "bird", role: "main" }] };
  const species = fit(ent({ handles: [H(5113, KIND.TAXON, 4)] }), bird).fit;
  const clade = fit(ent({ handles: [H(5113, KIND.TAXON, 4)], flags: FLAG.CLADE }), bird).fit;
  assert.ok(Math.abs(clade - species * WEIGHTS.cladeFactor) < 1e-9);
  const asClass = fit(ent({ handles: [H(5113, KIND.CLASS, 1)], flags: FLAG.CLADE }), bird).fit;
  assert.ok(Math.abs(asClass - 0.84 * WEIGHTS.cladeFactor) < 1e-9);
  assert.equal(fit(ent({ qid: 5113, flags: FLAG.CLADE }), bird).fit, 1);                 // the topic's own page is never halved
});

test("a person is diluted by the number of occupations: 0.85 / (1 + 0.15 (n - 1))", () => {
  const one = fit(ent({ handles: [H(718, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (1 << 4) })).fit;
  const four = fit(ent({ handles: [H(718, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (4 << 4) })).fit;
  assert.ok(Math.abs(four - 0.85 / 1.45) < 1e-9);
  assert.ok(four < one);
});

test("a person whose subject statement is the only tie scores 0.45, so it never passes alone", () => {
  const f = fit(ent({ handles: [H(718, KIND.SUBJECT, 0)], flags: FLAG.PERSON | (3 << 4) })).fit;
  assert.equal(f, 0.45);
  assert.ok(f < WEIGHTS.listed && f >= WEIGHTS.weaker);
});

test("a person with 6+ occupations tied by one statement is capped at 0.49; Boris Spassky (1 occupation) stays high", () => {
  const streamer = fit(ent({ handles: [H(718, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (6 << 4) })).fit;
  assert.ok(streamer <= 0.49 && streamer >= WEIGHTS.weaker, String(streamer));
  const spassky = fit(ent({ handles: [H(718, KIND.OCCUPATION, 0), H(10873124, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (1 << 4) })).fit;
  assert.ok(spassky > 0.85);
});

test("a named class needs a whole word: 'chess opening' counts 0.60, 'chessboard' does not", () => {
  assert.ok(Math.abs(fit(ent({ handles: [H(103632, KIND.CLASS, 1)] })).fit - 0.60) < 1e-9);
  LABELS[999] = "chessboard maker";
  assert.equal(fit(ent({ handles: [H(999, KIND.CLASS, 1)] })).fit, 0);
  delete LABELS[999];
  assert.ok(wholeWord("chess").test("a chess opening") && !wholeWord("chess").test("chessboard"));
});

test("a named occupation counts 0.85: 'chess player' under the profile 'chess'", () => {
  const r = relations(ent({ handles: [H(10873124, KIND.OCCUPATION, 0)], flags: FLAG.PERSON | (1 << 4) }), prepareAnchors(chess)[0], labelOf);
  assert.deepEqual(r.map((x) => [x.kind, x.score]), [["named", 0.85]]);
});

test("title alone is 0.40: weaker, never listed; subject (0.45, person) + title (0.40) agree to 0.67", () => {
  const t = fit(ent({ title: "Chess Boxing" })).fit;
  assert.equal(t, 0.40);
  const both = fit(ent({ title: "Chess Boxing", handles: [H(718, KIND.SUBJECT, 0)], flags: FLAG.PERSON | (2 << 4) })).fit;
  assert.ok(Math.abs(both - (1 - 0.55 * 0.6)) < 1e-9);
});

test("a fictional entity is capped at 0.30 unless the topic is fictional", () => {
  const bird = { topics: [{ qid: "Q5113", label: "bird", role: "main" }] };
  assert.equal(fit(ent({ handles: [H(5113, KIND.CLASS, 1)], flags: FLAG.FICTIONAL }), bird).fit, 0.30);
  const fic = { topics: [{ qid: "Q5113", label: "fictional bird", role: "main" }] };
  assert.ok(fit(ent({ handles: [H(5113, KIND.CLASS, 1)], flags: FLAG.FICTIONAL }), fic).fit > 0.8);
});

test("avoid drops a row whose class or subject, or the row itself, is an avoided qid", () => {
  const prof = { topics: chess.topics, avoid: [{ qid: "Q131436", label: "puzzle" }] };
  const e = ent({ handles: [H(718, KIND.SUBJECT, 0), H(131436, KIND.CLASS, 1)] });
  assert.equal(fit(e, prof).fit, 0);
  const res = match(prof, [e, ent({ qid: 2, handles: [H(718, KIND.SUBJECT, 0)] })], labelOf);
  assert.deepEqual(res.fits.map((r) => r.entity.qid), [2]);
});

test("role weight: an 'also' topic counts 0.8 of its relation", () => {
  const prof = { topics: [{ qid: "Q7802", label: "bread", role: "main" }, { qid: "Q718", label: "chess", role: "also" }] };
  const f = fit(ent({ handles: [H(718, KIND.SUBJECT, 0)] }), prof).fit;
  assert.ok(Math.abs(f - 0.8 * 0.8) < 1e-9);
});

test("property: adding a topic never lowers a fit", () => {
  let seed = 7;
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
  const qids = [718, 5113, 7802, 103632, 10873124];
  for (let i = 0; i < 300; i++) {
    const e = ent({ qid: 10 + i, title: rnd() < 0.3 ? "Chess story" : "Other", flags: rnd() < 0.3 ? FLAG.PERSON | (Math.floor(rnd() * 8) << 4) : 0,
      handles: qids.filter(() => rnd() < 0.4).map((q) => H(q, Math.floor(rnd() * 4), Math.floor(rnd() * 3))) });
    const one = { topics: [{ qid: "Q718", label: "chess", role: "main" }] };
    const two = { topics: one.topics.concat([{ qid: "Q5113", label: "bird", role: "also" }]) };
    assert.ok(fit(e, two).fit >= fit(e, one).fit - 1e-12);
  }
});

test("match: one row per qid, listed >= 0.50 / weaker 0.35..0.49, ranked by fit and heat", () => {
  const rows = [
    ent({ qid: 1, handles: [H(718, KIND.SUBJECT, 0)], ratio: 1, flags: 0 }),
    ent({ qid: 2, handles: [H(718, KIND.SUBJECT, 0)], ratio: 5, flags: FLAG.RISING }),
    ent({ qid: 2, handles: [H(718, KIND.CLASS, 3)], ratio: 1, flags: 0 }),
    ent({ qid: 3, title: "Chess Boxing" })
  ];
  const r = match(chess, rows, labelOf);
  assert.deepEqual(r.fits.map((x) => x.entity.qid), [2, 1]);
  assert.deepEqual(r.weaker.map((x) => x.entity.qid), [3]);
  assert.equal(r.duplicates, 1);
  assert.ok(Math.abs(heat(rows[1]) - 1) < 1e-9 && heat(rows[0]) === 0);
});

test("why line: at most 90 characters, names the relation and the topic, same words every time", () => {
  const a = prepareAnchors(chess)[0];
  const lines = [{ kind: "same" }, { kind: "class" }, { kind: "occupation" }, { kind: "named", via: 10873124 }, { kind: "subject", sub: 0 }, { kind: "subject", sub: 6 }, { kind: "taxon" }, { kind: "title" }].map((r) => whyLine(r, a, labelOf));
  lines.forEach((l) => { assert.ok(l.length <= 90 && l.includes("chess"), l); });
  assert.equal(whyLine({ kind: "named", via: 10873124 }, a, labelOf), "Chess player: you follow chess.");
  const long = { label: "a very long topic name ".repeat(6), re: /x/ };
  assert.ok(whyLine({ kind: "subject", sub: 1 }, long, labelOf).length <= 90);
});

test("rowToEntity and eventToEntity", () => {
  assert.deepEqual(rowToEntity([106807, "Magnus Carlsen", 942, 1.6, [1], [1, 2], 8]), { qid: 106807, title: "Magnus Carlsen", views: 942, ratio: 1.6, handles: [1], series: [1, 2], flags: 8 });
  assert.equal(eventToEntity({ title: "Holiday" }), null);
  const e = eventToEntity({ title: "World Chess Championship 2026", qid: "Q99", niche: { h: [H(718, KIND.SUBJECT, 0)], f: 0 } });
  assert.equal(e.qid, 99);
  assert.ok(Math.abs(fit(e).fit - (1 - 0.2 * 1)) < 1e-9 || fit(e).fit >= 0.8);
});

test("yourSlot: the latest usual day in the window; late; none; no usual days", () => {
  const d = (s) => Date.parse(s + "T00:00:00Z");
  assert.equal(yourSlot([], d("2026-11-04"), d("2026-11-02")), null);
  assert.equal(yourSlot(["tuesday", "friday"], d("2026-11-04"), d("2026-11-02")).ms, d("2026-11-03"));      // Mon 2 Nov today, publish by Wed 4: Tue 3
  assert.equal(yourSlot(["tuesday", "friday"], d("2026-11-07"), d("2026-11-02")).ms, d("2026-11-06"));      // Fri 6
  assert.equal(yourSlot(["tuesday", "friday"], d("2027-06-22"), d("2026-11-02")).ms, d("2027-06-22"));      // a far event: Tue 22 Jun 2027 is itself a usual day
  assert.equal(yourSlot(["tuesday", "friday"], d("2027-06-21"), d("2026-11-02")).ms, d("2027-06-18"));      // Mon 21 Jun: the Friday before
  assert.deepEqual(yourSlot(["friday"], d("2026-11-04"), d("2026-11-02")), { ms: d("2026-11-02"), none: true });
  assert.deepEqual(yourSlot(["tuesday"], d("2026-10-30"), d("2026-11-02")), { ms: d("2026-11-02"), late: true });
});

/* ---- profile.js ---- */
const good = () => ({ schema: P.SCHEMA, id: "p_k3f9a2", name: "Chess notes", updated: "2026-11-01T10:12:00Z", region: "US", language: "en",
  topics: [{ qid: "Q718", label: "chess", role: "main", source: "typed" }, { qid: "Q103632", label: "chess opening", role: "also", source: "site" }],
  avoid: [{ qid: "Q131436", label: "puzzle" }], sites: [{ url: "https://example.org" }], channels: [{ platform: "x", handle: "@examplechess" }],
  formats: ["blog", "video"], cadence: { per_week: 2, days: ["tuesday", "friday"] } });

test("profile: a valid profile round-trips through export and import (new local id)", () => {
  const v = P.validate(good(), { keepUpdated: true });
  assert.ok(v.ok, v.errors.join());
  const back = P.importJson(P.exportJson(v.profile));
  assert.ok(back.ok);
  assert.notEqual(back.profile.id, v.profile.id);
  assert.deepEqual(Object.assign({}, back.profile, { id: "", updated: "" }), Object.assign({}, v.profile, { id: "", updated: "" }));
});

test("profile: unknown keys dropped, control characters stripped, sizes capped, bad qids refused", () => {
  const raw = Object.assign(good(), { evil: 1, name: "A\u0000B\u202ECD" + "x".repeat(100) });
  raw.topics = raw.topics.concat([{ qid: "Q0", label: "bad" }, { qid: "chess", label: "bad" }]).concat(Array.from({ length: 12 }, (_, i) => ({ qid: "Q" + (1000 + i), label: "t" + i })));
  const v = P.validate(raw);
  assert.ok(v.ok);
  assert.ok(v.dropped.includes("evil"));
  assert.ok(!/[\u0000\u202E]/.test(v.profile.name) && v.profile.name.length <= 60);
  assert.equal(v.profile.topics.length, P.MAX_TOPICS);
  assert.ok(v.profile.topics.every((t) => /^Q[1-9]\d*$/.test(t.qid)));
});

test("profile: wrong or newer schema and non-https sites are refused; one main topic is guaranteed", () => {
  assert.equal(P.validate({ schema: "nicheclock.profile/2" }).ok, false);
  assert.equal(P.validate({ schema: "other/1" }).ok, false);
  assert.equal(P.validate("x").ok, false);
  const raw = good(); raw.sites = [{ url: "http://example.org" }, { url: "javascript:alert(1)" }]; raw.topics[0].role = "also";
  const v = P.validate(raw);
  assert.deepEqual(v.profile.sites, []);
  assert.equal(v.profile.topics.filter((t) => t.role === "main").length, 1);
  assert.equal(P.validate(Object.assign(good(), { topics: [] })).ok, false);
  assert.equal(P.validate(Object.assign(good(), { region: "" })).ok, false);
});

test("profile: import refuses non-JSON and oversized files", () => {
  assert.equal(P.importJson("not json").ok, false);
  assert.equal(P.importJson("x".repeat(20000)).ok, false);
});

test("profile: one storage key, up to 5 profiles, delete clears, a damaged value is empty", () => {
  const mem = { d: {}, getItem(k) { return k in this.d ? this.d[k] : null; }, setItem(k, v) { this.d[k] = v; }, removeItem(k) { delete this.d[k]; } };
  let { store } = P.loadStore(mem);
  for (let i = 0; i < 5; i++) { const p = P.validate(Object.assign(good(), { id: "p_a" + i + "bcd" }), {}).profile; const r = P.upsert(store, p); assert.equal(r.error, ""); store = r.store; }
  const six = P.upsert(store, P.validate(Object.assign(good(), { id: "p_zzzzz" })).profile);
  assert.ok(six.error && six.store.profiles.length === 5);
  assert.ok(P.saveStore(mem, store));
  assert.deepEqual(Object.keys(mem.d), [P.STORAGE_KEY]);
  assert.equal(P.loadStore(mem).store.profiles.length, 5);
  assert.equal(P.activeProfile(P.loadStore(mem).store).id, "p_a4bcd");
  mem.d[P.STORAGE_KEY] = "{broken";
  assert.deepEqual(P.loadStore(mem), { store: P.emptyStore(), damaged: true });
  P.clearStore(mem);
  assert.deepEqual(mem.d, {});
  assert.equal(P.remove(store, "p_a4bcd").profiles.length, 4);
});

test("profile: samples are valid, use one qid per topic, and mark their source", () => {
  for (const s of P.SAMPLES) {
    const p = P.sampleProfile(s.key, "US", "2026-11-02T00:00:00Z");
    assert.ok(p && p.topics.every((t) => t.source === "sample") && p.topics.filter((t) => t.role === "main").length === 1, s.key);
  }
});

console.log("\n" + n + " tests passed");

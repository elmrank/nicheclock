/* match.js: match a profile against niche-pool rows and events. Pure functions: no DOM, no fetch, no clock except what the
   caller passes in. One ES module for the page (and, in a later step, the Worker), so the same input gives the same list
   everywhere. The rules, weights and thresholds are in WEIGHTS and are frozen by test-match.js; they change only with a
   before / after on the judge set (docs/personalization.md).

   Input
     profile   {topics: [{qid: "Q718", label: "chess", role: "main" | "also"}], avoid: [{qid, label}], formats: []}
     entity    {qid: 106807, title, views, ratio, handles: [int], series: [int] | 0, flags: int}   (rowToEntity / eventToEntity)
     labelOf   (qidNumber) => label | undefined       (the concept index: labels of the classes, occupations and subjects)
   A handle is  qid * 64 + kind * 16 + sub  (kind 0 class, 1 occupation, 2 subject, 3 taxon; see nicheclock/catalog/pool.py). */

export const KIND = { CLASS: 0, OCCUPATION: 1, SUBJECT: 2, TAXON: 3 };
export const FLAG = { PERSON: 1, FICTIONAL: 2, CLADE: 4, RISING: 8 };       // bits 4..7: number of occupations (at most 15)
/* the property behind a subject handle's `sub` (pool.py SUBJECT_PROPS) */
export const SUBJECT_PROP = ["P641", "P921", "P361", "P179", "P8345", "P136", "P800"];

export const WEIGHTS = {
  role: { main: 1.0, also: 0.8 },
  same: 1.0,
  classScore: (d) => Math.max(0.70, 0.92 - 0.08 * d),
  occupation: 0.85,
  occupationDilution: 0.15,          // a person with n occupations: 0.85 / (1 + 0.15 (n - 1))
  namedOccupation: 0.85,             // an occupation whose name holds the topic as a whole word: "chess player"
  namedClass: 0.60,                  // a class whose name holds the topic: "chess opening" (the prototype's 0.85 put "bread dish" rows at 0.75)
  subject: 0.80,
  subjectPerson: 0.45,               // a streamer who plays chess is not a chess topic by that statement alone
  taxonScore: (d) => Math.max(0.55, 0.90 - 0.03 * (d - 1)),
  cladeFactor: 0.5,                  // a page about a taxon above genus (family, order, clade) counts half in every tie but "same": a bird site writes about species
  title: 0.40,
  listed: 0.50,                      // at or above: "fits"
  weaker: 0.35,                      // 0.35 to 0.49: "Weaker fits"; below: not shown
  manyOccupations: 6,                // a person with this many occupations tied by one statement is capped
  personCap: 0.49,
  fictionalCap: 0.30,
  heatBase: 0.35,                    // rank = fit * (heatBase + (1 - heatBase) * heat)
  heatAt: 5                          // a rise of this multiple gives full heat
};

export const ORDER = ["same", "class", "occupation", "named", "subject", "taxon", "title"];

export function decodeHandle(h) {
  return { qid: Math.floor(h / 64), kind: Math.floor(h / 16) % 4, sub: h % 16 };
}

/* pool row -> entity */
export function rowToEntity(r) {
  return { qid: r[0], title: r[1], views: r[2], ratio: r[3], handles: r[4], series: r[5], flags: r[6] };
}
/* upcoming event with a `niche` annotation -> entity (null when the event has no Wikidata id or no handles) */
export function eventToEntity(ev) {
  if (!ev || !ev.qid || !ev.niche) return null;
  return { qid: Number(String(ev.qid).slice(1)), title: ev.title, views: 0, ratio: 0, handles: ev.niche.h || [], series: 0, flags: ev.niche.f || 0, event: ev };
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/* the topic's name as a whole word (letters and digits on both sides are not allowed) */
export function wholeWord(label) {
  return new RegExp("(^|[^\\p{L}\\p{N}])" + escapeRe(String(label).toLowerCase()) + "($|[^\\p{L}\\p{N}])", "u");
}

export function prepareAnchors(profile) {
  return (profile.topics || []).map((t) => ({
    qid: Number(String(t.qid).slice(1)), label: t.label || t.qid, w: WEIGHTS.role[t.role] || WEIGHTS.role.also,
    re: wholeWord(t.label || ""), fictional: /fiction/i.test(t.label || "")
  }));
}
export function prepareAvoid(profile) { return new Set((profile.avoid || []).map((a) => Number(String(a.qid).slice(1)))); }

const nocc = (e) => (e.flags >> 4) & 15;
const isPerson = (e) => !!(e.flags & FLAG.PERSON);

/* Every relation that holds between one entity and one anchor, best score per kind:
   [{kind, score, via, sub}] ordered by ORDER. `via` is the qid number of the class / occupation / subject that carried it. */
export function relations(e, a, labelOf) {
  const best = {};
  const clade = e.flags & FLAG.CLADE ? WEIGHTS.cladeFactor : 1;
  const put = (kind, score, via, sub) => { const s = kind === "same" ? score : score * clade; if (!best[kind] || s > best[kind].score) best[kind] = { kind, score: s, via, sub }; };
  const person = isPerson(e), n = nocc(e);
  const occScore = (base) => (person && n > 1 ? base / (1 + WEIGHTS.occupationDilution * (n - 1)) : base);
  if (e.qid === a.qid) put("same", WEIGHTS.same, e.qid, 0);
  for (const h of e.handles) {
    const d = decodeHandle(h);
    if (d.qid === a.qid) {
      if (d.kind === KIND.CLASS) put("class", WEIGHTS.classScore(d.sub), d.qid, d.sub);
      else if (d.kind === KIND.OCCUPATION && d.sub <= 2) put("occupation", occScore(WEIGHTS.occupation), d.qid, d.sub);
      else if (d.kind === KIND.SUBJECT) put("subject", person ? WEIGHTS.subjectPerson : WEIGHTS.subject, d.qid, d.sub);
      else if (d.kind === KIND.TAXON) put("taxon", WEIGHTS.taxonScore(d.sub), d.qid, d.sub);
    } else if ((d.kind === KIND.CLASS || d.kind === KIND.OCCUPATION) && d.sub <= 2) {
      const l = labelOf(d.qid);
      if (l && a.re.test(l.toLowerCase())) {
        put("named", d.kind === KIND.OCCUPATION ? occScore(WEIGHTS.namedOccupation) : WEIGHTS.namedClass, d.qid, d.kind);
      }
    }
  }
  if (a.re.test(String(e.title).toLowerCase())) put("title", WEIGHTS.title, e.qid, 0);
  return ORDER.filter((k) => best[k]).map((k) => best[k]);
}

const noisyOr = (xs) => 1 - xs.reduce((p, x) => p * (1 - x), 1);

/* fit of one entity against the whole profile: {fit, perAnchor: [{anchor, fit, rels}], rels (best anchor first)} */
export function fitOf(e, anchors, avoid, labelOf) {
  for (const h of e.handles) { if (avoid.has(decodeHandle(h).qid)) return { fit: 0, avoided: true, rels: [], perAnchor: [] }; }
  if (avoid.has(e.qid)) return { fit: 0, avoided: true, rels: [], perAnchor: [] };
  const per = [];
  for (const a of anchors) {
    const rels = relations(e, a, labelOf);
    if (!rels.length) continue;
    let fit = a.w * noisyOr(rels.map((r) => r.score));
    const strong = rels.filter((r) => r.kind !== "title").length;
    if (isPerson(e) && nocc(e) >= WEIGHTS.manyOccupations && strong <= 1) fit = Math.min(fit, WEIGHTS.personCap);
    if ((e.flags & FLAG.FICTIONAL) && !a.fictional) fit = Math.min(fit, WEIGHTS.fictionalCap);
    per.push({ anchor: a, fit, rels });
  }
  per.sort((x, y) => y.fit - x.fit);
  return { fit: noisyOr(per.map((p) => p.fit)), rels: per.length ? per[0].rels : [], perAnchor: per };
}

/* heat: how far above its own median the page is (rising rows only), 0 to 1 */
export function heat(e) {
  if (!(e.flags & FLAG.RISING) || !(e.ratio > 1)) return 0;
  return Math.max(0, Math.min(1, Math.log10(e.ratio) / Math.log10(WEIGHTS.heatAt)));
}
export function rankOf(fit, e) { return fit * (WEIGHTS.heatBase + (1 - WEIGHTS.heatBase) * heat(e)); }

/* ---- the "why it fits" line (at most 90 characters, from templates, no model) ---- */
const SUBJECT_TEXT = [
  (a) => "Sport: " + a + ", which you follow.", (a) => "About " + a + ", which you follow.", (a) => "Part of " + a + ", which you follow.",
  (a) => "In the series " + a + ", which you follow.", (a) => "Part of the " + a + " franchise.", (a) => "Genre: " + a + ", which you follow.",
  (a) => "Known for " + a + ", which you follow."
];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const clip = (s, n) => (s.length <= n ? s : s.slice(0, n - 1).replace(/\s+\S*$/, "") + "\u2026");

export function whyLine(rel, anchor, labelOf) {
  const a = anchor.label, via = labelOf(rel.via) || "";
  let s;
  switch (rel.kind) {
    case "same": s = "This is the topic you follow: " + a + "."; break;
    case "class": s = "A kind of " + a + ", which you follow."; break;
    case "occupation": s = "Has the occupation " + a + ", which you follow."; break;
    case "named": s = cap(via) + ": you follow " + a + "."; break;
    case "subject": s = SUBJECT_TEXT[rel.sub](a); break;
    case "taxon": s = "Belongs to the group " + a + " (taxon), which you follow."; break;
    default: s = "Title contains " + a + " (weak).";
  }
  return clip(s, 90);
}

/* Match a profile against entities. Returns {fits, weaker, near}: rows with fit >= 0.50 (best rank first), 0.35 to 0.49, and the
   three best below 0.35 (shown greyed only when nothing fits). One row per qid (the best). */
export function match(profile, entities, labelOf, opts) {
  const o = opts || {};
  const anchors = prepareAnchors(profile), avoid = prepareAvoid(profile);
  const seen = new Map();
  let duplicates = 0;
  for (const e of entities) {
    const r = fitOf(e, anchors, avoid, labelOf);
    if (!(r.fit > 0) || r.avoided) continue;
    const row = { entity: e, fit: r.fit, rank: rankOf(r.fit, e), relations: r.rels, perAnchor: r.perAnchor,
      why: r.perAnchor.length ? whyLine(r.rels[r.rels.length ? strongest(r.rels) : 0], r.perAnchor[0].anchor, labelOf) : "" };
    const prev = seen.get(e.qid);
    if (prev) duplicates++;
    if (!prev || row.rank > prev.rank) seen.set(e.qid, row);
  }
  const all = Array.from(seen.values());
  const byRank = (x, y) => y.rank - x.rank || y.fit - x.fit || (y.entity.views || 0) - (x.entity.views || 0) || x.entity.qid - y.entity.qid;
  const fits = all.filter((r) => r.fit >= WEIGHTS.listed).sort(byRank);
  const weaker = all.filter((r) => r.fit >= WEIGHTS.weaker && r.fit < WEIGHTS.listed).sort(byRank);
  const near = all.filter((r) => r.fit < WEIGHTS.weaker).sort(byRank).slice(0, 3);
  return { fits, weaker, near, matched: fits.length, duplicates };
}
function strongest(rels) { let k = 0; rels.forEach((r, i) => { if (r.score > rels[k].score) k = i; }); return k; }

/* sort keys for the niche view */
export const SORTS = {
  fit: (x, y) => y.rank - x.rank || y.fit - x.fit || (y.entity.views || 0) - (x.entity.views || 0) || x.entity.qid - y.entity.qid,
  rise: (x, y) => (y.entity.flags & FLAG.RISING ? y.entity.ratio : 0) - (x.entity.flags & FLAG.RISING ? x.entity.ratio : 0) || SORTS.fit(x, y),
  volume: (x, y) => (y.entity.views || 0) - (x.entity.views || 0) || x.entity.qid - y.entity.qid
};

/* ---- Upcoming: "your slot" ---- */
const DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];
const DAY_MS = 86400000;
/* The latest of the user's usual days that is on or after today and not later than publish_by (UTC days as ms), found by stepping
   back from publish_by. null when the profile has no usual days; {ms: today, late: true} when the publish-by date has passed;
   {ms: today, none: true} when the window is open but no usual day falls inside it. */
export function yourSlot(days, publishByMs, todayMs) {
  if (!days || !days.length) return null;
  if (publishByMs < todayMs) return { ms: todayMs, late: true };
  for (let t = publishByMs, i = 0; t >= todayMs && i < 7; t -= DAY_MS, i++) {
    if (days.indexOf(DAYS[new Date(t).getUTCDay()]) >= 0) return { ms: t };
  }
  return { ms: todayMs, none: true };
}

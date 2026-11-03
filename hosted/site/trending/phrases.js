/* phrases.js: topic phrases from page addresses and titles (or video titles and tags). Pure functions, no DOM, no fetch.
   One ES module for the Worker (POST /v1/suggest/site, /v1/suggest/youtube) and the browser (niche.js), so the words found are the
   same wherever the text is read. No model: stop words, 1 to 3-word n-grams and a document count, the same idea as
   nicheclock/onboard/extract.py (the self-hosted reader), without its background corpus.

   A "document" is one page or one video: {text, tags?}. `text` is a page title, or the words of the address when the title was not
   read. `tags` (the creator's own words on a video) count double. A phrase's count is the number of documents that hold it. */

export const STOP = new Set(("a an the and or nor but of to in on for with by at from into onto over under about as is are was were be been being " +
  "am do does did done doing can could should would will shall may might must have has had having not no yes so than then too very just also only more " +
  "most much many some any all each every other another such own same how what why when where which who whom whose this that these those there here it its " +
  "i me my mine we us our ours you your yours he him his she her hers they them their theirs one new latest official full get gets got make makes made " +
  "use uses using need needs know via per up out off down again once blog post posts category categories tag tags author authors page pages amp html htm " +
  "php index home about contact sitemap feed rss archive archives guide guides tips tip review reviews best top vs versus ultimate complete beginner beginners " +
  "easy simple quick free list lists ideas examples example tutorial checklist de la le les el en las los di il der die das und et du des da").split(" "));

export const MAX_PHRASES = 20;
export const MIN_DOCS = 2;
const SEPARATORS = /\s[|\u2013\u2014\u00B7\u00BB:]\s|\s-\s|\s::\s/;
const TOKEN = /[\p{L}\p{N}]+/gu;

const fold = (s) => String(s).toLowerCase().replace(/['\u2019`]/g, "");
export function tokens(text) { return fold(text).match(TOKEN) || []; }

/* Titles often end (or start) with the site name after a separator. A segment that most titles share is the brand and is dropped. */
export function cleanTitles(titles) {
  const split = titles.map((t) => String(t).split(SEPARATORS).map((s) => s.trim()).filter(Boolean));
  const count = new Map();
  for (const parts of split) {
    if (parts.length < 2) continue;
    for (const p of new Set([fold(parts[0]), fold(parts[parts.length - 1])])) count.set(p, (count.get(p) || 0) + 1);
  }
  const need = Math.max(2, Math.ceil(0.3 * titles.length));
  const brand = new Set([...count].filter(([, n]) => n >= need).map(([p]) => p));
  return split.map((parts) => {
    const keep = parts.filter((p) => !brand.has(fold(p)));
    return keep.length ? keep.join(" ") : "";
  });
}

/* the words of a path: its last segment, extension removed. An id or a date alone (/posts/12345, /2026/11) has no words. No URL parsing,
   so the Worker can run it on thousands of addresses cheaply. */
export function slugWordsOfPath(path) {
  const end = path.endsWith("/") ? path.length - 1 : path.length;
  let s = path.slice(path.lastIndexOf("/", end - 1) + 1, end);
  try { s = decodeURIComponent(s); } catch (e) { /* keep the raw text */ }
  s = s.replace(/\.(html?|php\d?|aspx?|jsp|shtml)$/i, "");
  if (/^[\d\-_]*$/.test(s)) return "";
  return s.replace(/[-_+]+/g, " ").trim();
}

/* the words of a page address (see slugWordsOfPath) */
export function slugWords(url) {
  let path;
  try { path = new URL(url).pathname; } catch (e) { return ""; }
  return slugWordsOfPath(path);
}

const usable = (t) => t.length >= 2 && !STOP.has(t) && !/^\d+$/.test(t);

/* the 1 to 3-word phrases of a text: runs of usable words, broken at stop words and at digits-only tokens */
export function ngrams(text, nmax) {
  const out = new Set();
  let run = [];
  const flush = () => {
    for (let i = 0; i < run.length; i++) for (let n = 1; n <= nmax && i + n <= run.length; n++) out.add(run.slice(i, i + n).join(" "));
    run = [];
  };
  for (const t of tokens(text)) { if (usable(t)) run.push(t); else flush(); }
  flush();
  return out;
}

/* extractPhrases(docs) -> {phrases: [{text, with, of}], of}. `with` is the number of documents holding the phrase (a tag counts for two),
   `of` the number of documents read. A phrase must be in at least MIN_DOCS; a shorter phrase with the same count as a longer one
   that contains it is dropped; the longer phrase ranks first. */
export function extractPhrases(docs, opts) {
  const o = Object.assign({ nmax: 3, max: MAX_PHRASES, min: MIN_DOCS }, opts || {});
  const df = new Map();
  const bump = (p, by) => df.set(p, (df.get(p) || 0) + by);
  for (const d of docs) {
    const seen = ngrams(d.text || "", o.nmax);
    for (const p of seen) bump(p, 1);
    for (const tag of new Set((d.tags || []).map((t) => tokens(t).filter((w) => w.length >= 1).join(" ")).filter(Boolean))) {
      const w = tag.split(" ");
      if (w.length > 4 || !usable(w[0]) || !usable(w[w.length - 1])) continue;
      bump(tag, seen.has(tag) ? 1 : 2);     // the creator's own word: counts double (once more when the title holds it too)
    }
  }
  const all = [...df].filter(([, n]) => n >= o.min).map(([text, n]) => ({ text, with: n, words: text.split(" ").length }));
  const keep = all.filter((p) => !all.some((q) => q.words > p.words && q.with === p.with && (" " + q.text + " ").indexOf(" " + p.text + " ") >= 0));
  keep.sort((a, b) => b.with * (1 + 0.3 * (b.words - 1)) - a.with * (1 + 0.3 * (a.words - 1)) || b.words - a.words || (a.text < b.text ? -1 : 1));
  return { phrases: keep.slice(0, o.max).map((p) => ({ text: p.text, with: p.with })), of: docs.length };
}

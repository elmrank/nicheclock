/* Tests for the Trending now logic and the fixture: `node hosted/site/trending/test.js` (no dependencies). */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const mod = { exports: {} };
new Function("module", "document", fs.readFileSync(path.join(here, "trending.js"), "utf8"))(mod, undefined);   // same realm, no DOM
const T = mod.exports;
const read = (f) => JSON.parse(fs.readFileSync(path.join(here, "fixture", f), "utf8"));
const meta = read("meta.json"), regionFiles = meta.regions.filter((r) => r.id !== "global").map((r) => read("trending-" + r.id + ".json")), topics = read("trending.json").topics, events = read("upcoming.json").events;
const NOW = Date.parse(meta.generated_at);
const ids = (t, now = NOW) => T.forChannel(t, now).filter((r) => r.hit).map((r) => r.id);

let n = 0;
const test = (name, fn) => { fn(); n++; console.log("ok  " + name); };

const mk = (over) => Object.assign({
  label: "x", status: "active", seasonal: "unknown", first_seen: new Date(NOW - 30 * 3600000).toISOString(),
  channels: { wikipedia: { value: 300, baseline: 100, unit: "pageviews/day", series: [100, 100, 100, 100, 100, 100, 300] } }
}, over);

test("channel rules: social needs a young active topic with News or HN at 2x", () => {
  const t = mk({ channels: { wikipedia: { value: 200, baseline: 100 }, news: { value: 250, baseline: 100 } } });
  assert.deepEqual(ids(t), ["social", "youtube", "newsletter"]);
  assert.ok(!ids(mk({ first_seen: new Date(NOW - 60 * 3600000).toISOString(), channels: t.channels })).includes("social"));
  assert.ok(!ids(mk({ channels: { wikipedia: { value: 300, baseline: 100 }, news: { value: 140, baseline: 100 } } })).includes("social"));
});
test("channel rules: blog needs Wikipedia rising and (seasonal or 72h old)", () => {
  assert.deepEqual(ids(mk()), []);
  assert.deepEqual(ids(mk({ seasonal: "seasonal" })), ["blog"]);
  assert.deepEqual(ids(mk({ first_seen: new Date(NOW - 80 * 3600000).toISOString() })), ["blog"]);
});
test("channel rules: newsletter needs two rising channels and 24h", () => {
  const t = mk({ first_seen: new Date(NOW - 50 * 3600000).toISOString(), channels: { hn: { value: 300, baseline: 100 }, github: { value: 300, baseline: 100 } } });
  assert.deepEqual(ids(t), ["newsletter"]);
  assert.ok(!ids(mk({ first_seen: new Date(NOW - 5 * 3600000).toISOString(), channels: t.channels })).includes("newsletter"));
});
test("channel rules: ended topics get no label", () => {
  assert.deepEqual(ids(mk({ status: "ended", seasonal: "seasonal" })), []);
});
test("channel rules are deterministic", () => {
  topics.forEach((t) => assert.deepEqual(ids(t), ids(t)));
});
test("channel ratio falls back from ratio to baseline to series", () => {
  assert.equal(T.channelRatio({ ratio: 2.5, value: 1, baseline: 1 }), 2.5);
  assert.equal(T.channelRatio({ value: 300, baseline: 100 }), 3);
  assert.equal(T.channelRatio({ series: [10, 10, 10, 10, 10, 10, 10, 30] }), 3);
  assert.equal(T.channelRatio(undefined), null);
  assert.equal(T.channelRatio({ value: 611, unit: "HN points" }), null);
});

const f = (over) => Object.assign({ window: "7d", region: "all", cat: "all", type: "all", active: false, multi: false, q: "" }, over);
const count = (o) => T.applyFilters(topics, f(o), NOW).length;
test("filters: windows nest and each filter narrows the fixture", () => {
  assert.ok(count({ window: "24h" }) < count({ window: "48h" }) && count({ window: "48h" }) < count({ window: "7d" }));
  assert.ok(count({ region: "GB" }) < count({}));
  assert.ok(count({ region: "GB" }) > 0 && count({ region: "all" }) === count({}));
  assert.ok(count({ cat: "food" }) > 0 && count({ cat: "food" }) < count({}));
  assert.ok(count({ active: true }) < count({}));
  assert.ok(count({ multi: true }) < count({}) && count({ multi: true }) > 0);
  assert.ok(count({ type: "seasonal" }) < count({}) && count({ type: "new" }) > 0);
  assert.ok(count({ src: "hn" }) > 0 && count({ src: "hn" }) < count({}));
  assert.equal(count({ q: "espresso" }), 1);
  assert.equal(count({ q: "zzzz" }), 0);
});
test("filters: category counts add up to the unfiltered-by-category total", () => {
  const c = T.categoryCounts(topics, f({ cat: "food" }), NOW);
  assert.equal(Object.values(c).reduce((a, b) => a + b, 0), count({}));
});
test("sort: rise and recency, both directions", () => {
  const rows = T.applyFilters(topics, f(), NOW);
  const key = (t) => { const i = T.riseInfo(t, "7d"); return i.kind === "new" ? 1 : i.ratio === null ? 0 : i.ratio; };
  const r = T.sortTopics(rows, "rise", "desc", "7d").map(key);
  assert.deepEqual(r, r.slice().sort((a, b) => b - a));
  const a = T.sortTopics(rows, "rise", "asc", "7d").map(key);
  assert.deepEqual(a, a.slice().sort((x, y) => x - y));
  const s = T.sortTopics(rows, "recency", "desc", "7d").map((t) => Date.parse(t.first_seen));
  assert.deepEqual(s, s.slice().sort((x, y) => y - x));
  const c = T.sortTopics(rows, "sources", "desc", "7d").map((t) => T.risingCount(t));
  assert.deepEqual(c, c.slice().sort((x, y) => y - x));
});
test("URL state round-trips and defaults stay out of the URL", () => {
  assert.equal(T.buildHash(T.DEFAULTS), "#/trending");
  const s = Object.assign({}, T.DEFAULTS, { window: "48h", cat: "food", active: true, multi: true, type: "new", q: "moka pot", sort: "recency", dir: "asc", region: "GB", open: "moka-pot" });
  assert.deepEqual(T.parseHash(T.buildHash(s)), s);
  assert.equal(T.parseHash("#/upcoming").tab, "upcoming");
  assert.equal(T.parseHash("#/trending?window=1y").window, "7d");
});
test("rising channels come from rising_channels when the catalog gives them (Hacker News has no ratio)", () => {
  const t = mk({ rising_channels: ["hn", "news"], channels: { wikipedia: { value: 900, baseline: 100 }, news: { value: 250, baseline: 100 }, hn: { value: 80, unit: "HN points" } } });
  assert.equal(T.risingCount(t), 2);
  assert.ok(ids(t).includes("social") && !ids(t).includes("youtube") && !ids(t).includes("blog"));
});
test("units: long catalog units become short chip units and keep est.", () => {
  assert.equal(T.unitShort("pageviews/day"), "views/day");
  assert.equal(T.unitShort("articles/day (estimated from sampled 15-min GDELT files)"), "articles/day est.");
  assert.equal(T.unitShort("HN points (sum over front-page and 48 h stories)"), "points");
  assert.equal(T.unitShort("documents/day"), "documents/day");
});
test("since: a date is shown as a date, never as hours", () => {
  assert.equal(T.since({ first_seen: "2026-10-30" }, NOW), "listed Oct 30");
  assert.equal(T.since({ first_seen: new Date(NOW - 3 * 3600000).toISOString() }, NOW), "3 hours ago");
});
test("sort: rank follows rank_score, volume puts missing volume last in both directions", () => {
  const row = (label, rank_score, volume, ratio) => ({ label, rank_score, volume, first_seen: "2026-10-30", rising_channels: ["wikipedia"], windows: { "7d": { ratio } },
    channels: { wikipedia: { value: 100 * ratio, baseline: 100, windows: { "7d": { ratio } } } } });
  const rows = [row("a", 5, null, 3), row("b", 9, 100, 2), row("c", 7, 900, 9)];
  assert.deepEqual(T.sortTopics(rows, "rank", "desc", "7d").map((t) => t.label), ["b", "c", "a"]);
  assert.deepEqual(T.sortTopics(rows, "volume", "desc", "7d").map((t) => t.label), ["c", "b", "a"]);
  assert.deepEqual(T.sortTopics(rows, "volume", "asc", "7d").map((t) => t.label), ["b", "c", "a"]);
  assert.deepEqual(T.sortTopics(rows, "rise", "desc", "7d").map((t) => t.label), ["c", "a", "b"]);
});
test("YouTube is a chart place: no ratio, counts for the Source filter, feeds the YouTube label", () => {
  const yt = { chart: true, rank: 3, videos: 1, value: 900000, unit: "video views" };
  assert.equal(T.channelRatio(yt), null);
  const t = mk({ rising_channels: [], windows: { "7d": { ratio: 2, active: true } }, channels: { wikipedia: { value: 120, baseline: 100 }, youtube: yt } });
  assert.equal(T.applyFilters([t], { window: "7d", src: "youtube" }, NOW).length, 1);
  assert.equal(T.applyFilters([t], { window: "7d", src: "news" }, NOW).length, 0);
  assert.ok(ids(t).includes("youtube"));
});
test("steady / top rows: Rising only drops them, rank sort lists them after the rising rows", () => {
  const steady = topics.filter(T.isSteady);
  assert.ok(steady.length >= 5);
  assert.equal(count({ rising: true }), count({}) - T.applyFilters(topics.filter((t) => T.notRising(t, "7d")), f(), NOW).length);
  const order = T.sortTopics(T.applyFilters(topics, f(), NOW), "rank", "desc", "7d").map((t) => T.notRising(t, "7d"));
  assert.deepEqual(order, order.slice().sort((a, b) => a - b));
  assert.equal(count({ active: true }), T.applyFilters(topics.filter((t) => !T.isSteady(t) && t.status === "active"), f(), NOW).length);
  assert.deepEqual(ids(steady[0]), []);   // no channel label for a topic that is not rising
});
test("rise: one definition per window, real factor above 10x, falls shown as falls, no ratio without a baseline", () => {
  const wiki = { value: 450000, unit: "pageviews/day", baseline: 4026, ratio: 111.8, series: [3963, 3312, 3369, 3195, 3301, 3347, 4141, 4089, 4563, 4277, 3775, 2356552, 1882386, 450119],
    windows: { "24h": { ratio: 111.8 }, "48h": { ratio: 301.4 }, "7d": { ratio: 200.2 } } };
  const news = { value: 224, unit: "articles/day", baseline: 0, ratio: 224, windows: { "24h": { ratio: 224 }, "48h": { ratio: 1784 } } };
  const t = mk({ rising_channels: ["wikipedia", "news"], channels: { wikipedia: wiki, news }, series_end: "2026-10-31" });
  assert.deepEqual([T.riseInfo(t, "24h").kind, T.riseInfo(t, "24h").channel, T.riseText(T.riseInfo(t, "24h"))], ["ratio", "wikipedia", "+1,000%+ (\u00D7112)"]);   // news at baseline 0 never gives the row figure
  assert.equal(T.riseText(T.riseInfo(t, "7d")), "+1,000%+ (\u00D7200)");
  assert.equal(T.chRiseText(news, "24h"), "new (no baseline)");
  assert.equal(T.chRiseText(wiki, "48h"), "+1,000%+ (\u00D7301)");
  const only = mk({ rising_channels: ["news"], channels: { wikipedia: { value: 90, baseline: 100, ratio: 0.9, windows: { "24h": { ratio: 0.9 } } }, news } });
  assert.equal(T.riseInfo(only, "24h").kind, "new");
  const down = mk({ rising_channels: ["wikipedia"], windows: { "24h": { ratio: 2 }, "7d": { ratio: 0.19 } }, channels: { wikipedia: { value: 19, baseline: 100, windows: { "24h": { ratio: 2 }, "7d": { ratio: 0.19 } } } } });
  assert.equal(T.riseText(T.riseInfo(down, "7d")), "\u221281%");
  assert.ok(T.notRising(down, "7d") && !T.notRising(down, "24h"));
  assert.equal(T.applyFilters([down], { window: "24h", rising: true }, NOW).length, 1);
  assert.equal(T.applyFilters([down], { window: "7d", rising: true }, NOW).length, 0);
  assert.ok(!/\+-/.test(["24h", "48h", "7d"].map((w) => T.riseText(T.riseInfo(t, w))).join(" ")));
});
test("fading: last day under half of the 14-day peak says so with the peak day", () => {
  const t = mk({ series_end: "2026-10-31", channels: { wikipedia: { value: 450119, baseline: 4026, series: [3963, 3312, 3369, 3195, 3301, 3347, 4141, 4089, 4563, 4277, 3775, 2356552, 1882386, 450119] } } });
  assert.ok(T.isFading(t));
  assert.equal(T.peakText(t), "peaked Oct 29, now \u221281% from the peak");
  assert.ok(!T.isFading(mk({ series_end: "2026-10-31", channels: { wikipedia: { value: 300, baseline: 100, series: [100, 100, 100, 100, 100, 100, 300] } } })));
});
test("fading from the catalog: trend fading is listed below the rising rows with the peak from the row; a ratio note means no baseline", () => {
  const f1 = mk({ trend: "fading", series_end: "2026-10-31", peak: { value: 2356552, days_ago: 2, from_peak: 0.19 }, windows: { "7d": { ratio: 200 } }, channels: { wikipedia: { value: 450119, baseline: 4026, windows: { "7d": { ratio: 200 } } } } });
  assert.ok(T.isFading(f1) && T.notRising(f1, "7d"));
  assert.equal(T.peakText(f1), "peaked Oct 29, now \u221281% from the peak");
  const kept = mk({ trend: "rising", series_end: "2026-10-31", peak: { value: 100, days_ago: 3, from_peak: 0.2 } });
  assert.ok(!T.isFading(kept));   // the catalog saw a live channel (Hacker News) and kept it rising
  assert.equal(T.chRiseText({ value: 20, baseline: 3, ratio: null, windows: {}, ratio_note: "no baseline" }, "24h"), "new (no baseline)");
});
test("no figure anywhere on the fixture shows a plus before a minus", () => {
  ["24h", "48h", "7d"].forEach((w) => topics.forEach((t) => { assert.ok(!/\+-/.test(T.riseText(T.riseInfo(t, w))), t.label); }));
});
test("coverage: categories below the minimum are flagged in meta.coverage", () => {
  const cov = meta.coverage.global;
  assert.ok(Object.values(cov).some((c) => c.limited));
  Object.entries(cov).forEach(([k, c]) => { assert.equal(c.total, topics.filter((t) => t.category === k).length); assert.equal(c.limited, c.total < T.MIN_ROWS); assert.equal(c.rising + c.steady, c.total); });
});
test("coverage: the catalog's meta.coverage equals what the page lists in 7d, for every region of the real catalog-data", () => {
  const dir = path.join(here, "..", "catalog-data");
  if (!fs.existsSync(path.join(dir, "meta.json"))) return;
  const cm = JSON.parse(fs.readFileSync(path.join(dir, "meta.json"), "utf8"));
  cm.regions.forEach((r) => {
    const rows = JSON.parse(fs.readFileSync(path.join(dir, r.file), "utf8")).topics;
    cm.categories.forEach((c) => {
      const listed = T.applyFilters(rows, { window: "7d", region: "all", cat: c.id }, Date.parse(cm.generated_at));
      const cv = cm.coverage[r.id][c.id];
      assert.equal(cv.total, listed.length, r.id + "/" + c.id);
      assert.equal(cv.limited, listed.length < T.MIN_ROWS, r.id + "/" + c.id);
    });
    assert.equal(r.topics, rows.length, r.id + " topics");
  });
});
test("formatting", () => {
  assert.equal(T.compact(21000), "21K"); assert.equal(T.compact(1900), "1.9K"); assert.equal(T.compact(310), "310"); assert.equal(T.compact(1200000), "1.2M");
  assert.equal(T.pct(7.59), "+659%"); assert.equal(T.pct(12), "+1,000%+"); assert.equal(T.pct(11), "+1,000%+"); assert.equal(T.pct(10.9), "+990%"); assert.equal(T.pct(5089), "+1,000%+"); assert.equal(T.factor(111.8), "\u00D7112"); assert.equal(T.factor(5089), "\u00D75,089"); assert.equal(T.factor(11.24), "\u00D711.2"); assert.equal(T.pct(0.81), "\u221219%"); assert.equal(T.pct(0.999), "0%"); assert.equal(T.pct(9.9), "+890%"); assert.equal(T.pct(1.5), "+50%");
  assert.equal(T.ago(3 * 3600000), "3 hours ago"); assert.equal(T.ago(49 * 3600000), "2 days ago");
});
test("sparkline text carries first, last, peak and unit", () => {
  assert.match(T.sparkLabel("Wikipedia", "views/day", [100, 400, 300]), /^Wikipedia views\/day, 3 days: from 100 to 300, peak 400$/);
});

test("fixture: every channel has a unit, a series, a source, a link and a licence", () => {
  assert.ok(topics.length >= 40);
  topics.forEach((t) => {
    assert.ok(Object.keys(t.channels).length >= 1, t.label);
    Object.entries(t.channels).forEach(([k, c]) => {
      assert.ok(c.unit && c.source && c.source_url && c.license, t.label + "/" + k);
      if (k === "youtube") assert.ok(c.chart && c.rank > 0 && !("baseline" in c) && !("series" in c), t.label);   // a chart place: no series, no rise
      else if (k !== "hn") assert.ok(c.series.length >= 7 && c.baseline > 0, t.label + "/" + k);   // Hacker News is a snapshot: no series, no baseline
    });
    assert.ok(t.evidence.length >= 1, t.label);
    t.evidence.forEach((e) => assert.ok(e.source && e.source_url && e.license, t.label));
    if (!T.isSteady(t)) Object.values(t.windows).forEach((w) => assert.ok(w.ratio >= T.RISING)); else assert.ok(t.rising_channels.length === 0 && Object.values(t.windows).every((w) => w.active === false), t.label);
  });
});
test("fixture: units are never mixed in one channel and no field is called search volume", () => {
  const units = new Set(); topics.forEach((t) => Object.values(t.channels).forEach((c) => units.add(c.unit)));
  assert.deepEqual([...units].map(T.unitShort).sort(), ["articles/day est.", "documents/day", "views/day", "points", "video views"].sort());
  assert.ok(!/search[_ ]volume/i.test(fs.readFileSync(path.join(here, "fixture", "trending.json"), "utf8")));
});
test("fixture: events carry date, publish_by, source and licence; categories cover Google's 19", () => {
  events.forEach((e) => { assert.ok(e.date && e.publish_by && e.publish_by <= e.date && e.source && e.source_url && e.license, e.title); });
  assert.equal(meta.categories.length, 19);
  assert.ok(topics.every((t) => meta.categories.some((c) => c.id === t.category)));
});

test("fixture regions: a limited region lists only sources it has, never a zero", () => {
  assert.equal(meta.regions[0].id, "global");
  regionFiles.forEach((f, i) => {
    const m = meta.regions.filter((r) => r.id !== "global")[i];
    f.topics.forEach((t) => Object.entries(t.channels).forEach(([k, c]) => { assert.ok(m.sources.includes(k) && c.value > 0, m.id + "/" + t.label + "/" + k); }));
    if (m.limited) assert.ok(m.limited_note && m.sources.length < meta.regions[0].sources.length);
  });
});

test("youtube sidecar: merged by Wikidata id as a chart channel plus evidence and the video family, never twice", () => {
  const rows = [{ qid: "Q1", channels: { wikipedia: { value: 1 } }, evidence: [], source_families: ["wiki"] }, { qid: "Q2", channels: {} }, { qid: null, channels: {} }];
  const yt = { topics: { Q1: { channel: { chart: true, rank: 2, value: 5, unit: "video views" }, evidence: [{ title: "v" }] } } };
  assert.equal(T.mergeYouTube(rows, yt), 1);
  assert.equal(rows[0].channels.youtube.rank, 2); assert.equal(rows[0].evidence.length, 1); assert.deepEqual(rows[0].source_families, ["wiki", "video"]);
  assert.equal(T.mergeYouTube(rows, yt), 0);
  assert.equal(T.mergeYouTube(rows, null), 0);
  assert.equal(rows[1].channels.youtube, undefined);
});

const ghPath = path.join(here, "..", "catalog-data", "github-rank.json");
if (fs.existsSync(ghPath)) {
  const gh = JSON.parse(fs.readFileSync(ghPath, "utf8"));
  test("github rank: windows list the catalog's ranks, in rank order, with a licence and source on every repo", () => {
    ["daily", "weekly", "monthly"].forEach((w) => {
      const rows = T.ghRows(gh, { gwin: w });
      assert.deepEqual(rows.map((r) => r.repo), gh.ranks[w]);
      rows.forEach((r) => assert.ok(r.stars[w] >= gh.minimum_stars[w] && r.source_url && r.license, r.repo));
    });
    assert.ok(T.ghRows(gh, { gwin: "monthly" }).length >= T.ghRows(gh, { gwin: "weekly" }).length);
  });
  test("github rank: language filter, search and sort", () => {
    const langs = T.ghLanguages(gh, "monthly");
    assert.ok(langs.length >= 2);
    const l = langs[0][0];
    assert.equal(T.ghRows(gh, { gwin: "monthly", lang: l }).length, langs[0][1]);
    assert.ok(T.ghRows(gh, { gwin: "monthly", lang: l }).every((r) => (r.language || "No language") === l));
    assert.equal(T.ghRows(gh, { gwin: "monthly", q: "zzzzzz" }).length, 0);
    const tot = T.ghRows(gh, { gwin: "monthly", gsort: "total" }).map((r) => r.total_stars);
    assert.deepEqual(tot, tot.slice().sort((a, b) => b - a));
  });
}

console.log(n + " tests passed");

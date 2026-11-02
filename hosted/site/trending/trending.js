/* nicheclock "Trending now": one table of rising topics across channels, plus an Upcoming tab.
   Reads three JSON files from data-src (meta.json, trending.json, upcoming.json; schema in hosted/site/catalog-data/SCHEMA.md).
   No framework; every string from the data goes in as a text node. The logic (rules, filters, sort) has no DOM in it so
   `node hosted/site/trending/test.js` can test it. */
(function () {
  "use strict";

  const MIN_ROWS = 10;     // a category with fewer rows than this is flagged "limited data"
  const RISING = 1.5;      // a channel counts as rising at 1.5x its own baseline (the alert default)
  const URGENT_DAYS = 7;   // publish-by within this many days is flagged
  const DAY = 86400000;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const WINDOWS = ["4h", "24h", "48h", "7d"];
  const DATA_WINDOWS = ["24h", "48h", "7d"];   // 4h stays disabled: every source is daily or sampled, so an hourly view would be invented
  const WINDOW_OFF = { "4h": "4h data not available yet" };   // no source is hourly: the catalog is built once a day from daily pageviews and sampled news files
  const REGION_NAMES = { global: "Worldwide", multi: "Several countries", US: "United States", GB: "United Kingdom", CA: "Canada", AU: "Australia", DE: "Germany", FR: "France", ES: "Spain", JP: "Japan", IN: "India", BR: "Brazil" };
  const CHANNEL_NAMES = {
    wikipedia: ["Wikipedia", "Wiki"], news: ["News", "News"], hn: ["Hacker News", "HN"], federal_register: ["Federal Register", "FedReg"],
    github: ["GitHub", "GitHub"], apple: ["Apple charts", "Apple"], youtube: ["YouTube", "YouTube"], bluesky: ["Bluesky", "Bluesky"], calendar: ["Events", "Events"]
  };
    const regionName = (r) => REGION_NAMES[r] || r;

  /* The "for which channel" rules. Pure function of the source mix, age and seasonality; order is the display order. */
  const FOR_CHANNEL = [
    { id: "social", label: "Social", rule: "Active, listed within the last 48 hours, and News is rising at 2x or more or Hacker News has 50 or more points.",
      test: (c) => c.active && c.ageH <= 48 && ((c.up.news && c.ratio.news >= 2) || c.up.hn) },
    { id: "youtube", label: "YouTube / Shorts", rule: "Active, and either Wikipedia and News are both rising (readers and press agree, so an explainer has an audience) or the topic is on YouTube's chart.",
      test: (c) => c.active && ((c.up.wikipedia && c.up.news) || c.chart.youtube) },
    { id: "blog", label: "Blog / SEO", rule: "Active, Wikipedia is rising, and the topic is seasonal or has been listed for 3 days or more: reader demand that lasts long enough to get indexed.",
      test: (c) => c.active && c.up.wikipedia && (c.seasonal === "seasonal" || c.ageH >= 72) },
    { id: "newsletter", label: "Newsletter", rule: "Active, rising on two or more channels, and listed for at least a day: worth a line in a roundup.",
      test: (c) => c.active && c.upCount >= 2 && c.ageH >= 24 }
  ];

  /* ---------- logic ---------- */

  /* a channel's rise against its own baseline; null when the source gives none (Hacker News is a snapshot of points) */
  function channelRatio(ch) {
    if (!ch) return null;
    if (typeof ch.ratio === "number") return ch.ratio;
    if (ch.baseline > 0 && typeof ch.value === "number") return ch.value / ch.baseline;
    const s = ch.series || [];
    if (s.length >= 6) {
      const prior = s.slice(0, -2).slice().sort((a, b) => a - b);
      const m = prior[Math.floor(prior.length / 2)];
      return m > 0 ? s[s.length - 1] / m : null;
    }
    return null;
  }
  function channelKeys(t) { return Object.keys(t.channels || {}); }
  /* rising channels: the catalog says which (rising_channels); without it, 1.5x the channel's own baseline */
  function isUp(t, k) {
    if (Array.isArray(t.rising_channels)) return t.rising_channels.indexOf(k) >= 0;
    const r = channelRatio(t.channels[k]);
    return r !== null && r >= RISING;
  }
  /* the Source filter: rising on that source; a chart source (YouTube) counts when the topic is on the chart */
  function onSource(t, k) { const c = t.channels && t.channels[k]; return !!c && (c.chart ? true : isUp(t, k)); }
  /* trend: "rising" (rise strong or mild) or "steady" (a top item of its category, listed to fill it up; not rising) */
  function isSteady(t) { return t.trend === "steady" || t.status === "steady"; }
  function risingKeys(t) { return channelKeys(t).filter((k) => isUp(t, k)); }
  function risingCount(t) { return risingKeys(t).length; }
  function isActive(t, win) {
    const w = t.windows && t.windows[win];
    if (w && typeof w.active === "boolean") return w.active;
    return t.status !== "ended";
  }
  function ageHours(t, nowMs) { return Math.max(0, (nowMs - Date.parse(t.first_seen)) / 3600000); }

  /* -> [{id, label, rule, hit}] for every rule, so the page can show why a label was or was not given */
  function forChannel(t, nowMs) {
    const ctx = { active: t.status !== "ended" && !isSteady(t) && !isFading(t), ageH: ageHours(t, nowMs), seasonal: t.seasonal, up: {}, ratio: {}, chart: {} };
    channelKeys(t).forEach((k) => { ctx.up[k] = isUp(t, k); ctx.ratio[k] = channelRatio(t.channels[k]) || 0; ctx.chart[k] = isChart(t.channels[k]); });
    ctx.upCount = risingCount(t);
    return FOR_CHANNEL.map((r) => ({ id: r.id, label: r.label, rule: r.rule, hit: !!r.test(ctx) }));
  }

  /* the channel whose series draws the row's sparkline: the one rising fastest */
  function leadChannel(t) {
    let best = null, bestR = -1;
    channelKeys(t).forEach((k) => {
      const c = t.channels[k], r = channelRatio(c);
      if (c.series && c.series.length >= 2 && (r === null ? 0 : r) > bestR) { best = k; bestR = r === null ? 0 : r; }
    });
    return best;
  }

  function topicWindow(t, win) { return t.windows && t.windows[win] ? t.windows[win] : null; }

  /* ONE definition of Rise, used by the row, the detail table, the sort and the "rising" filter: for the chosen window, the
     largest ratio against the channel's own median of the 14 days before, among the rising channels that HAVE a baseline.
     kind: "ratio" (a real figure), "down" (below its baseline in this window: not rising here), "new" (the rising channel has
     no baseline, e.g. news at 0 articles before, or a Hacker News snapshot: a ratio would be a division by zero), "none". */
  function chWinRatio(c, w) {
    if (!c || c.chart) return null;
    const x = c.windows && c.windows[w];
    if (x && typeof x.ratio === "number") return x.ratio;
    return c.windows ? null : channelRatio(c);
  }
  function riseInfo(t, w) {
    const keys = channelKeys(t);
    const base = keys.filter((k) => t.channels[k].baseline > 0 && chWinRatio(t.channels[k], w) !== null);
    const up = base.filter((k) => isUp(t, k));
    if (!up.length && risingKeys(t).length) return { kind: "new", ratio: null, channel: null };
    let best = null;
    (up.length ? up : base).forEach((k) => { const r = chWinRatio(t.channels[k], w); if (!best || r > best.ratio) best = { ratio: r, channel: k }; });
    if (!best) return { kind: "none", ratio: null, channel: null };
    return { kind: best.ratio < 1 ? "down" : "ratio", ratio: best.ratio, channel: best.channel };
  }
  function riseText(info) { return info.kind === "new" ? "new (no baseline)" : info.kind === "none" ? "n/a" : pct(info.ratio) + (capped(info.ratio) ? " (" + factor(info.ratio) + ")" : ""); }
  /* a channel's own rise text for the chosen window; a channel with baseline 0 has no ratio */
  function chRiseText(c, w) {
    if (c.chart) return "none (chart place)";
    if (c.ratio_note || !(c.baseline > 0)) return typeof c.baseline === "number" ? "new (no baseline)" : "n/a";
    const r = chWinRatio(c, w);
    return r === null ? "n/a" : (c.censored ? "\u2265 " : "") + pct(r) + (capped(r) ? " (" + factor(r) + ")" : "") + (c.censored ? " lower bound" : "");
  }
  /* rows listed under "Trending now" that are not rising in the chosen window: steady / top rows and rows below their own baseline */
  function notRising(t, w) { return isSteady(t) || isFading(t) || riseInfo(t, w).kind === "down"; }

  /* how far the topic is from its peak. The catalog gives it on the row (peak: {days_ago, from_peak}); older files get it from the
     Wikipedia series (else the lead channel's): the last value against the 14-day maximum */
  function peakInfo(t) {
    let daysAgo, drop;
    if (t.peak && typeof t.peak.from_peak === "number") { daysAgo = t.peak.days_ago; drop = 1 - t.peak.from_peak; }
    else {
      const w = t.channels && t.channels.wikipedia;
      const k = w && w.series && w.series.length >= 6 ? "wikipedia" : leadChannel(t);
      const s = k && t.channels[k].series;
      if (!s || s.length < 6) return null;
      const max = Math.max.apply(null, s);
      if (!(max > 0)) return null;
      daysAgo = s.length - 1 - s.lastIndexOf(max); drop = 1 - s[s.length - 1] / max;
    }
    let day = null;
    if (t.series_end && String(t.series_end).length >= 10) { const d = new Date(utcDay(t.series_end) - daysAgo * DAY); day = MONTHS[d.getUTCMonth()] + " " + d.getUTCDate(); }
    return { drop, daysAgo, day };
  }
  const FADING = 0.5;   // the last value is under half of the 14-day peak (the catalog uses the same line)
  /* fading: the catalog says so, or the last day is under half of the 14-day peak and the peak is not the last day. A live Hacker News
     snapshot can keep the catalog's trend "rising" for a topic whose readers are long gone; the page still says it has peaked. */
  function isFading(t) {
    if (t.trend === "fading") return true;
    const p = peakInfo(t);
    return !!p && p.drop >= FADING && p.daysAgo >= 1;
  }
  function peakText(t) {
    const p = peakInfo(t);
    return p && p.drop > 0 ? "peaked " + (p.day || p.daysAgo + (p.daysAgo === 1 ? " day" : " days") + " before the data day") + ", now \u2212" + Math.round(p.drop * 100) + "% from the peak" : "";
  }

  function applyFilters(topics, f, nowMs) {
    const q = (f.q || "").trim().toLowerCase();
    return topics.filter((t) => {
      if (!topicWindow(t, f.window)) return false;
      if (f.region && f.region !== "all" && t.region !== f.region && t.region !== "global") return false;
      if (f.cat && f.cat !== "all" && t.category !== f.cat) return false;
      if (f.active && !isActive(t, f.window)) return false;
      if (f.multi && risingCount(t) < 2) return false;
      if (f.rising && notRising(t, f.window)) return false;
      if (f.src && f.src !== "all" && !onSource(t, f.src)) return false;
      if (f.type && f.type !== "all" && t.seasonal !== f.type) return false;
      if (q && t.label.toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  /* sort keys: rank = the catalog's rank_score (the files arrive sorted by it), volume = wikipedia/news value of the day (null last) */
  function sortTopics(topics, sort, dir, win) {
    const sign = dir === "asc" ? 1 : -1;
    const ratio = (t) => { const r = riseInfo(t, win); return r.kind === "new" ? 1 : r.ratio === null ? 0 : r.ratio; };
    const key = {
      rank: (t) => (notRising(t, win) ? -1e6 : 0) + (typeof t.rank_score === "number" ? t.rank_score : typeof t.score === "number" ? t.score : ratio(t)),   // steady rows come after rising ones
      rise: ratio,
      volume: (t) => (typeof t.volume === "number" ? t.volume : null),
      recency: (t) => Date.parse(t.first_seen),
      sources: (t) => risingCount(t) * 1000 + ratio(t)
    }[sort] || ratio;
    return topics.map((t, i) => ({ t, i, k: key(t) })).sort((a, b) => {
      if (a.k === null || b.k === null) return a.k === b.k ? a.i - b.i : a.k === null ? 1 : -1;   // no volume: always last
      return sign * (a.k - b.k) || a.i - b.i;
    }).map((x) => x.t);
  }
  /* counts per category over the rows every other filter lets through, like the counts in Google's category menu */
  function categoryCounts(topics, f, nowMs) {
    const counts = {};
    applyFilters(topics, Object.assign({}, f, { cat: "all" }), nowMs).forEach((t) => { counts[t.category] = (counts[t.category] || 0) + 1; });
    return counts;
  }

  /* YouTube data lives in git-ignored sidecars (youtube[-ID].json, 30-day storage limit of the API terms), keyed by Wikidata id.
     Merged into the rows here: the chart channel, its evidence and the "video" family; multi_source is not recomputed. */
  function mergeYouTube(rows, yt) {
    if (!yt || !yt.topics) return 0;
    let n = 0;
    rows.forEach((t) => {
      const y = t.qid && yt.topics[t.qid];
      if (!y || !y.channel || (t.channels && t.channels.youtube)) return;
      t.channels = Object.assign({}, t.channels, { youtube: y.channel });
      t.evidence = (t.evidence || []).concat(y.evidence || []);
      if ((t.source_families || []).indexOf("video") < 0) t.source_families = (t.source_families || []).concat("video");
      n++;
    });
    return n;
  }

  /* ---------- GitHub rank (github-rank.json): repos by stars gained in a window, like github.com/trending but counted from public events ---------- */

  const GH_WINDOWS = [["daily", "Today"], ["weekly", "This week"], ["monthly", "This month"]];
  /* repos listed for a window (the catalog's own ranks[window] decides who is in and in which order), then language filter, search and sort */
  function ghRows(gh, f) {
    const win = f.gwin || "weekly";
    const byName = {};
    (gh.repos || []).forEach((r) => { byName[r.repo] = r; });
    let rows = ((gh.ranks && gh.ranks[win]) || []).map((n) => byName[n]).filter(Boolean);
    if (f.lang && f.lang !== "all") rows = rows.filter((r) => (r.language || "") === f.lang);
    const q = (f.q || "").trim().toLowerCase();
    if (q) rows = rows.filter((r) => (r.repo + " " + (r.description || "")).toLowerCase().indexOf(q) >= 0);
    const key = { stars: (r) => (r.stars || {})[win] || 0, forks: (r) => (r.forks || {})[win] || 0, total: (r) => r.total_stars || 0 }[f.gsort || "stars"];
    return rows.map((r, i) => ({ r, i, k: key(r) })).sort((a, b) => b.k - a.k || a.i - b.i).map((x) => x.r);
  }
  function ghLanguages(gh, win) {
    const set = {};
    ((gh.ranks && gh.ranks[win]) || []).forEach((n) => { const r = (gh.repos || []).filter((x) => x.repo === n)[0]; if (r) set[r.language || "No language"] = (set[r.language || "No language"] || 0) + 1; });
    return Object.keys(set).sort((a, b) => set[b] - set[a] || a.localeCompare(b)).map((l) => [l, set[l]]);
  }

  /* ---------- formatting ---------- */

  function compact(n) {
    if (typeof n !== "number" || !isFinite(n)) return "n/a";
    const a = Math.abs(n);
    const f = (v, s) => (v >= 100 || Number.isInteger(v) ? Math.round(v) : v.toFixed(1).replace(/\.0$/, "")) + s;
    if (a >= 1e6) return f(n / 1e6, "M");
    if (a >= 1e3) return f(n / 1e3, "K");
    return String(Math.round(n));
  }
  /* A ratio against the channel's own 14-day median, in one format like Google Trends: "+411%", capped at "+1,000%+" (11x and more);
     the true multiple is always next to a capped figure (factor(), shown under the figure and in the tooltip). Below 1 is a fall. */
  const CAP_RATIO = 11;
  function capped(ratio) { return typeof ratio === "number" && isFinite(ratio) && ratio >= CAP_RATIO; }
  function factor(ratio) { return "\u00D7" + (ratio >= 100 ? Math.round(ratio) : Math.round(ratio * 10) / 10).toLocaleString("en-US"); }
  function pct(ratio) {
    if (typeof ratio !== "number" || !isFinite(ratio)) return "n/a";
    if (capped(ratio)) return "+1,000%+";
    if (ratio < 1) { const n = Math.round((1 - ratio) * 100); return n === 0 ? "0%" : "\u2212" + n + "%"; }
    return "+" + Math.round((ratio - 1) * 100).toLocaleString("en-US") + "%";
  }
  /* units as the catalog writes them are long ("articles/day (estimated from sampled 15-min GDELT files)"); chips use the short form and keep "est." */
  function unitShort(u) {
    u = u || "";
    if (/^pageviews\/day/.test(u)) return "views/day";
    if (/^HN points/.test(u)) return "points";
    const est = /estimated/i.test(u) ? " est." : "";
    return u.replace(/\s*\(.*$/, "") + est;
  }
  function chName(k, short) { const n = CHANNEL_NAMES[k]; return n ? n[short ? 1 : 0] : k.charAt(0).toUpperCase() + k.slice(1); }
  /* first_seen is a day (the first catalog day), not the moment the trend began: say "listed Oct 30", never "x hours ago" */
  function since(t, nowMs) {
    if (String(t.first_seen).length <= 10) return "listed " + MONTHS[+String(t.first_seen).slice(5, 7) - 1] + " " + (+String(t.first_seen).slice(8, 10));
    return ago(nowMs - Date.parse(t.first_seen));
  }
  function ago(ms) {
    const h = Math.round(ms / 3600000);
    if (h < 1) return "under 1 hour ago";
    if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
    const d = Math.round(h / 24);
    return d + (d === 1 ? " day ago" : " days ago");
  }
  function sparkLabel(name, unit, s) {
    return name + " " + unit + ", " + s.length + " days: from " + compact(s[0]) + " to " + compact(s[s.length - 1]) + ", peak " + compact(Math.max.apply(null, s));
  }
  function sparkPath(s, w, h) {
    const lo = Math.min.apply(null, s), hi = Math.max.apply(null, s), span = hi - lo || 1;
    return s.map((v, i) => (i ? "L" : "M") + (s.length > 1 ? (i * (w - 4)) / (s.length - 1) + 2 : w / 2).toFixed(1) + " " + (h - 3 - ((v - lo) / span) * (h - 6)).toFixed(1)).join("");
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function utcDay(s) { const p = String(s).slice(0, 10).split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }
  function dateLabel(s) { const d = new Date(utcDay(s)); return WEEKDAYS[d.getUTCDay()] + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCDate(); }

  /* ---------- URL state ---------- */

  const DEFAULTS = { tab: "trending", fit: false, nsort: "fit", nmode: "both", region: "all", window: "7d", cat: "all", type: "all", kind: "all", src: "all", gwin: "weekly", lang: "all", gsort: "stars", openOnly: true, rising: false, active: false, multi: false, q: "", sort: "rank", dir: "desc", open: "" };
  function parseHash(hash) {
    const m = String(hash || "").replace(/^#\/?/, "").split("?");
    const p = new URLSearchParams(m[1] || "");
    const s = Object.assign({}, DEFAULTS);
    s.tab = m[0] === "upcoming" || m[0] === "github" || m[0] === "niche" ? m[0] : "trending";
    ["region", "cat", "type", "kind", "src", "lang", "q", "open"].forEach((k) => { if (p.get(k)) s[k] = p.get(k); });
    if (DATA_WINDOWS.indexOf(p.get("window")) >= 0) s.window = p.get("window");
    if (["daily", "weekly", "monthly"].indexOf(p.get("gwin")) >= 0) s.gwin = p.get("gwin");
    if (["stars", "forks", "total"].indexOf(p.get("gsort")) >= 0) s.gsort = p.get("gsort");
    if (["rank", "rise", "volume", "recency", "sources"].indexOf(p.get("sort")) >= 0) s.sort = p.get("sort");
    if (p.get("dir") === "asc") s.dir = "asc";
    s.fit = p.get("fit") === "1";
    if (["fit", "rise", "volume"].indexOf(p.get("nsort")) >= 0) s.nsort = p.get("nsort");
    if (["rising", "steady"].indexOf(p.get("nmode")) >= 0) s.nmode = p.get("nmode");
    s.active = p.get("active") === "1";
    s.rising = p.get("rising") === "1";
    s.multi = p.get("multi") === "1";
    if (p.get("open_only") === "0") s.openOnly = false;   // Upcoming starts with windows that are still open
    return s;
  }
  function buildHash(s) {
    const p = new URLSearchParams();
    ["region", "window", "cat", "type", "kind", "src", "lang", "gwin", "gsort", "sort", "dir", "nsort", "nmode"].forEach((k) => { if (s[k] !== DEFAULTS[k]) p.set(k, s[k]); });
    if (s.fit) p.set("fit", "1");
    if (s.active) p.set("active", "1");
    if (s.rising) p.set("rising", "1");
    if (s.multi) p.set("multi", "1");
    if (!s.openOnly) p.set("open_only", "0");
    if (s.q) p.set("q", s.q);
    if (s.open) p.set("open", s.open);
    const qs = p.toString();
    return "#/" + s.tab + (qs ? "?" + qs : "");
  }

  if (typeof document === "undefined") {
    module.exports = { capped, factor, riseInfo, riseText, chRiseText, chWinRatio, notRising, peakInfo, peakText, isFading, mergeYouTube, ghRows, ghLanguages, MIN_ROWS, isSteady, RISING, FOR_CHANNEL, channelRatio, forChannel, risingCount, leadChannel, applyFilters, sortTopics, categoryCounts, compact, pct, ago, since, unitShort, sparkLabel, parseHash, buildHash, DEFAULTS };
    return;
  }


  /* ---------- DOM ---------- */

  const app = document.getElementById("app");
  const pageTitle = document.title;   // the home page's own title (what a search result shows); only the other tabs retitle it
  const base = app.getAttribute("data-src") || "/catalog-data/";
  let data = null;
  let state = parseHash(location.hash);
  const phone = window.matchMedia("(max-width: 44rem)");   // below this width every filter sits behind one Filters button

  function h(tag, props, kids) {
    const e = document.createElement(tag);
    for (const k in props || {}) {
      const v = props[k];
      if (v === false || v == null) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    (kids || []).forEach((c) => { if (c != null && c !== false) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function spark(series, label) {
    const w = 84, hh = 24;
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("class", "spark"); s.setAttribute("viewBox", "0 0 " + w + " " + hh); s.setAttribute("role", "img"); s.setAttribute("aria-label", label);
    const p = document.createElementNS(svgNS, "path"); p.setAttribute("d", sparkPath(series, w, hh)); s.appendChild(p);
    const lo = Math.min.apply(null, series), hi = Math.max.apply(null, series), span = hi - lo || 1;
    const c = document.createElementNS(svgNS, "circle");
    c.setAttribute("cx", String(w - 2)); c.setAttribute("cy", (hh - 3 - ((series[series.length - 1] - lo) / span) * (hh - 6)).toFixed(1)); c.setAttribute("r", "2.2");
    s.appendChild(c);
    return s;
  }

  /* Icons: Radix Icons 1.3.2 (MIT, github.com/radix-ui/icons), 15 px grid, the path data as published. */
  const ICONS = {
    search: "M10 6.5C10 8.433 8.433 10 6.5 10C4.567 10 3 8.433 3 6.5C3 4.567 4.567 3 6.5 3C8.433 3 10 4.567 10 6.5ZM9.30884 10.0159C8.53901 10.6318 7.56251 11 6.5 11C4.01472 11 2 8.98528 2 6.5C2 4.01472 4.01472 2 6.5 2C8.98528 2 11 4.01472 11 6.5C11 7.56251 10.6318 8.53901 10.0159 9.30884L12.8536 12.1464C13.0488 12.3417 13.0488 12.6583 12.8536 12.8536C12.6583 13.0488 12.3417 13.0488 12.1464 12.8536L9.30884 10.0159Z",
    chevron: "M3.13523 6.15803C3.3241 5.95657 3.64052 5.94637 3.84197 6.13523L7.5 9.56464L11.158 6.13523C11.3595 5.94637 11.6759 5.95657 11.8648 6.15803C12.0536 6.35949 12.0434 6.67591 11.842 6.86477L7.84197 10.6148C7.64964 10.7951 7.35036 10.7951 7.15803 10.6148L3.15803 6.86477C2.95657 6.67591 2.94637 6.35949 3.13523 6.15803Z",
    check: "M11.4669 3.72684C11.7558 3.91574 11.8369 4.30308 11.648 4.59198L7.39799 11.092C7.29783 11.2452 7.13556 11.3467 6.95402 11.3699C6.77247 11.3931 6.58989 11.3355 6.45446 11.2124L3.70446 8.71241C3.44905 8.48022 3.43023 8.08494 3.66242 7.82953C3.89461 7.57412 4.28989 7.55529 4.5453 7.78749L6.75292 9.79441L10.6018 3.90792C10.7907 3.61902 11.178 3.53795 11.4669 3.72684Z",
    close: "M11.7816 4.03157C12.0062 3.80702 12.0062 3.44295 11.7816 3.2184C11.5571 2.99385 11.193 2.99385 10.9685 3.2184L7.50005 6.68682L4.03164 3.2184C3.80708 2.99385 3.44301 2.99385 3.21846 3.2184C2.99391 3.44295 2.99391 3.80702 3.21846 4.03157L6.68688 7.49999L3.21846 10.9684C2.99391 11.193 2.99391 11.557 3.21846 11.7816C3.44301 12.0061 3.80708 12.0061 4.03164 11.7816L7.50005 8.31316L10.9685 11.7816C11.193 12.0061 11.5571 12.0061 11.7816 11.7816C12.0062 11.557 12.0062 11.193 11.7816 10.9684L8.31322 7.49999L11.7816 4.03157Z"
  };
  function icon(name, cls) {
    const s = document.createElementNS(svgNS, "svg");
    s.setAttribute("viewBox", "0 0 15 15"); s.setAttribute("width", "15"); s.setAttribute("height", "15"); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false");
    if (cls) s.setAttribute("class", cls);
    const p = document.createElementNS(svgNS, "path");
    p.setAttribute("d", ICONS[name]); p.setAttribute("fill", "currentColor"); p.setAttribute("fill-rule", "evenodd"); p.setAttribute("clip-rule", "evenodd");
    s.appendChild(p);
    return s;
  }

  let nowMs = Date.now();

  function setState(patch, replace) {
    state = Object.assign({}, state, patch);
    const url = buildHash(state);
    if (url !== location.hash) history[replace === false ? "pushState" : "replaceState"](null, "", url);
    render();
  }

  /* ---------- UI kit: segmented control, menu button with a popover, toggle chip, search, filter bar, filters sheet, empty state ----------
     A control is a plain description {kind, id, label, value, def, options, patch}; patch(value) gives the state change. The same
     descriptions draw the desktop bar (inline controls and popovers) and the phone sheet (one Filters button), on every view. */

  const C = {
    seg: (id, label, value, def, options, patch, extra) => Object.assign({ kind: "seg", id, label, value, def, options, patch }, extra),
    menu: (id, label, value, def, options, patch, extra) => Object.assign({ kind: "menu", id, label, value, def, options, patch }, extra),
    chip: (id, label, value, patch, extra) => Object.assign({ kind: "chip", id, label, value: !!value, def: false, patch }, extra),
    search: (id, label, value, patch, extra) => Object.assign({ kind: "search", id, label, value: value || "", def: "", patch }, extra)
  };
  const optLabel = (o) => o[1];
  const optMeta = (o) => o[2] || {};

  let closeMenu = null;       // closes the one open popover, if any

  /* a button that opens a listbox popover. value is kept here, so a form can use it too: onPick runs with the new value */
  function menuEl(c, onPick) {
    let value = c.value;
    const cur = () => c.options.filter((o) => o[0] === value)[0] || c.options[0];
    const isOn = () => c.def != null && value !== c.def;       // a form field has no default: it is never "on"
    const showVal = c.always || isOn();
    const mk = h("span", { class: "mk", text: c.label ? c.label + (showVal ? ":" : "") : "" });
    const mv = h("span", { class: "mv", text: showVal ? optLabel(cur()) : "" });
    const btn = h("button", { type: "button", class: "mbtn" + (isOn() ? " on" : "") + (c.full ? " full" : ""), id: c.domId || false, "aria-haspopup": "listbox", "aria-expanded": "false", "data-fk": "m-" + c.id, title: c.title || false, disabled: !!c.off }, [mk, mv, icon("chevron", "chev")]);
    const wrap = h("div", { class: "mwrap" + (c.end ? " end" : "") }, [btn]);
    let pop = null, outside = null;
    const close = (back) => {
      if (!pop) return;
      pop.remove(); pop = null; btn.setAttribute("aria-expanded", "false");
      document.removeEventListener("pointerdown", outside, true); closeMenu = null;
      if (back) btn.focus();
    };
    const pick = (o) => {
      if (optMeta(o).off) return;
      value = o[0];
      const sv = c.always || isOn();
      mk.textContent = c.label ? c.label + (sv ? ":" : "") : ""; mv.textContent = sv ? optLabel(o) : "";
      btn.classList.toggle("on", isOn());
      close(true);
      onPick(o[0]);
    };
    const open = () => {
      if (closeMenu) closeMenu();
      const items = c.options.map((o) => {
        const m = optMeta(o), sel = o[0] === value;
        return h("div", { class: "opt" + (sel ? " sel" : "") + (m.off ? " off" : ""), role: "option", tabindex: "-1", "data-v": o[0], "aria-selected": String(sel), "aria-disabled": m.off ? "true" : false, title: m.title || false,
          onclick: () => pick(o) }, [icon("check", "tick"), h("span", { class: "ol", text: optLabel(o) }), m.note ? h("span", { class: "on2", text: m.note }) : null, typeof m.count === "number" ? h("span", { class: "oc", text: String(m.count) }) : null]);
      });
      pop = h("div", { class: "pop", role: "listbox", "aria-label": c.label || "Options", tabindex: "-1" }, items);
      wrap.appendChild(pop);
      btn.setAttribute("aria-expanded", "true");
      const r = pop.getBoundingClientRect();
      if (r.right > window.innerWidth - 8) pop.classList.add("flip");
      outside = (e) => { if (!wrap.contains(e.target)) close(false); };
      document.addEventListener("pointerdown", outside, true);
      closeMenu = () => close(false);
      const start = items.filter((i) => i.classList.contains("sel"))[0] || items.filter((i) => !i.classList.contains("off"))[0];
      if (start) { start.focus(); start.scrollIntoView({ block: "nearest" }); }
    };
    btn.addEventListener("click", () => (pop ? close(true) : open()));
    btn.addEventListener("keydown", (e) => { if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !pop) { e.preventDefault(); open(); } });
    wrap.addEventListener("keydown", (e) => {
      if (!pop) return;
      const live = Array.prototype.filter.call(pop.children, (i) => !i.classList.contains("off")), at = live.indexOf(document.activeElement);
      const go = (i) => { const t = live[(i + live.length) % live.length]; if (t) { t.focus(); t.scrollIntoView({ block: "nearest" }); } };
      if (e.key === "ArrowDown") { e.preventDefault(); go(at + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); go(at < 0 ? -1 : at - 1); }
      else if (e.key === "Home") { e.preventDefault(); go(0); }
      else if (e.key === "End") { e.preventDefault(); go(live.length - 1); }
      else if (e.key === "Enter" || e.key === " ") { if (e.target.classList.contains("opt")) { e.preventDefault(); e.target.click(); } }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "Tab") close(false);
      else if (e.key.length === 1 && /\S/.test(e.key)) {     // type-ahead: the next option that starts with the letter
        const ch = e.key.toLowerCase(), from = at + 1;
        const order = live.slice(from).concat(live.slice(0, from)).filter((i) => i.textContent.replace(/^\s+/, "").toLowerCase().indexOf(ch) === 0);
        if (order[0]) order[0].focus();
      }
    });
    wrap.getValue = () => value;
    return wrap;
  }

  function segEl(c, onPick) {
    return h("div", { class: "segc" + (c.end ? " end" : ""), role: "group", "aria-label": c.label }, c.options.map((o) => {
      const m = optMeta(o);
      return h("button", { type: "button", class: "sb" + (m.off ? " off" : ""), "data-fk": "seg-" + c.id + "-" + o[0], "aria-pressed": String(c.value === o[0]), "aria-disabled": m.off ? "true" : false, "aria-description": m.off ? m.title : false, title: m.title || false, text: optLabel(o), onclick: () => { if (!m.off) onPick(o[0]); } });
    }));
  }
  function chipEl(c, onPick) {
    return h("button", { type: "button", class: "tg", "data-fk": "chip-" + c.id, "aria-pressed": String(c.value), title: c.title || false, onclick: () => onPick(!c.value) }, [icon("check", "tick"), c.label]);
  }
  function searchEl(c, onPick) {
    const input = h("input", { type: "search", class: "search-in", "data-fk": "q", placeholder: c.label, "aria-label": c.label, value: c.value, autocomplete: "off", spellcheck: "false",
      oninput: (e) => { clearTimeout(input._t); input._t = setTimeout(() => onPick(e.target.value), 160); } });
    return h("label", { class: "search" }, [icon("search", "sicon"), input]);
  }
  function controlEl(c) {
    const apply = (v) => setState(c.patch(v), c.nav && c.nav(v) ? false : undefined);
    return c.kind === "seg" ? segEl(c, apply) : c.kind === "menu" ? menuEl(c, apply) : c.kind === "chip" ? chipEl(c, apply) : searchEl(c, apply);
  }

  function resetPatch(controls) {
    return Object.assign.apply(null, [{}].concat(controls.map((c) => c.patch(c.def)), [{ open: "" }]));
  }

  /* the filters sheet (phone): the controls as option groups; nothing changes until Show */
  function openSheet(controls, o, opener) {
    const draft = {}, syncs = [];
    controls.forEach((c) => { draft[c.id] = c.value; });
    const dlg = h("dialog", { class: "sheet", "aria-label": "Filters" });
    const changed = () => controls.filter((c) => draft[c.id] !== c.value);
    const patchOf = () => Object.assign.apply(null, [{}].concat(changed().map((c) => c.patch(draft[c.id])), [{ open: "" }]));
    const show = h("button", { type: "button", class: "btn", onclick: () => { const p = patchOf(), nav = changed().some((c) => c.nav && c.nav(draft[c.id])); dlg.close(); setState(p, nav ? false : undefined); } });
    const label = () => { const n = o.preview ? o.preview(patchOf()) : null; show.textContent = n === null ? "Show results" : "Show " + n + " " + (o.noun || "results"); };
    const group = (c) => {
      const box = h("div", { class: "opts", role: "radiogroup", "aria-label": c.label }, c.options.map((op) => {
        const m = optMeta(op);
        return h("button", { type: "button", class: "ro" + (m.off ? " off" : ""), role: "radio", "data-v": op[0], "aria-checked": "false", "aria-disabled": m.off ? "true" : false, "aria-description": m.off ? m.title : false, title: m.title || false,
          onclick: () => { if (m.off) return; draft[c.id] = op[0]; sync(); label(); } }, [optLabel(op), typeof m.count === "number" ? h("span", { class: "oc", text: String(m.count) }) : null, m.off && m.note ? h("span", { class: "on2", text: m.note }) : null]);
      }));
      const sync = () => Array.prototype.forEach.call(box.children, (b) => b.setAttribute("aria-checked", String(b.getAttribute("data-v") === String(draft[c.id]))));
      syncs.push(sync); sync();
      return h("div", { class: "grp" }, [h("h3", { text: c.label }), box]);
    };
    const chips = controls.filter((c) => c.kind === "chip");
    const chipBox = h("div", { class: "opts" }, chips.map((c) => {
      const b = h("button", { type: "button", class: "tg", "aria-pressed": "false", title: c.title || false, onclick: () => { draft[c.id] = !draft[c.id]; sync(); label(); } }, [icon("check", "tick"), c.label]);
      const sync = () => b.setAttribute("aria-pressed", String(!!draft[c.id]));
      syncs.push(sync); sync();
      return b;
    }));
    const body = h("div", { class: "sheet-body" }, controls.filter((c) => c.kind !== "chip").map(group).concat(chips.length ? [h("div", { class: "grp" }, [h("h3", { text: "Show only" }), chipBox])] : []));
    dlg.appendChild(h("div", { class: "sheet-head" }, [h("h2", { text: "Filters" }), h("button", { type: "button", class: "ibtn", "aria-label": "Close filters", onclick: () => dlg.close() }, [icon("close")])]));
    dlg.appendChild(body);
    dlg.appendChild(h("div", { class: "sheet-foot" }, [h("button", { type: "button", class: "btn2", text: "Reset", onclick: () => { controls.forEach((c) => { draft[c.id] = c.def; }); syncs.forEach((f) => f()); label(); } }), show]));
    dlg.addEventListener("close", () => { dlg.remove(); if (opener.isConnected) opener.focus(); });
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    label();
    document.body.appendChild(dlg);
    dlg.showModal();
  }

  /* the bar: controls carry row (1 or 2) and end (pushed to the right); on a phone only search stays out, the rest sits behind Filters */
  function filterBar(controls, o) {
    o = o || {};
    if (closeMenu) closeMenu();
    const active = controls.filter((c) => c.value !== c.def && c.kind !== "search");
    const searchCtl = controls.filter((c) => c.kind === "search")[0];
    if (phone.matches) {
      const rest = controls.filter((c) => c.kind !== "search");
      const btn = h("button", { type: "button", class: "mbtn filters-btn" + (active.length ? " on" : ""), "data-fk": "filters-open", "aria-haspopup": "dialog" }, [h("span", { class: "mk", text: "Filters" }), active.length ? h("span", { class: "badge", text: String(active.length) }) : null]);
      btn.addEventListener("click", () => openSheet(rest, o, btn));
      return h("div", { class: "fbar phone", role: "search" }, [h("div", { class: "frow" }, [searchCtl ? controlEl(searchCtl) : null, btn])]);
    }
    const rows = {};
    controls.forEach((c) => { (rows[c.row || 1] = rows[c.row || 1] || []).push(c); });
    const keys = Object.keys(rows).sort();
    const last = keys[keys.length - 1];
    const dirty = controls.some((c) => c.value !== c.def);
    return h("div", { class: "fbar", role: "search" }, keys.map((k) => h("div", { class: "frow" }, rows[k].map(controlEl).concat(k === last && dirty ? [h("button", { type: "button", class: "lnk end", "data-fk": "reset", text: "Reset filters", onclick: () => setState(resetPatch(controls)) })] : []))));
  }

  /* one empty state for every view: what is missing, what to try, an optional action */
  function emptyState(title, text, action) {
    return h("div", { class: "state" }, [h("strong", { text: title }), text ? h("p", { text }) : null, action || null]);
  }

  /* table cells shared by the Trending, Upcoming and Your niche views */
  function riseTd(o) {
    return h("td", { class: "num rise" + (o.tier ? " t" + o.tier : "") + (o.mute ? " mute" : ""), "data-label": o.label, title: o.title || false }, [
      h("span", { class: "rv" }, [o.arrow ? h("span", { class: "tri" + (o.arrow === "down" ? " down" : o.arrow === "flat" ? " flat" : ""), "aria-hidden": "true" }) : null, o.text]),
      o.sub ? h("span", { class: "via", text: o.sub }) : null]);
  }
  const tierOf = (ratio) => (ratio >= 5 ? 3 : ratio >= 2 ? 2 : 1);   // restrained strength: x5 and up is accent, x2 and up is ink, below is grey
  function statusCell(kind, text, sub, title, label) {
    return h("td", { class: "stat", "data-label": label || "Status", title: title || false }, [h("span", { class: "st " + kind, text }), sub ? h("span", { class: "when", text: sub }) : null]);
  }
  function tags(list) {
    return h("div", { class: "tags" }, list.map((x) => h("span", { class: "tag" + (x.up ? " up" : "") + (x.main ? " main" : ""), title: x.title || false }, [x.text, x.up ? h("span", { class: "sr", text: " (rising)" }) : null])));
  }


  /* regions: meta.regions (Worldwide + countries, one trending file each) when the catalog has them; else the old client-side filter */
  function regionList() { return Array.isArray(data.meta.regions) && data.meta.regions.length && typeof data.meta.regions[0] === "object" ? data.meta.regions : null; }
  function regionId(r) { const l = regionList(); return l ? (l.some((x) => x.id === r) ? r : l[0].id) : r; }
  function regionMeta() { const l = regionList(); return l ? l.filter((x) => x.id === regionId(state.region))[0] : null; }
  function curTopics() { return regionList() ? (data.byRegion[regionId(state.region)] || []) : data.topics; }
  function regionFile(id) {
    const m = regionList().filter((x) => x.id === id)[0];
    return (m && m.file) || (id === "global" ? "trending.json" : "trending-" + id + ".json");
  }
  function filterState() { return regionList() ? Object.assign({}, state, { region: "all" }) : state; }   // region files are already regional
  function channelsSeen(topics) { const set = new Set(); topics.forEach((t) => channelKeys(t).forEach((k) => set.add(k))); return set; }

  /* rows per category in the loaded region, counted from the rows the page actually has in the selected window (not from meta.coverage,
     which can describe a build the served files do not hold); the catalog's coverage rule counts the same rows for the default 7d window */
  function coverageOf(cat) {
    const rows = curTopics().filter((t) => t.category === cat && topicWindow(t, state.window));
    const steady = rows.filter(isSteady).length;
    return { rising: rows.length - steady, steady, total: rows.length, limited: rows.length < MIN_ROWS };
  }
  function topicRegions() { return Array.from(new Set(data.topics.map((t) => t.region))).sort(); }
  function eventRegions() {
    const set = new Set();
    data.events.forEach((e) => { set.add(e.region); (e.regions || []).forEach((r) => set.add(r)); });
    return Array.from(set).sort((a, b) => regionName(a).localeCompare(regionName(b)));
  }
  function eventKinds() { return Array.from(new Set(data.events.map((e) => e.kind))).sort(); }

  /* ---------- controls of each view ---------- */

  const searchControl = (label, patch) => C.search("q", label, state.q, patch, { row: 1 });
  /* Show: all topics, or only those that fit the saved niche (Trending and Upcoming); no profile yet sends the visitor to the profile page */
  function showControl() {
    return C.seg("show", "Show", state.fit ? "fit" : "all", "all", [["all", "All topics"], ["fit", "For your niche"]],
      (v) => (v === "fit" ? (profileName() ? { fit: true, open: "" } : { tab: "niche", fit: false, open: "" }) : { fit: false, open: "" }),
      { row: 1, nav: (v) => v === "fit" && !profileName() });
  }
  function regionControl(trending) {
    const rl = regionList();
    const regions = rl ? rl.map((x) => x.id) : ["all"].concat(trending ? topicRegions() : eventRegions());
    const label = (r) => {
      if (r === "all") return "All regions";
      if (!rl) return regionName(r);
      const m = rl.filter((x) => x.id === r)[0];
      return m.label + (m.native_label && m.native_label !== m.label ? " \u00B7 " + m.native_label : "");
    };
    const only = trending && !rl && regions.length <= 2;      // every topic is worldwide today: say so instead of offering a filter that does nothing
    return C.menu("region", "Region", rl ? regionId(state.region) : state.region, rl ? rl[0].id : "all", regions.map((r) => [r, label(r)]), (v) => ({ region: rl && v === rl[0].id ? "all" : v, open: "" }),
      { always: true, row: 1, off: only, title: only ? "Every topic is worldwide for now: the sources carry no country split." : false });
  }
  /* source filter: topics rising on one source. A source with no data for this region is listed as "no data" and cannot be picked */
  function sourceControl() {
    const have = channelsSeen(curTopics());
    const rm = regionMeta();
    const status = (k) => {
      if (k === "youtube" && !(data.yt && data.yt[regionList() ? regionId(state.region) : "global"])) return { status: "no_data", detail: "The YouTube chart file is not present in this build." };
      const st = rm && rm.source_status && rm.source_status[k];
      if (st) return st;
      if (rm && Array.isArray(rm.sources)) return { status: rm.sources.indexOf(k) >= 0 ? "ok" : "no_data" };
      return { status: have.has(k) ? "ok" : "no_data" };
    };
    const listed = Array.isArray(data.meta.source_filter) ? data.meta.source_filter.map((x) => x.key) : ["wikipedia", "news", "hn"];
    const keys = Array.from(new Set(listed.concat(Array.from(have))));
    const label = (k) => { const f = (data.meta.source_filter || []).filter((x) => x.key === k)[0]; return f ? f.label : chName(k); };
    const note = { no_data: "no data here", pending_key: "needs a key", disabled: "off", limited: "limited" };
    const opts = [["all", "All sources"]].concat(keys.map((k) => {
      const st = status(k), off = st.status === "no_data" || st.status === "pending_key" || st.status === "disabled";
      return [k, label(k), { off, title: st.detail || false, note: note[st.status], count: off ? undefined : curTopics().filter((t) => onSource(t, k)).length }];
    }));
    return C.menu("src", "Source", state.src, "all", opts, (v) => ({ src: v, open: "" }), { row: 2, title: "Show topics rising on one source" });
  }
  function categoryControl(trending, row) {
    const counts = trending ? categoryCounts(curTopics(), filterState(), nowMs) : {};
    const total = Object.keys(counts).reduce((n, k) => n + counts[k], 0);
    const opts = [["all", "All categories", trending ? { count: total } : {}]].concat((data.meta.categories || []).map((c) =>
      [c.id, c.label, trending ? { count: counts[c.id] || 0, note: coverageOf(c.id).limited ? "limited data" : undefined } : {}]));
    return C.menu("cat", "Category", state.cat, "all", opts, (v) => ({ cat: v, open: "" }), { row });
  }
  function trendingControls() {
    const sortKeys = [["rank:desc", "Relevance"], ["rise:desc", "Biggest rise"], ["volume:desc", "Highest volume"], ["recency:desc", "Newest first"], ["recency:asc", "Oldest first"], ["sources:desc", "Most channels rising"], ["rise:asc", "Smallest rise"]];
    const c = [searchControl("Search topics", (v) => ({ q: v, open: "" }))];
    if (nicheAvailable()) c.push(showControl());
    c.push(regionControl(true));
    c.push(C.seg("window", "Time window", state.window, "7d", WINDOWS.map((w) => [w, w, WINDOW_OFF[w] ? { off: true, title: WINDOW_OFF[w] } : {}]), (v) => ({ window: v, open: "" }), { row: 1 }));
    c.push(C.menu("sort", "Sort", state.sort + ":" + state.dir, "rank:desc", sortKeys, (v) => { const p = v.split(":"); return { sort: p[0], dir: p[1] }; }, { always: true, row: 1, end: true }));
    c.push(categoryControl(true, 2));
    c.push(sourceControl());
    c.push(C.menu("type", "Type", state.type, "all", [["all", "All topics"], ["seasonal", "Seasonal"], ["new", "New"]], (v) => ({ type: v, open: "" }), { row: 2 }));
    c.push(C.chip("rising", "Rising only", state.rising, (v) => ({ rising: v, open: "" }), { row: 2 }));
    c.push(C.chip("active", "Active only", state.active, (v) => ({ active: v, open: "" }), { row: 2 }));
    c.push(C.chip("multi", "Multi-source only", state.multi, (v) => ({ multi: v, open: "" }), { row: 2 }));
    return c;
  }
  function upcomingControls() {
    const c = [searchControl("Search events", (v) => ({ q: v, open: "" }))];
    if (nicheAvailable()) c.push(showControl());
    c.push(regionControl(false));
    c.push(categoryControl(false, 1));
    c.push(C.menu("kind", "Kind", state.kind, "all", [["all", "All kinds"]].concat(eventKinds().map((k) => [k, k.charAt(0).toUpperCase() + k.slice(1)])), (v) => ({ kind: v }), { row: 1 }));
    c.push(C.chip("open", "Publish window open", state.openOnly, (v) => ({ openOnly: v }), { row: 1, def: true }));
    return c;
  }
  function ghControls() {
    const langs = ghLanguages(data.gh, state.gwin);
    return [searchControl("Search repositories", (v) => ({ q: v })),
      C.seg("gwin", "Time window", state.gwin, "weekly", GH_WINDOWS, (v) => ({ gwin: v, lang: "all", open: "" }), { row: 1 }),
      C.menu("lang", "Language", state.lang, "all", [["all", "All languages", { count: langs.reduce((n, l) => n + l[1], 0) }]].concat(langs.map((l) => [l[0], l[0], { count: l[1] }])), (v) => ({ lang: v }), { row: 1 }),
      C.menu("gsort", "Sort", state.gsort, "stars", [["stars", "Stars in window"], ["forks", "Forks in window"], ["total", "Total stars"]], (v) => ({ gsort: v }), { always: true, row: 1, end: true })];
  }
  function filterBarFor() {
    if (state.tab === "github") return filterBar(ghControls(), { noun: "repositories" });
    if (state.tab === "upcoming") return filterBar(upcomingControls(), { noun: "events" });
    return filterBar(trendingControls(), { noun: "topics", preview: (patch) => applyFilters(curTopics(), Object.assign({}, filterState(), patch, regionList() ? { region: "all" } : {}), nowMs).length });
  }

  /* ---------- Trending table ---------- */

  /* YouTube is a chart presence: a rank and, when the API gave it, raw views. No rise figure (its terms forbid derived metrics). */
  function isChart(c) { return !!(c && c.chart); }
  function volText(c) { return isChart(c) ? "#" + c.rank : /chart rank/i.test(c.unit || "") ? "#" + c.value : compact(c.value); }
  function volUnit(c) { return isChart(c) ? "on chart" : unitShort(c.unit); }
  function volTitle(t, k) {
    const c = t.channels[k];
    return isChart(c) ? chName(k) + ": rank #" + c.rank + " on the chart" + (typeof c.value === "number" ? ", " + compact(c.value) + " " + (c.unit || "views") : "") + "; no rise figure" : chName(k) + ": " + compact(c.value) + " " + c.unit + (typeof c.baseline === "number" ? " (baseline " + compact(c.baseline) + ")" : " (a snapshot: no baseline)");
  }
  /* Volume: the channel the Rise figure comes from, in its own unit; the other channels are counted ("+1") and listed in the tooltip and in the opened row */
  function volCell(t) {
    const keys = channelKeys(t), ri = riseInfo(t, state.window);
    const k = (ri.channel && keys.indexOf(ri.channel) >= 0 ? ri.channel : leadChannel(t)) || keys[0];
    if (!k) return h("td", { class: "num vols", "data-label": "Volume" }, [h("span", { class: "rv dim", text: "n/a" })]);
    const c = t.channels[k], more = keys.length - 1;
    return h("td", { class: "num vols", "data-label": "Volume", title: keys.map((x) => volTitle(t, x)).join("\n") }, [
      h("span", { class: "rv", text: volText(c) }), h("span", { class: "via", text: (isChart(c) ? chName(k, true) + " " : "") + volUnit(c) + (more > 0 ? " +" + more : "") })]);
  }
  function srcCell(t) {
    return h("td", { class: "srcs", "data-label": "Sources" }, [tags(channelKeys(t).map((k) => {
      const up = isUp(t, k), c = t.channels[k];
      return { text: chName(k, true), up, title: chName(k) + (up ? " is rising" : " is present, not rising") + (isChart(c) ? ": a chart place, no rise figure" : c.baseline === 0 ? ": no earlier history, so no ratio" : chWinRatio(c, state.window) === null ? "" : ": " + chRiseText(c, state.window) + " in the " + state.window + " window vs its own 14-day median") };
    }))]);
  }
  function forCell(t) {
    const hits = forChannel(t, nowMs).filter((r) => r.hit);
    return h("td", { class: "fors", "data-label": "For which channel" }, [hits.length ? h("span", { class: "forl", title: hits.map((r) => r.label).join(", "), text: hits.slice(0, 2).map((r) => r.label).join(", ") }) : h("span", { class: "dim", text: isSteady(t) ? "Not rising" : t.status === "ended" ? "Ended" : "Watch" })]);
  }
  function statCell(t) {
    const ended = t.status === "ended", fading = !isSteady(t) && !ended && isFading(t);   // an ended row says Ended; fading = past its peak
    const kind = isSteady(t) ? "steady" : ended ? "ended" : fading ? "fading" : "active";
    const pk = fading ? peakInfo(t) : null;
    return statusCell(kind, isSteady(t) ? "Steady / Top" : ended ? "Ended" : fading ? "Fading" : "Active", fading && pk && pk.day ? "peaked " + pk.day : since(t, nowMs),
      (fading ? peakText(t) + ". " : "") + "First day this topic was listed in the catalog. It is not the day the trend began.");
  }
  function sparkCell(t) {
    const k = leadChannel(t);
    const c = k && t.channels[k];
    if (!c || !c.series || c.series.length < 2) return h("span", { class: "dim", text: "no series" });
    const sp = spark(c.series, sparkLabel(chName(k), unitShort(c.unit), c.series));
    if (isSteady(t)) sp.setAttribute("class", "spark steady");
    return sp;
  }

  function th(label, key, tip, cls, sub) {
    const sorted = state.sort === key;
    const attrs = { scope: "col" };
    if (key) attrs["aria-sort"] = sorted ? (state.dir === "asc" ? "ascending" : "descending") : "none";
    const inner = key ? h("button", { type: "button", class: "thbtn", "data-fk": "th-" + key, title: tip, onclick: () => setState(sorted ? { dir: state.dir === "asc" ? "desc" : "asc" } : { sort: key, dir: "desc" }) },
      [label, sorted ? icon("chevron", "sortm" + (state.dir === "asc" ? " up" : "")) : null, sub ? h("span", { class: "sr", text: " " + sub }) : null]) : h("span", { text: label, title: tip });
    return h("th", Object.assign(attrs, { class: cls || "" }), [inner]);
  }

  /* names related to the topic: Wikidata aliases and "breakdown" names from the catalog; else titles on other Wikipedias */
  function relatedNames(t) {
    const seen = new Set([String(t.label).toLowerCase()]), out = [];
    const add = (x) => { const k = String(x || "").trim(); if (k && !seen.has(k.toLowerCase())) { seen.add(k.toLowerCase()); out.push(k); } };
    (t.aliases || []).forEach(add);
    (t.breakdown || []).forEach(add);
    if (!out.length) {
      const bw = (t.channels.wikipedia && t.channels.wikipedia.by_wiki) || {};
      Object.keys(bw).forEach((w) => { if (bw[w] && bw[w].value > 0) add(bw[w].title); });
    }
    return out.slice(0, 12);
  }

  /* the opened row: a panel under the row, breakdown on the left, the rules and the evidence on the right */
  function detail(t) {
    const rules = forChannel(t, nowMs);
    const win = topicWindow(t, state.window);
    const country = regionList() && regionId(state.region) !== regionList()[0].id;
    const chRows = channelKeys(t).map((k) => {
      const c = t.channels[k], chart = isChart(c);
      let title = chName(k);
      if (k === "wikipedia" && (c.wiki || c.scope)) title += " (" + [c.wiki, c.scope].filter(Boolean).join(", ") + ")";
      if (k === "news" && country) title += " (places in this country)";
      const latest = chart ? "rank #" + c.rank + (typeof c.value === "number" ? ", " + compact(c.value) + " " + (c.unit || "views") : "") : compact(c.value) + " " + unitShort(c.unit);
      const rise = chRiseText(c, state.window);
      return h("tr", {}, [
        h("th", { scope: "row", text: title }),
        h("td", { class: "num", "data-label": "Latest", text: latest }),
        h("td", { class: "num", "data-label": "Baseline", text: typeof c.baseline === "number" ? compact(c.baseline) + " " + unitShort(c.unit) : chart ? "none (chart)" : "none (snapshot)" }),
        h("td", { class: "num", "data-label": "Rise", title: c.censored ? "Country view counts are censored for small pages, so the true rise is at least this" : false, text: rise }),
        h("td", { class: "mspark" }, [c.series && c.series.length > 1 ? spark(c.series, sparkLabel(chName(k), unitShort(c.unit), c.series)) : "no series"]),
        h("td", { class: "daily" }, [c.series && c.series.length > 1 ? h("details", {}, [h("summary", { text: "Daily values" }), h("p", { text: c.series.map(compact).join(", ") + " " + unitShort(c.unit) + " (oldest first, ending " + (t.series_end || "on the data day") + ")" })]) : ""])
      ]);
    });
    const ev = (t.evidence || []).map((e) => {
      const lic = e.license ? e.license : "licence unknown";
      return h("li", { class: "ev" }, [
        h("p", { class: "evt" }, [e.source_url ? h("a", { href: e.source_url, rel: "noopener noreferrer", text: e.title || e.source }) : (e.title || e.source)]),
        h("p", { class: "evm" }, [(typeof e.value === "number" ? compact(e.value) + " " + unitShort(e.unit) + " \u00B7 " : ""), sourceLabel(e.source), " \u00B7 ", h("span", { class: "lic" + (e.license ? "" : " bad"), text: lic })])
      ]);
    });
    const names = relatedNames(t);
    const via = t.category_via && t.category_via.label ? "placed by Wikidata " + t.category_via.kind + ": " + t.category_via.label : "";
    return h("div", { class: "detail-body" }, [
      h("div", { class: "pcol" }, [
        h("h3", { text: "Trend breakdown" }),
        t.description ? h("p", { class: "dim", text: t.description }) : null,
        h("h4", { text: "Related names" }),
        names.length ? tags(names.map((n) => ({ text: n }))) : h("p", { class: "dim", text: "The catalog lists no related names for this topic." }),
        h("p", { class: "dim small", text: "Wikidata aliases, titles of the article on other Wikipedias and names counted in the news. These are names, not search queries." }),
        isFading(t) && !isSteady(t) ? h("p", { class: "fade", role: "note" }, [h("b", { text: "Fading. " }), "This topic " + peakText(t) + ". The Rise figure compares the window with the weeks before the spike; it does not mean the topic is still going up."]) : null,
        h("h4", { text: "Channels" }),
        h("p", { class: "dim", text: "Each channel is measured in its own unit and is never added to another. Rise is the " + state.window + " window against that channel's own median of the 14 days before. The Rise on the row is the largest figure here among the rising channels that have a baseline." }),
        h("table", { class: "mini" }, [h("thead", {}, [h("tr", {}, ["Channel", "Latest", "Baseline", "Rise (" + state.window + ")", "Last " + ((t.channels[channelKeys(t)[0]] || {}).series || []).length + " days", ""].map((x, i) => h("th", { scope: "col", class: i > 0 && i < 4 ? "num" : "", text: x })))]), h("tbody", {}, chRows)])
      ]),
      h("div", { class: "pcol" }, [
        h("h3", { text: "For which channel" }),
        h("ul", { class: "rules" }, rules.map((r) => h("li", { class: r.hit ? "hit" : "" }, [h("b", { text: r.label + (r.hit ? ": yes." : ": no.") }), " ", r.rule]))),
        h("h4", { text: "Evidence and licence" }),
        ev.length ? h("ul", { class: "evl" }, ev) : h("p", { class: "dim", text: "No evidence entries in the data for this topic." }),
        h("p", { class: "dim meta", text: [t.category_label || t.category, via, t.region === "global" ? "worldwide" : t.region, win ? "rise in " + state.window + " window: " + riseText(riseInfo(t, state.window)) : "", t.seasonal && t.seasonal !== "unknown" ? t.seasonal : "", t.qid ? "Wikidata " + t.qid : "no Wikidata id"].filter(Boolean).join(" \u00B7 ") })
      ])
    ]);
  }

  function riseCell(t) {
    const ri = riseInfo(t, state.window);
    const lbl = "Rise " + state.window, fade = isFading(t);
    if (isSteady(t)) return riseTd({ label: lbl, mute: true, arrow: "flat", text: "Steady", title: "Steady / Top: not rising. Listed because it is among the top topics of its category." });
    if (ri.kind === "down") return riseTd({ label: lbl, mute: true, arrow: "down", text: pct(ri.ratio), title: "Below its own 14-day median in the " + state.window + " window, so not rising here. Another window may differ." });
    if (ri.kind === "new") return riseTd({ label: lbl, mute: fade, tier: fade ? 0 : 2, arrow: "up", text: "new", title: "Rising, but the rising channel has no earlier history to compare with (zero articles before, or a Hacker News snapshot), so there is no ratio." });
    if (ri.kind === "none") return riseTd({ label: lbl, mute: true, text: "n/a", title: "No channel with a baseline in this window." });
    const cap = capped(ri.ratio);
    return riseTd({ label: lbl, mute: fade, tier: fade ? 0 : tierOf(ri.ratio), arrow: "up", text: pct(ri.ratio), sub: cap ? factor(ri.ratio) : null,
      title: chName(ri.channel) + " in the " + state.window + " window against its own median of the 14 days before" + (cap ? ": " + factor(ri.ratio) + " (the figure is capped at +1,000%+)" : "") + (t.rise === "mild" ? ". Mild rise: at least 1.2x" : "") });
  }

  function trendingTable(rows) {
    const body = [];
    rows.forEach((t) => {
      const open = state.open === t.id;
      const toggleRow = () => setState({ open: open ? "" : t.id });
      const tr = h("tr", { class: "row" + (open ? " open" : ""), "data-id": t.id, onclick: (e) => { if (!e.target.closest("a,button,summary")) toggleRow(); } }, [
        h("td", { class: "topic" }, [
          h("button", { type: "button", class: "rowbtn", title: t.label, "data-fk": "row-" + t.id, "aria-expanded": String(open), "aria-controls": "d-" + t.id, onclick: toggleRow }, [h("span", { class: "tt", text: t.label })]),
          h("span", { class: "cat", text: (t.category_label || t.category) + (t.seasonal === "seasonal" ? " \u00B7 seasonal" : t.seasonal === "new" ? " \u00B7 new" : "") + (t.rise === "mild" ? " \u00B7 mild rise" : "") })]),
        riseCell(t),
        volCell(t),
        h("td", { class: "sparkc", "data-label": "Trend" }, [sparkCell(t)]),
        srcCell(t),
        forCell(t),
        statCell(t)
      ]);
      body.push(tr);
      if (open) body.push(h("tr", { class: "detail", id: "d-" + t.id }, [h("td", { colspan: "7" }, [detail(t)])]));
    });
    return h("table", { class: "trend main" }, [
      h("colgroup", {}, ["c-topic", "c-rise", "c-vol", "c-trend", "c-src", "c-for", "c-stat"].map((c) => h("col", { class: c }))),
      h("thead", {}, [h("tr", {}, [
        th("Topic", "rank", "One row per topic, ordered by relevance: volume and rise together. Click a row for the trend breakdown."),
        th("Rise (" + state.window + ")", "rise", "The window's value against the channel's own median of the 14 days before; the largest among the rising channels that have a baseline (the Volume column shows that channel's latest value in its unit). Capped at +1,000%+ (11x and more); the real multiple is under the figure. Not search volume.", "num", "vs 14-day median"),
        th("Volume", "volume", "Latest daily value of the channel the Rise comes from, in its own unit. Other channels are in the opened row; channels are never added together. Sorting uses the Wikipedia or news value.", "num"),
        th("Trend", null, "Daily series of the channel rising fastest."),
        th("Sources", "sources", "Channels the topic appears on. Dark text means that channel is rising (1.5x its baseline or more)."),
        th("For which channel", null, "Rule-based from the source mix. See How the channel label is chosen."),
        th("Status", "recency", "Active, fading (well below its 14-day peak) or ended, and the first day it was listed. Listing day is not the day the trend began.")
      ])]),
      h("tbody", {}, body)]);
  }

  function ruleNote() {
    return h("details", { class: "how" }, [
      h("summary", { text: "How the channel label is chosen" }),
      h("p", { text: "Four fixed rules read the topic's source mix, age and seasonality. A topic can match more than one; the table shows up to two, in this order. No model is involved, and the same data always gives the same label." }),
      h("ul", { class: "rules" }, FOR_CHANNEL.map((r) => h("li", {}, [h("b", { text: r.label + ": " }), r.rule]))),
      h("p", { text: "A channel is rising when the catalog marks it rising: its 24 h or 48 h value is at least " + RISING + "x its own baseline, or Hacker News has 50 or more points. Active topics only. If no rule matches, the label is Watch." })
    ]);
  }
  /* every number that is estimated or derived says so here */
  function numbersNote() {
    const li = (b, t) => h("li", {}, [h("b", { text: b + " " }), t]);
    return h("details", { class: "how" }, [
      h("summary", { text: "How to read the numbers" }),
      h("ul", {}, [
        li("Rise.", "For the window you picked (24 h, 48 h or 7 d): the largest ratio among the rising channels that have a baseline, against that channel's own median of the 14 days before. The channel is named in the Volume column. The figure is capped at \"+1,000%+\" (11 times and more, like Google Trends) and the real multiple (x112 means 112 times) is written under it. A channel with no earlier history (news at 0 articles before) shows \"new\", not a ratio. A fall shows with a minus sign and a down arrow, and the row moves below the rising ones. It is not search volume and there is none anywhere on this page."),
        li("Fading.", "The last day is under half of the topic's 14-day peak. Rise still compares with the weeks before the spike, so a fading topic can show a large Rise."),
        li("Views/day.", "Wikipedia pageviews of the last full day, real readers only (no crawlers), on the wiki where the topic is biggest."),
        li("Articles/day est.", "Estimated: distinct news articles naming the topic in GDELT's 15-minute files, counted from 6 files a day and scaled to a full day."),
        li("Points.", "Hacker News points of matching front-page and recent stories at the time of the build. A snapshot, so it has no baseline and no rise."),
        li("Listed.", "The first day the topic was listed in the catalog. It is not the moment the trend began."),
        li("Seasonal, new.", "Computed from monthly Wikipedia pageviews since 2015 for the best 150 topics only; the others stay unknown."),
        li("Windows.", "The catalog is built once a day, so 24 h means the last full day. There is no 4 h view because no source is hourly.")
      ])
    ]);
  }

  function renderTrending() {
    const all = curTopics();
    const rows = sortTopics(applyFilters(all, filterState(), nowMs), state.sort, state.dir, state.window);
    const frag = [];
    const nSteady = rows.filter((t) => notRising(t, state.window)).length;
    const countText = rows.length + " of " + all.filter((t) => topicWindow(t, state.window)).length + " topics in the past " + state.window + (regionList() ? " (" + regionMeta().label + ")" : state.region !== "all" ? " in " + regionName(state.region) + " plus worldwide" : "") +
      ": " + (rows.length - nSteady) + " rising, " + nSteady + " steady, top or below baseline";
    frag.push(h("div", { class: "mline" }, [h("p", { class: "count", role: "status", text: countText }), h("p", { class: "unitnote", text: app.getAttribute("data-unit-note") || "Every volume has its own unit; none is search volume." })]));
    if (state.cat !== "all") {
      const cv = coverageOf(state.cat), cl = (data.meta.categories || []).filter((c) => c.id === state.cat)[0];
      if (cv.limited) frag.push(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data for this category. " }), (cl ? cl.label : state.cat) + " has " + cv.total + " row" + (cv.total === 1 ? "" : "s") + " here (" + cv.rising + " rising, " + cv.steady + " steady / top), fewer than " + MIN_ROWS + "."]));
    }
    const rm = regionMeta();
    if (rm && rm.limited) frag.push(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data for this region. " }), (rm.limited_note ? rm.limited_note.replace(/\s*\.?\s*$/, ".") + " " : ""), "Sources with data here: " + ((rm.sources || Array.from(channelsSeen(all))).map((k) => chName(k)).join(", ") || "none") + "."]));
    if (!rows.length) {
      frag.push(emptyState("No topics match these filters.", "Try a longer window or turn off Active only, Multi-source only or the category.",
        h("button", { type: "button", class: "btn", text: "Reset filters", onclick: () => setState({ region: "all", cat: "all", type: "all", src: "all", rising: false, active: false, multi: false, q: "", open: "" }) })));
    } else {
      frag.push(h("div", { class: "tablewrap" }, [trendingTable(rows)]));
    }
    frag.push(h("div", { class: "notes" }, [ruleNote(), numbersNote()]));
    return frag;
  }

  /* ---------- Upcoming ---------- */

  /* an event with a regions list (holidays, Federal Register) belongs only to those regions; one without (Wikidata) belongs to every region */
  function inRegion(e) {
    if (state.region === "all") return true;
    if (regionList()) {
      const r = regionId(state.region);
      return r === regionList()[0].id || !e.regions || e.regions.indexOf(r) >= 0;
    }
    return e.region === state.region || e.region === "global" || (e.regions || []).indexOf(state.region) >= 0;
  }
  function regionText(e) {
    if (e.regions && e.regions.length > 1) return e.regions.slice(0, 3).join(", ") + (e.regions.length > 3 ? " +" + (e.regions.length - 3) : "");
    return regionName(e.region);
  }
  function sourceLabel(id) {
    const m = (data.meta.sources || []).filter((x) => x.id === id)[0];
    return m ? m.label : id;
  }
  /* "Left" for an event: days to its publish-by date; inside 7 days it is flagged in red and in weight, not by colour alone */
  function leftTd(left, urgent) {
    return h("td", { class: "num left" + (urgent ? " urgent" : left < 0 ? " dim" : ""), "data-label": "Left", text: left < 0 ? "window closed" : left === 0 ? "today" : left + (left === 1 ? " day" : " days") });
  }

  function renderUpcoming() {
    const today = utcDay(new Date().toISOString());   // countdowns use the real date; ages above use the data's snapshot time
    const q = state.q.trim().toLowerCase();
    let rows = data.events.filter((e) => inRegion(e) &&
      (state.cat === "all" || e.category === state.cat) && (state.kind === "all" || e.kind === state.kind) && (!q || e.title.toLowerCase().indexOf(q) >= 0) &&
      (!state.openOnly || utcDay(e.publish_by) >= today));
    rows = rows.filter((e) => utcDay(e.date) >= today).sort((a, b) => utcDay(a.date) - utcDay(b.date) || a.title.localeCompare(b.title));
    const out = [h("div", { class: "mline" }, [h("p", { class: "count", role: "status", text: rows.length + " dated events. Publish by is the event date minus a default lead time per kind of event (not measured for any site)." })])];
    if (!rows.length) {
      out.push(emptyState("No upcoming events match.", "Clear the category or search, or turn off Publish window open.",
        h("button", { type: "button", class: "btn", text: "Reset filters", onclick: () => setState({ region: "all", cat: "all", kind: "all", q: "", openOnly: true, open: "" }) })));
      return out;
    }
    const body = rows.map((e) => {
      const left = Math.round((utcDay(e.publish_by) - today) / DAY);
      const urgent = left >= 0 && left <= URGENT_DAYS;
      return h("tr", { class: "row up" }, [
        h("td", { class: "topic", "data-label": "Event" }, [h("span", { class: "tt", title: e.title, text: e.title }), h("span", { class: "cat", text: (e.category_label || e.category) + " \u00B7 " + e.kind + " \u00B7 " + regionText(e) })]),
        h("td", { class: "date", "data-label": "Date", text: dateLabel(e.date) }),
        h("td", { class: "date", "data-label": "Publish by", title: e.lag_days ? "Event date minus " + e.lag_days + " days, the default for this kind of event" : false, text: dateLabel(e.publish_by) }),
        leftTd(left, urgent),
        h("td", { class: "evsrc", "data-label": "Source" }, [h("a", { href: e.source_url, rel: "noopener noreferrer", title: sourceLabel(e.source), text: sourceLabel(e.source) }), h("span", { class: "lic", title: e.license || false, text: e.license || "licence unknown" })])
      ]);
    });
    out.push(h("div", { class: "tablewrap" }, [h("table", { class: "trend upcoming" }, [
      h("colgroup", {}, ["c-ev", "c-date", "c-date", "c-left", "c-esrc"].map((c) => h("col", { class: c }))),
      h("thead", {}, [h("tr", {}, [th("Event"), th("Date"), th("Publish by", null, "Event date minus the usual lead time for this kind of event"), th("Left", null, "Days until the publish-by date", "num"), th("Source and licence")])]),
      h("tbody", {}, body)])]));
    return out;
  }

  /* ---------- GitHub rank table ---------- */

  const GH_WORD = { daily: "today", weekly: "this week", monthly: "this month" };
  function renderGithub() {
    const gh = data.gh, win = state.gwin, rows = ghRows(gh, state);
    const out = [h("div", { class: "mline" }, [h("p", { class: "count", role: "status", text: rows.length + " repositories gained stars " + GH_WORD[win] + (state.lang !== "all" ? " (" + state.lang + ")" : "") + ". A repository needs " + ((gh.minimum_stars || {})[win] || "?") + " stars in the window to be listed." })])];
    if (rows.length < MIN_ROWS) out.push(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data. " }), "Only " + rows.length + " repositor" + (rows.length === 1 ? "y" : "ies") + " pass the star minimum for this window. Try a longer window."]));
    if (!rows.length) { out.push(emptyState("No repositories match.", "Try another window or language.", h("button", { type: "button", class: "btn", text: "Reset filters", onclick: () => setState({ lang: "all", q: "", open: "" }) }))); return out; }
    const body = [];
    rows.forEach((r, i) => {
      const open = state.open === r.repo;
      const toggleRow = () => setState({ open: open ? "" : r.repo });
      const st = (r.stars || {})[win], fk = (r.forks || {})[win];
      body.push(h("tr", { class: "row up gh" + (open ? " open" : ""), "data-id": r.repo, onclick: (e) => { if (!e.target.closest("a,button,summary")) toggleRow(); } }, [
        h("td", { class: "ghrank", "data-label": "Rank", text: String(i + 1) }),
        h("td", { class: "topic ghrepo", "data-label": "Repository" }, [
          h("button", { type: "button", class: "rowbtn", title: r.repo, "data-fk": "row-" + r.repo, "aria-expanded": String(open), onclick: toggleRow }, [h("span", { class: "tt", "data-rank": String(i + 1), text: r.repo })]),
          h("span", { class: "cat", title: r.description || false, text: r.description || "No description." })]),
        h("td", { class: "ghlang", "data-label": "Language", text: r.language || "n/a" }),
        riseTd({ label: "Stars " + GH_WORD[win], tier: 2, arrow: "up", text: "+" + (typeof st === "number" ? st.toLocaleString("en-US") : "n/a") }),
        h("td", { class: "num", "data-label": "Total stars", text: compact(r.total_stars) }),
        h("td", { class: "num", "data-label": "Forks " + GH_WORD[win], text: typeof fk === "number" ? "+" + fk.toLocaleString("en-US") : "n/a" }),
        h("td", { class: "sparkc", "data-label": "Trend" }, [r.series && r.series.length > 1 ? spark(r.series, sparkLabel(r.repo, "stars/day", r.series)) : h("span", { class: "dim", text: "no series" })])
      ]));
      if (open) body.push(h("tr", { class: "detail" }, [h("td", { colspan: "7" }, [ghDetail(r)])]));
    });
    out.push(h("div", { class: "tablewrap" }, [h("table", { class: "trend github" }, [
      h("colgroup", {}, ["c-rank", "c-repo", "c-lang", "c-rise", "c-vol", "c-vol", "c-trend"].map((c) => h("col", { class: c }))),
      h("thead", {}, [h("tr", {}, [th("#", null, "Rank in this window"), th("Repository"), th("Language"),
        th("Stars " + GH_WORD[win], null, "Stars gained in the window: the difference between our daily snapshots of the star count from the GitHub API", "num"),
        th("Total stars", null, "All-time stars from the GitHub API", "num"), th("Forks " + GH_WORD[win], null, "Public fork events in the window", "num"), th("Last 14 days", null, "Stars per day")])]),
      h("tbody", {}, body)])]));
    out.push(h("p", { class: "dim small", text: gh.note || "" }));
    return out;
  }
  function ghDetail(r) {
    const c = (o, k) => (o && typeof o[k] === "number" ? o[k].toLocaleString("en-US") : "n/a");
    const rows = GH_WINDOWS.map((w) => h("tr", {}, [h("th", { scope: "row", text: w[1] }), h("td", { class: "num", "data-label": "Stars", text: c(r.stars, w[0]) }), h("td", { class: "num", "data-label": "Stars per day", text: typeof (r.stars_per_day || {})[w[0]] === "number" ? r.stars_per_day[w[0]].toFixed(1) : "n/a" }), h("td", { class: "num", "data-label": "Forks", text: c(r.forks, w[0]) })]));
    return h("div", { class: "detail-body" }, [
      h("div", { class: "pcol" }, [
        h("h3", { text: "Repository breakdown" }),
        r.description ? h("p", { class: "dim", text: r.description }) : null,
        h("h4", { text: "Topics" }),
        (r.topics || []).length ? tags(r.topics.map((x) => ({ text: x }))) : h("p", { class: "dim", text: "The repository lists no topics." }),
        h("h4", { text: "Source and licence" }),
        h("p", {}, [h("a", { href: r.source_url, rel: "noopener noreferrer", text: r.repo + " on GitHub" }), " \u00B7 ", h("span", { class: "lic", text: r.license || "licence unknown" })])]),
      h("div", { class: "pcol" }, [
        h("h4", { text: "Stars and forks in each window" }),
        h("table", { class: "mini" }, [h("thead", {}, [h("tr", {}, ["Window", "Stars", "Stars per day", "Forks"].map((x, i) => h("th", { scope: "col", class: i ? "num" : "", text: x })))]), h("tbody", {}, rows)]),
        h("p", { class: "dim small", text: "Baseline " + (typeof r.baseline === "number" ? r.baseline.toFixed(1) + " stars/day (median of the earlier days)" : "n/a") + (r.new_repo ? " \u00B7 new repository" : "") + (r.created_at ? " \u00B7 created " + r.created_at : "") + " \u00B7 " + (r.total_stars || 0).toLocaleString("en-US") + " stars" + (typeof r.age_days === "number" ? " in " + r.age_days.toLocaleString("en-US") + " days" : "") + " and " + (r.total_forks || 0).toLocaleString("en-US") + " forks in total" })])
    ]);
  }

  /* ---------- Your niche (docs/personalization.md) ----------
     The profile page and the "For your niche" views live in niche.js, loaded with import() the first time one is opened. Nothing about
     a profile is read here except its name (for the bar); the profile itself stays in localStorage and is never put in the URL hash. */
  const NICHE_KEY = "nicheclock.profiles.v1";
  function nicheAvailable() { return !!(data && data.meta && data.meta.match); }
  function profileName() {
    try {
      const j = JSON.parse(localStorage.getItem(NICHE_KEY) || "null");
      const p = j && (j.profiles || []).filter((x) => x.id === j.active)[0];
      return p && p.name ? String(p.name) : "";
    } catch (e) { return ""; }
  }
  function updateBar() {
    const b = document.getElementById("nichebtn");
    if (!b) return;
    const name = profileName();
    b.textContent = name ? "Your niche: " + name : "Your niche";
    if (state.tab === "niche") b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  let nicheMod = null;
  function nicheCtx() {
    return { state, setState, data, base, storage: localStorage, showControl, profileChanged: updateBar,
      u: { h, spark, pct, capped, factor, compact, dateLabel, utcDay, th, DAY, icon, C, menuEl, filterBar, emptyState, riseTd, tierOf, statusCell, tags, leftTd } };
  }
  function nicheHost() {
    const host = h("div", { class: "niche-host", id: "niche-host" });
    const go = () => nicheMod.view(nicheCtx(), host);
    if (nicheMod) go();
    else {
      host.appendChild(h("p", { class: "state", role: "status", text: "Loading\u2026" }));
      import(app.getAttribute("data-niche") || "/assets/niche.js").then((m) => { nicheMod = m; if (host.isConnected) go(); })
        .catch((e) => {
          host.textContent = "";
          host.appendChild(emptyState("The niche view did not load.", String(e && e.message || e),
            h("button", { type: "button", class: "btn", text: "Reload the page", onclick: () => location.reload() })));
        });
    }
    return host;
  }
  const nicheMode = () => state.tab === "niche" || (state.fit && nicheAvailable() && (state.tab === "trending" || state.tab === "upcoming"));

  /* ---------- page ---------- */

  function tabs() {
    const n = (k, c) => h("button", { type: "button", role: "tab", class: "tab", "data-fk": "tab-" + k, "aria-selected": String(state.tab === k), onclick: () => setState({ tab: k, open: "" }, false) },
      [k === "trending" ? "Trending now" : k === "github" ? "GitHub rank" : "Upcoming", h("span", { class: "n", text: String(c) })]);
    return h("div", { class: "tabs", role: "tablist", "aria-label": "Views" }, [n("trending", curTopics().filter((t) => topicWindow(t, state.window)).length), n("upcoming", data.events.length)].concat(data.gh ? [n("github", (data.gh.repos || []).length)] : []));
  }
  function stamp() {
    const g = data.meta.generated_at || "";
    if (state.tab === "github") return "GitHub data for " + (data.gh.date || "n/a") + " (the last full UTC day), built " + (data.gh.generated_at || "").replace("T", " ").replace("Z", " UTC") + ". Updated once a day.";
    return "Data for " + (data.meta.date || g.slice(0, 10)) + " (the last full day), built " + g.slice(0, 10) + " " + g.slice(11, 16) + " UTC. Updated once a day.";
  }
  function foot() {
    const el = document.getElementById("foot-sources");
    if (!el || !data) return;
    el.textContent = "";
    const all = data.meta.sources || [];
    const used = all.filter((x) => !x.status || x.status === "used");
    const rest = all.filter((x) => x.status && x.status !== "used");
    el.appendChild(h("p", {}, ["Sources: "].concat(used.map((x, i) => h("span", {}, [i ? "; " : "", h("a", { href: x.url, rel: "noopener noreferrer", text: x.label }), x.license ? " (" + x.license + ")" : "", x.ok === false ? ". Unavailable in the last build, so its channel is missing" : ""])))));
    if (rest.length) {
      el.appendChild(h("details", { class: "notused" }, [h("summary", { text: "Sources we do not use (" + rest.length + ")" }),
        h("ul", {}, rest.map((x) => h("li", {}, [h("b", { text: x.label + ": " }), (x.status === "planned" ? "planned. " : "not used. ") + (x.detail || x.terms_quote || "")])))]));
    }
  }

  const ytFetch = (f) => fetch(base + f, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  let pendingFk = null;
  /* a region's topics are fetched the first time it is picked, then kept */
  function loadRegion(id) {
    const ae = document.activeElement;
    pendingFk = ae && ae.getAttribute ? ae.getAttribute("data-fk") : null;
    app.appendChild(h("p", { class: "state", role: "status", text: "Loading " + regionMeta().label + "\u2026" }));
    fetch(base + regionFile(id), { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(regionFile(id) + " returned " + r.status); return r.json(); })
      .then((j) => Promise.all([j, ytFetch(id === "global" ? "youtube.json" : "youtube-" + id + ".json")]))
      .then((r) => { data.byRegion[id] = r[0].topics || []; data.yt[id] = r[1]; mergeYouTube(data.byRegion[id], r[1]); render(); }).catch((e) => fail(String(e && e.message || e)));
  }

  function render() {
    if (closeMenu) closeMenu();
    if (state.tab === "github" && !data.gh) state = Object.assign({}, state, { tab: "trending" });
    if (state.tab === "niche" && !nicheAvailable()) state = Object.assign({}, state, { tab: "trending" });
    if (state.tab !== "github" && !nicheMode() && regionList() && !data.byRegion[regionId(state.region)]) { loadRegion(regionId(state.region)); return; }
    nowMs = Date.parse(data.meta.generated_at) || Date.now();
    const kids = [];
    if (data.meta.sample) kids.push(h("p", { class: "sample", role: "note" }, [h("b", { text: "Sample data. " }), "The topics are real subjects, but every number is invented to test this page."]));
    if (state.tab === "niche") kids.push(nicheHost());
    else if (nicheMode()) kids.push(tabs(), nicheHost());
    else {
      kids.push(tabs(), filterBarFor());
      kids.push.apply(kids, state.tab === "trending" ? renderTrending() : state.tab === "github" ? renderGithub() : renderUpcoming());
      kids.push(h("p", { class: "dim stamp", text: stamp() }));
    }
    const y = window.scrollY, ae = document.activeElement;
    const fk = (ae && ae.getAttribute && ae.getAttribute("data-fk")) || pendingFk;
    pendingFk = null;
    const caret = fk === "q" ? ae.selectionStart : null;
    app.textContent = "";
    kids.forEach((k) => app.appendChild(k));
    if (fk) {
      const back = Array.prototype.find.call(app.querySelectorAll("[data-fk]"), (e) => e.getAttribute("data-fk") === fk);
      if (back) { back.focus({ preventScroll: true }); if (caret !== null) back.setSelectionRange(caret, caret); }
    }
    window.scrollTo(0, y);
    document.title = state.tab === "trending" ? pageTitle : (state.tab === "github" ? "GitHub rank" : state.tab === "niche" ? "Your niche" : "Upcoming") + " | nicheclock";
    document.body.classList.toggle("niche-page", state.tab === "niche");
    updateBar();
    foot();
  }

  function fail(msg) {
    app.textContent = "";
    app.appendChild(emptyState("The data did not load.", msg + " " + base, h("button", { type: "button", class: "btn", text: "Try again", onclick: load })));
  }
  function load() {
    if (!app.querySelector("#prerender")) {      // the served page carries a summary table: keep it on screen until the data arrives, then replace it
      app.textContent = "";
      app.appendChild(h("p", { class: "state", text: "Loading data\u2026" }));
    }
    const get = (f) => fetch(base + f, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(f + " returned " + r.status); return r.json(); });
    const optional = (f) => get(f).catch(() => null);      // github-rank.json: absent in older builds, the tab is then hidden
    Promise.all([get("meta.json"), get("trending.json"), get("upcoming.json"), optional("github-rank.json"), ytFetch("youtube.json")]).then((r) => {
      data = { meta: r[0], topics: r[1].topics || [], events: r[2].events || [], byRegion: { global: r[1].topics || [] }, yt: { global: r[4] }, gh: r[3] && Array.isArray(r[3].repos) ? r[3] : null };
      mergeYouTube(data.topics, r[4]);
      if (r[1].sample || r[0].sample) data.meta.sample = true;
      state = parseHash(location.hash);       // a click on "Your niche" while the files were still loading changed the hash; its hashchange had nothing to render
      render();
    }).catch((e) => fail(String(e && e.message || e)));
  }

  /* the intro video: a link in the page opens it in a modal on click; nothing is fetched and nothing plays until the visitor presses play */
  function introVideo() {
    const a = document.getElementById("introbtn");
    if (!a) return;
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const v = h("video", { controls: true, preload: "none", playsinline: true, poster: "/video/nicheclock-intro-poster.jpg", width: "1920", height: "1080" }, [
        h("source", { src: "/video/nicheclock-intro.mp4", type: "video/mp4" }),
        h("track", { kind: "captions", src: "/video/nicheclock-intro.vtt", srclang: "en", label: "English" })]);
      const dlg = h("dialog", { class: "modal", "aria-label": "Intro video" }, [h("div", { class: "modal-in" }, [
        h("div", { class: "modal-head" }, [h("h2", { text: "Intro, 74 seconds, no sound" }), h("button", { type: "button", class: "ibtn", "aria-label": "Close the video", onclick: () => dlg.close() }, [icon("close")])]),
        v, h("p", { class: "dim small", text: "The words are on screen and in a caption track. The tables in it are this site, captured on 2 Nov 2026." })])]);
      dlg.addEventListener("close", () => { v.pause(); dlg.remove(); a.focus(); });
      dlg.addEventListener("click", (ev) => { if (ev.target === dlg) dlg.close(); });
      document.body.appendChild(dlg);
      dlg.showModal();
    });
  }

  window.addEventListener("hashchange", () => { state = parseHash(location.hash); if (data) render(); });
  phone.addEventListener("change", () => { if (data) render(); });
  introVideo();
  load();
})();

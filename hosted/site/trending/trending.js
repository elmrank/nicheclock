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
  const WINDOW_OFF = { "4h": "No hourly data: the catalog is built once a day from daily pageviews and sampled news files." };
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

  let nowMs = Date.now();

  function setState(patch, replace) {
    state = Object.assign({}, state, patch);
    const url = buildHash(state);
    if (url !== location.hash) history[replace === false ? "pushState" : "replaceState"](null, "", url);
    render();
  }

  function select(label, value, options, onchange) {
    const s = h("select", { "aria-label": label, "data-fk": "sel-" + label, onchange: (e) => onchange(e.target.value) },
      options.map((o) => h("option", { value: o[0], text: o[1], selected: o[0] === value })));
    return h("label", { class: "ctl" }, [h("span", { class: "lbl", text: label }), s]);
  }
  function toggle(label, on, onchange) {
    return h("label", { class: "ctl chk" }, [h("input", { type: "checkbox", "data-fk": "chk-" + label, checked: on, onchange: (e) => onchange(e.target.checked) }), h("span", { text: label })]);
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

  /* source filter: topics rising on one source. A source with no data for this region is listed as "no data" and cannot be picked */
  function sourceSelect() {
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
    const note = { no_data: " (no data here)", pending_key: " (needs a key)", disabled: " (off)", limited: " (limited)" };
    const opts = [h("option", { value: "all", text: "All sources", selected: state.src === "all" })].concat(keys.map((k) => {
      const st = status(k), off = st.status === "no_data" || st.status === "pending_key" || st.status === "disabled";
      return h("option", { value: k, disabled: off, title: st.detail || false, text: label(k) + (!off ? " (" + curTopics().filter((t) => onSource(t, k)).length + ")" : "") + (note[st.status] || ""), selected: state.src === k });
    }));
    const sel = h("select", { "aria-label": "Source", "data-fk": "sel-Source", title: "Show topics rising on one source", onchange: (e) => setState({ src: e.target.value, open: "" }) }, opts);
    return h("label", { class: "ctl" }, [h("span", { class: "lbl", text: "Source" }), sel]);
  }

  function ghFilterBar() {
    const gh = data.gh;
    const langs = ghLanguages(gh, state.gwin);
    const kids = [
      h("div", { class: "ctl seg3", role: "group", "aria-label": "Time window" }, [h("span", { class: "lbl", text: "Window" })].concat(
        GH_WINDOWS.map((w) => h("button", { type: "button", class: "segbtn", "data-fk": "gseg-" + w[0], "aria-pressed": String(state.gwin === w[0]), text: w[1], onclick: () => setState({ gwin: w[0], lang: "all", open: "" }) })))),
      select("Language", state.lang, [["all", "All languages (" + langs.reduce((n, l) => n + l[1], 0) + ")"]].concat(langs.map((l) => [l[0], l[0] + " (" + l[1] + ")"])), (v) => setState({ lang: v })),
      select("Sort", state.gsort, [["stars", "Stars in window"], ["forks", "Forks in window"], ["total", "Total stars"]], (v) => setState({ gsort: v }))
    ];
    const search = h("input", { type: "search", class: "search", placeholder: "Search repositories", "data-fk": "q", "aria-label": "Search repositories", value: state.q, autocomplete: "off",
      oninput: (e) => { clearTimeout(search._t); search._t = setTimeout(() => setState({ q: e.target.value }), 160); } });
    kids.push(h("label", { class: "ctl grow" }, [h("span", { class: "lbl", text: "Search" }), search]));
    return h("div", { class: "filters", role: "search" }, kids);
  }

  function filterBar() {
    if (state.tab === "github") return ghFilterBar();
    const trending = state.tab === "trending";
    const rl = regionList();
    const regions = rl ? rl.map((x) => x.id) : ["all"].concat(trending ? topicRegions() : eventRegions());
    const regionLabel = (r) => {
      if (r === "all") return "All regions";
      if (!rl) return regionName(r);
      const m = rl.filter((x) => x.id === r)[0];
      return m.label + (m.native_label && m.native_label !== m.label ? " \u00B7 " + m.native_label : "");
    };
    const counts = categoryCounts(curTopics(), filterState(), nowMs);
    const total = Object.keys(counts).reduce((n, k) => n + counts[k], 0);
    const cats = [["all", "All categories" + (trending ? " (" + total + ")" : "")]].concat(
      (data.meta.categories || []).map((c) => [c.id, c.label + (trending ? " (" + (counts[c.id] || 0) + (coverageOf(c.id).limited ? ", limited data" : "") + ")" : "")]));
    const regionSel = select("Region", rl ? regionId(state.region) : state.region, regions.map((r) => [r, regionLabel(r)]), (v) => setState({ region: v, open: "" }));
    if (trending && !rl && regions.length <= 2) {      // every topic is worldwide today: say so instead of offering a filter that does nothing
      const sel = regionSel.querySelector("select");
      sel.disabled = true; sel.title = "Every topic is worldwide for now: the sources carry no country split.";
      regionSel.title = sel.title;
    }
    const kids = [regionSel];
    if (nicheAvailable()) kids.unshift(showControl());
    if (trending) {
      kids.push(h("div", { class: "ctl seg", role: "group", "aria-label": "Time window" }, [h("span", { class: "lbl", text: "Window" })].concat(
        WINDOWS.map((w) => h("button", { type: "button", class: "segbtn", "data-fk": "seg-" + w, "aria-pressed": String(state.window === w), text: w, disabled: !!WINDOW_OFF[w], title: WINDOW_OFF[w] || false, onclick: () => setState({ window: w, open: "" }) })))));
    }
    kids.push(select("Category", state.cat, cats, (v) => setState({ cat: v, open: "" })));
    if (trending) {
      kids.push(sourceSelect());
      kids.push(select("Type", state.type, [["all", "All topics"], ["seasonal", "Seasonal"], ["new", "New"]], (v) => setState({ type: v, open: "" })));
      kids.push(toggle("Rising only", state.rising, (v) => setState({ rising: v, open: "" })));
      kids.push(toggle("Active only", state.active, (v) => setState({ active: v, open: "" })));
      kids.push(toggle("Multi-source only", state.multi, (v) => setState({ multi: v, open: "" })));
      kids.push(select("Sort", state.sort + ":" + state.dir, [["rank:desc", "Relevance"], ["rise:desc", "Biggest rise"], ["volume:desc", "Highest volume"], ["recency:desc", "Newest first"], ["recency:asc", "Oldest first"], ["sources:desc", "Most channels rising"], ["rise:asc", "Smallest rise"]],
        (v) => { const p = v.split(":"); setState({ sort: p[0], dir: p[1] }); }));
    } else {
      kids.push(select("Kind", state.kind, [["all", "All kinds"]].concat(eventKinds().map((k) => [k, k.charAt(0).toUpperCase() + k.slice(1)])), (v) => setState({ kind: v })));
      kids.push(toggle("Publish window open", state.openOnly, (v) => setState({ openOnly: v })));
    }
    const search = h("input", { type: "search", class: "search", placeholder: "Search topics", "data-fk": "q", "aria-label": "Search topics", value: state.q, autocomplete: "off",
      oninput: (e) => { clearTimeout(search._t); search._t = setTimeout(() => setState({ q: e.target.value, open: "" }), 160); } });
    kids.push(h("label", { class: "ctl grow" }, [h("span", { class: "lbl", text: "Search" }), search]));
    return h("div", { class: "filters", role: "search" }, kids);
  }

  /* ---------- Trending table ---------- */

  /* YouTube is a chart presence: a rank and, when the API gave it, raw views. No rise figure (its terms forbid derived metrics). */
  function isChart(c) { return !!(c && c.chart); }
  function volText(c) { return isChart(c) ? "#" + c.rank : /chart rank/i.test(c.unit || "") ? "#" + c.value : compact(c.value); }
  function volUnit(c) { return isChart(c) ? "on chart" : unitShort(c.unit); }
  function volChips(t) {
    return h("div", { class: "chips" }, channelKeys(t).map((k) => {
      const c = t.channels[k];
      return h("span", { class: "vol", title: isChart(c) ? chName(k) + ": rank #" + c.rank + " on the chart" + (typeof c.value === "number" ? ", " + compact(c.value) + " " + (c.unit || "views") : "") + "; no rise figure" : chName(k) + ": " + compact(c.value) + " " + c.unit + (typeof c.baseline === "number" ? " (baseline " + compact(c.baseline) + ")" : " (a snapshot: no baseline)") }, [
        h("b", { text: chName(k, true) }), " ", h("span", { class: "n", text: volText(c) }), " ", h("span", { class: "u", text: volUnit(c) })]);
    }));
  }
  function srcChips(t) {
    return h("div", { class: "chips" }, channelKeys(t).map((k) => {
      const up = isUp(t, k), c = t.channels[k];
      return h("span", { class: "src" + (up ? " up" : ""), title: chName(k) + (up ? " is rising" : " is present, not rising") + (isChart(c) ? ": a chart place, no rise figure" : c.baseline === 0 ? ": no earlier history, so no ratio" : chWinRatio(c, state.window) === null ? "" : ": " + chRiseText(c, state.window) + " in the " + state.window + " window vs its own 14-day median") },
        [up ? h("span", { class: "tri", "aria-hidden": "true" }) : "", chName(k, true)]);
    }));
  }
  function forChips(t) {
    const hits = forChannel(t, nowMs).filter((r) => r.hit);
    if (!hits.length) return h("span", { class: "dim", text: isSteady(t) ? "Not rising" : t.status === "ended" ? "Ended" : "Watch" });
    return h("div", { class: "chips" }, hits.slice(0, 2).map((r) => h("span", { class: "for", text: r.label })));
  }
  function statusCell(t) {
    const ended = t.status === "ended", fading = !isSteady(t) && !ended && isFading(t);   // an ended row says Ended; fading = past its peak
    return h("div", { class: "status" }, [
      h("span", { class: "st " + (isSteady(t) ? "steady" : ended ? "ended" : fading ? "fading" : "active"), text: isSteady(t) ? "Steady / Top" : ended ? "Ended" : fading ? "Fading" : "Active" }),
      fading ? h("span", { class: "when dim", text: peakText(t) }) : null,
      h("span", { class: "when dim", title: "First day this topic was listed in the catalog. It is not the day the trend began.", text: since(t, nowMs) })]);
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
      [label, sorted ? h("span", { class: "tri" + (state.dir === "asc" ? "" : " down"), "aria-hidden": "true" }) : null, sub ? h("span", { class: "sub", text: sub }) : null]) : h("span", { text: label, title: tip });
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

  function detail(t) {
    const rules = forChannel(t, nowMs);
    const win = topicWindow(t, state.window);
    const country = regionList() && regionId(state.region) !== regionList()[0].id;
    const chRows = channelKeys(t).map((k) => {
      const c = t.channels[k], chart = isChart(c);
      let title = chName(k);
      if (k === "wikipedia" && (c.wiki || c.scope)) title += " (" + [c.wiki, c.scope].filter(Boolean).join(", ") + ")";
      if (k === "news" && country) title += " (places in this country)";
      const latest = chart ? "rank #" + c.rank + (typeof c.value === "number" ? ", " + compact(c.value) + " " + (c.unit || "views") : "") : compact(c.value) + " " + c.unit;
      const rise = chRiseText(c, state.window);
      return h("tr", {}, [
        h("th", { scope: "row", text: title }),
        h("td", { class: "num", "data-label": "Latest", text: latest }),
        h("td", { class: "num", "data-label": "Baseline", text: typeof c.baseline === "number" ? compact(c.baseline) + " " + unitShort(c.unit) : chart ? "none (chart)" : "none (snapshot)" }),
        h("td", { class: "num", "data-label": "Rise", title: c.censored ? "Country view counts are censored for small pages, so the true rise is at least this" : false, text: rise }),
        h("td", {}, [c.series && c.series.length > 1 ? spark(c.series, sparkLabel(chName(k), unitShort(c.unit), c.series)) : "no series"]),
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
      h("h3", { text: "Trend breakdown" }),
      t.description ? h("p", { class: "dim", text: t.description }) : null,
      h("h4", { text: "Related names" }),
      names.length ? h("div", { class: "chips names" }, names.map((n) => h("span", { class: "src bd", text: n }))) : h("p", { class: "dim", text: "The catalog lists no related names for this topic." }),
      h("p", { class: "dim small", text: "Wikidata aliases, titles of the article on other Wikipedias and names counted in the news. These are names, not search queries." }),
      isFading(t) && !isSteady(t) ? h("p", { class: "fade", role: "note" }, [h("b", { text: "Fading. " }), "This topic " + peakText(t) + ". The Rise figure compares the window with the weeks before the spike; it does not mean the topic is still going up."]) : null,
      h("h4", { text: "Channels" }),
      h("p", { class: "dim", text: "Each channel is measured in its own unit and is never added to another. Rise is the " + state.window + " window against that channel's own median of the 14 days before. The Rise on the row is the largest figure here among the rising channels that have a baseline." }),
      h("table", { class: "mini" }, [h("thead", {}, [h("tr", {}, ["Channel", "Latest", "Baseline", "Rise (" + state.window + ")", "Last " + ((t.channels[channelKeys(t)[0]] || {}).series || []).length + " days", ""].map((x, i) => h("th", { scope: "col", class: i > 0 && i < 4 ? "num" : "", text: x })))]), h("tbody", {}, chRows)]),
      h("h4", { text: "For which channel" }),
      h("ul", { class: "rules" }, rules.map((r) => h("li", { class: r.hit ? "hit" : "" }, [h("b", { text: r.label + (r.hit ? ": yes." : ": no.") }), " ", r.rule]))),
      h("h4", { text: "Evidence and licence" }),
      ev.length ? h("ul", { class: "evl" }, ev) : h("p", { class: "dim", text: "No evidence entries in the data for this topic." }),
      h("p", { class: "dim meta", text: [t.category_label || t.category, via, t.region === "global" ? "worldwide" : t.region, win ? "rise in " + state.window + " window: " + riseText(riseInfo(t, state.window)) : "", t.seasonal && t.seasonal !== "unknown" ? t.seasonal : "", t.qid ? "Wikidata " + t.qid : "no Wikidata id"].filter(Boolean).join(" \u00B7 ") })
    ]);
  }

  function riseCell(t) {
    const ri = riseInfo(t, state.window);
    const base = "num rise" + (isFading(t) ? " steady" : ""), lbl = "Rise " + state.window;
    if (isSteady(t)) return h("td", { class: base + " steady", "data-label": lbl, title: "Steady / Top: not rising. Listed because it is among the top topics of its category." }, [h("span", { class: "tri flat", "aria-hidden": "true" }), "Steady"]);
    if (ri.kind === "down") return h("td", { class: base + " steady", "data-label": lbl, title: "Below its own 14-day median in the " + state.window + " window, so not rising here. Another window may differ." }, [h("span", { class: "tri down fall", "aria-hidden": "true" }), pct(ri.ratio), h("span", { class: "via", text: chName(ri.channel, true) })]);
    if (ri.kind === "new") return h("td", { class: base, "data-label": lbl, title: "Rising, but the rising channel has no earlier history to compare with (zero articles before, or a Hacker News snapshot), so there is no ratio." }, [h("span", { class: "tri", "aria-hidden": "true" }), "new"]);
    if (ri.kind === "none") return h("td", { class: base + " steady", "data-label": lbl, title: "No channel with a baseline in this window." }, ["n/a"]);
    const cap = capped(ri.ratio);
    return h("td", { class: base, "data-label": lbl, title: chName(ri.channel) + " in the " + state.window + " window against its own median of the 14 days before" + (cap ? ": " + factor(ri.ratio) + " (the figure is capped at +1,000%+)" : "") + (t.rise === "mild" ? ". Mild rise: at least 1.2x" : "") }, [h("span", { class: "tri", "aria-hidden": "true" }), pct(ri.ratio), h("span", { class: "via", text: (cap ? factor(ri.ratio) + " \u00B7 " : "") + chName(ri.channel, true) })]);
  }

  function trendingTable(rows) {
    const body = [];
    rows.forEach((t) => {
      const open = state.open === t.id;
      const win = topicWindow(t, state.window);
      const toggleRow = () => setState({ open: open ? "" : t.id });
      const tr = h("tr", { class: "row" + (open ? " open" : ""), "data-id": t.id, onclick: (e) => { if (!e.target.closest("a,button,summary")) toggleRow(); } }, [
        h("td", { class: "topic" }, [
          h("button", { type: "button", class: "rowbtn", title: t.label, "data-fk": "row-" + t.id, "aria-expanded": String(open), "aria-controls": "d-" + t.id, onclick: toggleRow }, [h("span", { class: "tt", text: t.label })]),
          h("span", { class: "cat dim", text: (t.category_label || t.category) + (t.seasonal === "seasonal" ? " \u00B7 seasonal" : t.seasonal === "new" ? " \u00B7 new" : "") + (t.rise === "mild" ? " \u00B7 mild rise" : "") })]),
        riseCell(t),
        h("td", { class: "vols", "data-label": "Volume" }, [volChips(t)]),
        h("td", { class: "sparkc", "data-label": "Trend" }, [sparkCell(t)]),
        h("td", { class: "srcs", "data-label": "Sources" }, [srcChips(t)]),
        h("td", { class: "fors", "data-label": "For" }, [forChips(t)]),
        h("td", { class: "stat", "data-label": "Status" }, [statusCell(t)])
      ]);
      body.push(tr);
      if (open) body.push(h("tr", { class: "detail", id: "d-" + t.id }, [h("td", { colspan: "7" }, [detail(t)])]));
    });
    return h("table", { class: "trend" }, [
      h("thead", {}, [h("tr", {}, [
        th("Topic", "rank", "One row per topic, ordered by relevance: volume and rise together. Click a row for the trend breakdown."),
        th("Rise (" + state.window + ")", "rise", "The window's value against the channel's own median of the 14 days before; the largest among the rising channels that have a baseline (the channel is named under the figure). Capped at +1,000%+ (11x and more); the real multiple is under the figure. Not search volume.", "num", "vs 14-day median"),
        th("Volume by channel", "volume", "Latest daily value per channel, each in its own unit. Channels are never added together. Sorting uses the Wikipedia or news value."),
        th("Trend", null, "Daily series of the channel rising fastest."),
        th("Sources", "sources", "Channels the topic appears on. A filled mark means that channel is rising (1.5x its baseline or more)."),
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
        li("Rise.", "For the window you picked (24 h, 48 h or 7 d): the largest ratio among the rising channels that have a baseline, against that channel's own median of the 14 days before. The channel is named under the figure. The figure is capped at \"+1,000%+\" (11 times and more, like Google Trends) and the real multiple (x112 means 112 times) is written under it. A channel with no earlier history (news at 0 articles before) shows \"new\", not a ratio. A fall shows with a minus sign and a down arrow, and the row moves below the rising ones. It is not search volume and there is none anywhere on this page."),
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
    frag.push(h("p", { class: "count", role: "status", text: rows.length + " of " + all.filter((t) => topicWindow(t, state.window)).length + " topics in the past " + state.window + (regionList() ? " (" + regionMeta().label + ")" : state.region !== "all" ? " in " + regionName(state.region) + " plus worldwide" : "") }));
    const nSteady = rows.filter((t) => notRising(t, state.window)).length;
    frag[0].textContent += ": " + (rows.length - nSteady) + " rising, " + nSteady + " steady, top or below baseline";
    frag.push(h("p", { class: "dim small unitnote", text: app.getAttribute("data-unit-note") || "Every volume has its own unit; none is search volume." }));
    if (state.cat !== "all") {
      const cv = coverageOf(state.cat), cl = (data.meta.categories || []).filter((c) => c.id === state.cat)[0];
      if (cv.limited) frag.unshift(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data for this category. " }), (cl ? cl.label : state.cat) + " has " + cv.total + " row" + (cv.total === 1 ? "" : "s") + " here (" + cv.rising + " rising, " + cv.steady + " steady / top), fewer than " + MIN_ROWS + "."]));
    }
    const rm = regionMeta();
    if (rm && rm.limited) frag.unshift(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data for this region. " }), (rm.limited_note ? rm.limited_note.replace(/\s*\.?\s*$/, ".") + " " : ""), "Sources with data here: " + ((rm.sources || Array.from(channelsSeen(all))).map((k) => chName(k)).join(", ") || "none") + "."]));
    if (!rows.length) {
      frag.push(h("div", { class: "state" }, [h("strong", { text: "No topics match these filters." }), "Try a longer window or turn off Active only, Multi-source only or the category.",
        h("div", {}, [h("button", { type: "button", class: "btn", text: "Reset filters", onclick: () => setState({ region: "all", cat: "all", type: "all", src: "all", rising: false, active: false, multi: false, q: "", open: "" }) })])]));
    } else {
      frag.push(h("div", { class: "tablewrap" }, [trendingTable(rows)]));
    }
    frag.push(ruleNote());
    frag.push(numbersNote());
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

  function renderUpcoming() {
    const today = utcDay(new Date().toISOString());   // countdowns use the real date; ages above use the data's snapshot time
    const q = state.q.trim().toLowerCase();
    let rows = data.events.filter((e) => inRegion(e) &&
      (state.cat === "all" || e.category === state.cat) && (state.kind === "all" || e.kind === state.kind) && (!q || e.title.toLowerCase().indexOf(q) >= 0) &&
      (!state.openOnly || utcDay(e.publish_by) >= today));
    rows = rows.filter((e) => utcDay(e.date) >= today).sort((a, b) => utcDay(a.date) - utcDay(b.date) || a.title.localeCompare(b.title));
    const out = [h("p", { class: "count", role: "status", text: rows.length + " dated events. Publish by is the event date minus a default lead time per kind of event (not measured for any site)." })];
    if (!rows.length) { out.push(h("div", { class: "state" }, [h("strong", { text: "No upcoming events match." }), "Clear the category or search."])); return out; }
    const body = rows.map((e) => {
      const left = Math.round((utcDay(e.publish_by) - today) / DAY);
      const urgent = left >= 0 && left <= URGENT_DAYS;
      return h("tr", { class: "row up" }, [
        h("td", { class: "topic", "data-label": "Event" }, [h("span", { class: "tt", text: e.title }), h("span", { class: "cat dim", text: (e.category_label || e.category) + " \u00B7 " + e.kind + " \u00B7 " + regionText(e) })]),
        h("td", { class: "date", "data-label": "Date", text: dateLabel(e.date) }),
        h("td", { class: "date", "data-label": "Publish by", title: e.lag_days ? "Event date minus " + e.lag_days + " days, the default for this kind of event" : false, text: dateLabel(e.publish_by) }),
        h("td", { class: "num left" + (urgent ? " urgent" : left < 0 ? " dim" : ""), "data-label": "Left", text: left < 0 ? "window closed" : left === 0 ? "today" : left + (left === 1 ? " day" : " days") }),
        h("td", { class: "evsrc", "data-label": "Source" }, [h("a", { href: e.source_url, rel: "noopener noreferrer", text: sourceLabel(e.source) }), " \u00B7 ", h("span", { class: "lic", text: e.license || "licence unknown" })])
      ]);
    });
    out.push(h("div", { class: "tablewrap" }, [h("table", { class: "trend upcoming" }, [
      h("thead", {}, [h("tr", {}, [th("Event"), th("Date"), th("Publish by", null, "Event date minus the usual lead time for this kind of event"), th("Left", null, "Days until the publish-by date", "num"), th("Source and licence")])]),
      h("tbody", {}, body)])]));
    return out;
  }

  /* ---------- GitHub rank table ---------- */

  const GH_WORD = { daily: "today", weekly: "this week", monthly: "this month" };
  function renderGithub() {
    const gh = data.gh, win = state.gwin, rows = ghRows(gh, state);
    const out = [h("p", { class: "count", role: "status", text: rows.length + " repositories gained stars " + GH_WORD[win] + (state.lang !== "all" ? " (" + state.lang + ")" : "") + ". A repository needs " + ((gh.minimum_stars || {})[win] || "?") + " stars in the window to be listed." })];
    if (rows.length < MIN_ROWS) out.unshift(h("p", { class: "limited", role: "note" }, [h("b", { text: "Limited data. " }), "Only " + rows.length + " repositor" + (rows.length === 1 ? "y" : "ies") + " pass the star minimum for this window. Try a longer window."]));
    if (!rows.length) { out.push(h("div", { class: "state" }, [h("strong", { text: "No repositories match." }), "Try another window or language."])); return out; }
    const body = [];
    rows.forEach((r, i) => {
      const open = state.open === r.repo;
      const toggleRow = () => setState({ open: open ? "" : r.repo });
      const st = (r.stars || {})[win], fk = (r.forks || {})[win];
      body.push(h("tr", { class: "row up gh" + (open ? " open" : ""), "data-id": r.repo, onclick: (e) => { if (!e.target.closest("a,button,summary")) toggleRow(); } }, [
        h("td", { class: "ghrank", "data-label": "Rank", text: String(i + 1) }),
        h("td", { class: "topic ghrepo", "data-label": "Repository" }, [
          h("button", { type: "button", class: "rowbtn", title: r.repo, "data-fk": "row-" + r.repo, "aria-expanded": String(open), onclick: toggleRow }, [h("span", { class: "tt", "data-rank": String(i + 1), text: r.repo })]),
          h("span", { class: "cat dim", title: r.description || false, text: r.description || "No description." })]),
        h("td", { class: "ghlang", "data-label": "Language", text: r.language || "n/a" }),
        h("td", { class: "num rise", "data-label": "Stars " + GH_WORD[win] }, [h("span", { class: "tri", "aria-hidden": "true" }), "+" + (typeof st === "number" ? st.toLocaleString("en-US") : "n/a")]),
        h("td", { class: "num", "data-label": "Total stars", text: compact(r.total_stars) }),
        h("td", { class: "num", "data-label": "Forks " + GH_WORD[win], text: typeof fk === "number" ? "+" + fk.toLocaleString("en-US") : "n/a" }),
        h("td", { class: "sparkc", "data-label": "Trend" }, [r.series && r.series.length > 1 ? spark(r.series, sparkLabel(r.repo, "stars/day", r.series)) : h("span", { class: "dim", text: "no series" })])
      ]));
      if (open) body.push(h("tr", { class: "detail" }, [h("td", { colspan: "7" }, [ghDetail(r)])]));
    });
    out.push(h("div", { class: "tablewrap" }, [h("table", { class: "trend github" }, [
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
      h("h3", { text: "Repository breakdown" }),
      r.description ? h("p", { class: "dim", text: r.description }) : null,
      h("h4", { text: "Topics" }),
      (r.topics || []).length ? h("div", { class: "chips names" }, r.topics.map((x) => h("span", { class: "src bd", text: x }))) : h("p", { class: "dim", text: "The repository lists no topics." }),
      h("h4", { text: "Stars and forks in each window" }),
      h("table", { class: "mini" }, [h("thead", {}, [h("tr", {}, ["Window", "Stars", "Stars per day", "Forks"].map((x, i) => h("th", { scope: "col", class: i ? "num" : "", text: x })))]), h("tbody", {}, rows)]),
      h("p", { class: "dim small", text: "Baseline " + (typeof r.baseline === "number" ? r.baseline.toFixed(1) + " stars/day (median of the earlier days)" : "n/a") + (r.new_repo ? " \u00B7 new repository" : "") + (r.created_at ? " \u00B7 created " + r.created_at : "") + " \u00B7 " + (r.total_stars || 0).toLocaleString("en-US") + " stars" + (typeof r.age_days === "number" ? " in " + r.age_days.toLocaleString("en-US") + " days" : "") + " and " + (r.total_forks || 0).toLocaleString("en-US") + " forks in total" }),
      h("h4", { text: "Source and licence" }),
      h("p", {}, [h("a", { href: r.source_url, rel: "noopener noreferrer", text: r.repo + " on GitHub" }), " \u00B7 ", h("span", { class: "lic", text: r.license || "licence unknown" })])
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
  function showControl() {
    return h("div", { class: "ctl seg2", role: "group", "aria-label": "Show" }, [h("span", { class: "lbl", text: "Show" }),
      h("button", { type: "button", class: "segbtn", "data-fk": "show-all", "aria-pressed": String(!state.fit), text: "All topics", onclick: () => setState({ fit: false, open: "" }) }),
      h("button", { type: "button", class: "segbtn", "data-fk": "show-fit", "aria-pressed": String(state.fit), text: "For your niche", onclick: () => (profileName() ? setState({ fit: true, open: "" }) : setState({ tab: "niche", fit: false, open: "" }, false)) })]);
  }
  let nicheMod = null;
  function nicheCtx() {
    return { state, setState, data, base, storage: localStorage, showControl, profileChanged: updateBar,
      u: { h, spark, pct, capped, factor, compact, dateLabel, utcDay, select, toggle, th, DAY } };
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
          host.appendChild(h("div", { class: "state" }, [h("strong", { text: "The niche view did not load." }), String(e && e.message || e) + " ",
            h("div", {}, [h("button", { type: "button", class: "btn", text: "Reload the page", onclick: () => location.reload() })])]));
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
    if (state.tab === "github" && !data.gh) state = Object.assign({}, state, { tab: "trending" });
    if (state.tab === "niche" && !nicheAvailable()) state = Object.assign({}, state, { tab: "trending" });
    if (state.tab !== "github" && !nicheMode() && regionList() && !data.byRegion[regionId(state.region)]) { loadRegion(regionId(state.region)); return; }
    nowMs = Date.parse(data.meta.generated_at) || Date.now();
    const kids = [];
    if (data.meta.sample) kids.push(h("p", { class: "sample", role: "note" }, [h("b", { text: "Sample data. " }), "The topics are real subjects, but every number is invented to test this page."]));
    if (state.tab === "niche") kids.push(nicheHost());
    else if (nicheMode()) kids.push(tabs(), nicheHost());
    else {
      kids.push(tabs(), filterBar());
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
    app.appendChild(h("div", { class: "state" }, [h("strong", { text: "The data did not load." }), msg + " ", h("code", { text: base }),
      h("div", {}, [h("button", { type: "button", class: "btn", text: "Try again", onclick: load })])]));
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
      render();
    }).catch((e) => fail(String(e && e.message || e)));
  }
  window.addEventListener("hashchange", () => { if (data) { state = parseHash(location.hash); render(); } });
  load();
})();

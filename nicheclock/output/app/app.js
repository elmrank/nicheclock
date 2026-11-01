/* nicheclock report: draws niche tabs, Trends, Upcoming and Gaps from one JSON (schema 1).
   The JSON is embedded in the page (<script id="nc-data">, self-hosted report) or fetched from data-src (hosted demo).
   No framework; text goes in as text nodes only. */
(function () {
  "use strict";

  var app = typeof document === "undefined" ? null : document.getElementById("app");
  var TOP = 10;          // trends shown before "Show all"
  var URGENT_DAYS = 7;   // publish-by within this many days is flagged
  var DAY = 86400000;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
  var SOURCE_NAMES = {
    wikimedia_pageviews: "Wikimedia pageviews", wikipedia: "Wikipedia", hn: "Hacker News", gdelt: "GDELT",
    federal_register: "Federal Register", calendar: "Event calendar", ics: "Calendar", rss: "RSS feed",
    google_trends: "Google Trends", github: "GitHub", apple: "Apple charts"
  };
  var KINDS = { event: "Event", deadline: "Deadline", rising: "Rising" };

  var data = null;
  var state = { niche: null, all: false, watch: false };

  /* ---------- what the page decides from the data (kept free of the DOM so it can be tested) ---------- */

  var STRONG = 35;        // trends scoring at least this are the table; the rest wait under "Watching"

  function splitTrends(topics) {
    var sorted = topics.slice().sort(function (a, b) { return b.score - a.score; });
    return {
      strong: sorted.filter(function (t) { return t.score >= STRONG; }),
      watching: sorted.filter(function (t) { return t.score < STRONG; })
    };
  }
  /* The niche to open first: the highest top score, then the most trends at or above STRONG, then the data's own order. */
  function pickNiche(d) {
    var best = null, bestKey = null;
    d.niches.forEach(function (n) {
      var mine = d.topics.filter(function (t) { return t.niche === n.id; });
      var top = mine.reduce(function (m, t) { return Math.max(m, t.score); }, -1);
      var key = [top, splitTrends(mine).strong.length];
      if (!bestKey || key[0] > bestKey[0] || (key[0] === bestKey[0] && key[1] > bestKey[1])) { best = n; bestKey = key; }
    });
    return best;
  }
  /* Upcoming lists dated things only: "rising" entries are trends, and the Trends table already has them. */
  function visibleUpcoming(d, nicheId) {
    return d.upcoming.filter(function (e) { return e.niche === nicheId && (e.kind === "event" || e.kind === "deadline"); });
  }
  if (typeof document === "undefined") {
    module.exports = { STRONG: STRONG, splitTrends: splitTrends, pickNiche: pickNiche, visibleUpcoming: visibleUpcoming };
    return;
  }

  /* ---------- small helpers ---------- */

  function h(tag, props, kids) {
    var e = document.createElement(tag);
    for (var k in props || {}) {
      var v = props[k];
      if (v == null || v === false) continue;
      if (k === "text") e.textContent = v;
      else if (k === "class") e.className = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    (kids || []).forEach(function (c) {
      if (c == null || c === false) return;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return e;
  }
  function link(href, text) {
    return safeUrl(href) ? h("a", { href: href, rel: "noopener noreferrer", text: text }) : document.createTextNode(text);
  }
  function safeUrl(u) { return typeof u === "string" && /^https?:\/\//i.test(u); }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function num(n, digits) {
    return typeof n === "number" ? n.toLocaleString("en-US", { maximumFractionDigits: digits || 0 }) : "";
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function day(iso) {                     // "YYYY-MM-DD" to UTC milliseconds
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : NaN;
  }
  function todayMs() {
    var n = new Date();
    return Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate());
  }
  function fmtDay(iso, precision) {
    var t = day(iso);
    if (isNaN(t)) return "";
    var d = new Date(t);
    var year = d.getUTCFullYear() !== new Date().getUTCFullYear() ? " " + d.getUTCFullYear() : "";
    if (precision === "month") return MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
    return WEEKDAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + year;
  }
  function fmtStamp(iso) {                // "2026-10-31T13:45Z" to "31 Oct 13:45"
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso || "");
    return m ? +m[3] + " " + MONTHS[+m[2] - 1] + " " + m[4] : "";
  }
  function sourceName(id) {
    return SOURCE_NAMES[id] || String(id || "").replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }

  /* time left until publish-by: text plus whether it is urgent */
  function timeLeft(publishBy) {
    var t = day(publishBy);
    if (isNaN(t)) return { text: "–", urgent: false };
    var d = Math.round((t - todayMs()) / DAY);
    if (d < 0) return { text: "Publish now", urgent: true };
    if (d === 0) return { text: "Today", urgent: true };
    return { text: plural(d, "day", "days"), urgent: d <= URGENT_DAYS };
  }

  /* ---------- sparkline: a real column (Wikipedia pageviews a day), plus a larger labelled chart when a row is opened ---------- */

  var SVGNS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    return e;
  }
  function points(values, w, hgt, pad) {
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var span = max - min || 1;
    return values.map(function (v, i) {
      return [pad + (w - 2 * pad) * (values.length === 1 ? 1 : i / (values.length - 1)), hgt - pad - (hgt - 2 * pad) * ((v - min) / span)];
    });
  }
  function pathOf(pts) {
    return pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("");
  }
  function validSeries(s) {
    return s && Array.isArray(s.values) && s.values.length > 1 && s.values.every(function (v) { return typeof v === "number"; });
  }
  function spark(series) {
    var pts = points(series.values, 84, 24, 2);
    var last = pts[pts.length - 1];
    var s = svg("svg", { "class": "spark", viewBox: "0 0 84 24", "aria-hidden": "true" });
    s.appendChild(svg("path", { d: pathOf(pts) }));
    s.appendChild(svg("circle", { cx: last[0], cy: last[1], r: 2.5 }));
    return s;
  }
  function chart(series) {
    var W = Math.max(300, Math.min(640, app.clientWidth - 64)), H = 130, L = 52, R = 60, T = 14, B = 26;
    var vals = series.values, min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var pts = points(vals, W - L - R + 0, H - T - B, 0).map(function (p) { return [p[0] + L, p[1] + T]; });
    var s = svg("svg", { "class": "chart", viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": (series.label || "Daily views") + ": " + num(vals[0]) + " to " + num(vals[vals.length - 1]) + " a day over " + vals.length + " days" });
    [[max, T], [min, H - B]].forEach(function (g) {
      s.appendChild(svg("line", { x1: L, x2: W - R, y1: g[1], y2: g[1] }));
      var t = svg("text", { x: L - 8, y: g[1] + 4, "text-anchor": "end" });
      t.textContent = num(g[0]);
      s.appendChild(t);
    });
    s.appendChild(svg("path", { d: pathOf(pts) }));
    var last = pts[pts.length - 1];
    s.appendChild(svg("circle", { cx: last[0], cy: last[1], r: 4 }));
    var end = svg("text", { x: last[0] + 10, y: last[1] + 4 });
    end.textContent = num(vals[vals.length - 1]);
    s.appendChild(end);
    var start = day(series.start);
    if (!isNaN(start)) {
      [[0, "start"], [vals.length - 1, "end"]].forEach(function (g) {
        var d = new Date(start + g[0] * DAY);
        var t = svg("text", { x: pts[g[0]][0], y: H - 6, "text-anchor": g[1] });
        t.textContent = d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
        s.appendChild(t);
      });
    }
    return s;
  }

  /* ---------- states ---------- */

  function showState(title, lines, retry) {
    clear(app);
    var box = h("div", { "class": "state" }, [h("strong", { text: title })]);
    (lines || []).forEach(function (l) { box.appendChild(h("p", null, l)); });
    if (retry) box.appendChild(h("button", { "class": "btn", type: "button", text: "Try again", onclick: load }));
    app.appendChild(box);
    app.removeAttribute("aria-busy");
  }
  function fail(message) {
    showState("The data did not load", [[message], ["The page loaded but the data file it reads did not."]], app.dataset.src ? true : false);
  }

  /* ---------- tables ---------- */

  function cell(cls, kids, label) {
    return h("td", { "class": cls, "data-label": label }, kids);
  }
  function head(cols) {
    return h("thead", null, [h("tr", null, cols.map(function (c) {
      return h("th", { "class": c[1] || "", scope: "col", text: c[0] });
    }))]);
  }

  /* One row that opens an inline detail row beneath it. */
  function expandable(tr, btn, colspan, build) {
    function toggle() {
      var next = tr.nextSibling;
      if (next && next.classList && next.classList.contains("detail")) {
        next.parentNode.removeChild(next);
        tr.classList.remove("open");
        btn.setAttribute("aria-expanded", "false");
        return;
      }
      var d = h("tr", { "class": "detail" }, [h("td", { colspan: colspan }, [build()])]);
      tr.parentNode.insertBefore(d, tr.nextSibling);
      tr.classList.add("open");
      btn.setAttribute("aria-expanded", "true");
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); toggle(); });
    tr.addEventListener("click", function (e) { if (!e.target.closest("a, button")) toggle(); });
  }

  function evidenceItem(s) {
    var first = [h("strong", { text: s.name || sourceName(s.id) })];
    var p = h("p", null, first);
    var li = h("li", null, [p]);
    var line = [];
    if (s.title) line.push(link(s.url, s.title));
    if (s.at) line.push(" · " + fmtStamp(s.at) + " UTC");
    if (line.length) li.appendChild(h("p", null, line));
    if (s.note) li.appendChild(h("p", { text: s.note }));
    var cite = [];
    if (s.attribution) cite.push(s.attribution);
    if (s.license && !s.attribution) cite.push("License: " + s.license + ".");
    if (cite.length) li.appendChild(h("p", { text: cite.join(" ") }));
    if (safeUrl(s.terms_url) && !s.attribution) li.appendChild(h("p", null, [link(s.terms_url, "Source terms")]));
    return li;
  }

  function trendDetail(t) {
    var box = h("div", { "class": "detail-body" });
    box.appendChild(h("h3", { text: "Evidence" }));
    if (t.sources && t.sources.length) {
      box.appendChild(h("ul", null, t.sources.map(evidenceItem)));
    } else {
      box.appendChild(h("p", null, ["No evidence was recorded for this item. ", safeUrl(t.url) ? link(t.url, "Open its link") : ""]));
    }
    if (validSeries(t.series)) {
      box.appendChild(h("h3", { text: "Views a day, last " + t.series.values.length + " days" }));
      box.appendChild(chart(t.series));
      if (t.series.label) box.appendChild(h("p", null, [link(t.series.url, t.series.label)]));
    }
    var weight = data.llm && typeof data.llm.fit_weight === "number" ? data.llm.fit_weight : 0.6;
    var pct = Math.round(weight * 100);
    if (t.fit != null && t.rule_score != null) {
      box.appendChild(h("h3", { text: "Score" }));
      box.appendChild(h("p", { text: num(t.score, 0) + " = " + (100 - pct) + "% of the rule score (" + num(t.rule_score, 0) + ") + " + pct + "% of the model's fit score (" + num(t.fit, 0) + ")." }));
    } else if (t.rule_score != null) {
      box.appendChild(h("h3", { text: "Score" }));
      box.appendChild(h("p", { text: "Rule score " + num(t.rule_score, 0) + ". The model did not score this item." }));
    }
    if (t.why && t.why !== t.reason) box.appendChild(h("p", { text: "Rules: " + t.why }));
    var meta = [];
    if (t.regions && t.regions.length) meta.push("Seen in " + t.regions.join(", "));
    else if (t.region && t.region !== "global") meta.push("Region: " + t.region);
    if (t.first_seen) meta.push("first seen " + fmtDay(t.first_seen));
    if (meta.length) box.appendChild(h("p", { text: meta.join(", ") + "." }));
    return box;
  }

  function trendTable(list, everything, hasModel) {
    var withSeries = list.some(function (t) { return validSeries(t.series); });
    var cols = [["Score", "num"], ["Topic"], [hasModel ? "Model's reason" : "Why it ranks"], ["Source"]];
    if (withSeries) cols.push(["Wikipedia views a day", "views"]);
    var body = h("tbody");
    var dup = {};          // the same title in two regions (per-country feeds): say which one each row is
    everything.forEach(function (t) { dup[t.title] = dup[t.title] ? 2 : 1; });
    Object.keys(dup).forEach(function (k) { dup[k] = dup[k] > 1; });
    list.forEach(function (t) {
      var btn = h("button", { "class": "rowbtn", type: "button", "aria-expanded": "false", title: t.title }, [h("span", { "class": "clamp", text: t.title })]);
      var names = [];
      (t.sources || []).forEach(function (s) { var n = s.name || sourceName(s.id); if (names.indexOf(n) < 0) names.push(n); });
      var tr = h("tr", { "class": "row" }, [
        cell("score num", [num(t.score, 0)]),
        cell("title", [btn, dup[t.title] && t.region && t.region !== "global" ? " · " + (t.regions && t.regions.length ? t.regions.join(", ") : t.region) : ""]),
        cell("reason", [t.reason || ""]),
        cell("src", [names.join(", ") || "–"])
      ]);
      if (withSeries) {
        var s = validSeries(t.series) ? t.series : null;
        tr.appendChild(cell("views", s ? [spark(s), h("span", { "class": "v", text: num(s.values[s.values.length - 1]) })] : []));
      }
      expandable(tr, btn, cols.length, function () { return trendDetail(t); });
      body.appendChild(tr);
    });
    return h("table", null, [head(cols), body]);
  }

  function trendSection(niche) {
    var all = data.topics.filter(function (t) { return t.niche === niche.id; });
    var split = splitTrends(all);
    var shown = state.all ? split.strong : split.strong.slice(0, TOP);
    var hasModel = all.some(function (t) { return t.fit != null; });
    var weight = data.llm && typeof data.llm.fit_weight === "number" ? data.llm.fit_weight : 0.6;
    var sec = h("section", { "aria-labelledby": "h-trends" }, [
      h("h2", { id: "h-trends" }, ["Trends", h("span", { "class": "n", text: String(split.strong.length) })])
    ]);
    if (!all.length) {
      sec.appendChild(h("p", { "class": "none" }, ["Nothing is rising in this niche right now. The next run may find something; until then there is nothing to rank."]));
      return sec;
    }
    if (!split.strong.length) {
      sec.appendChild(h("p", { "class": "none", text: "No trend in this niche scores " + STRONG + " or more today." + (split.watching.length ? " The " + split.watching.length + " lower-scoring ones are under Watching." : "") }));
    } else {
      sec.appendChild(h("p", { "class": "lede", text: "Scores of " + STRONG + " and up, ranked by score" + (hasModel
        ? ": " + (100 - Math.round(weight * 100)) + "% rules, " + Math.round(weight * 100) + "% the model's judgement of fit. "
        : " (rules only, no model scored this run). ") + "Click a row for the evidence." }));
      sec.appendChild(trendTable(shown, all, hasModel));
      if (split.strong.length > TOP) {
        sec.appendChild(h("p", { "class": "more" }, [h("button", {
          "class": "btn", type: "button",
          text: state.all ? "Show the top " + TOP + " only" : "Show all " + split.strong.length + " trends",
          onclick: function () { state.all = !state.all; render(); }
        })]));
      }
    }
    if (split.watching.length) {
      var det = h("details", { "class": "watch" }, [
        h("summary", null, ["Watching", h("span", { "class": "n", text: String(split.watching.length) })]),
        h("p", { "class": "lede", text: "Scored below " + STRONG + ", so not in the table above. Grouping is by score alone; the reason column often says there is no timely event or the angle is unclear." }),
        trendTable(split.watching, all, hasModel)
      ]);
      if (state.watch) det.setAttribute("open", "");
      det.addEventListener("toggle", function () { state.watch = det.open; });
      sec.appendChild(det);
    }
    return sec;
  }

  function upcomingDetail(e) {
    var box = h("div", { "class": "detail-body" });
    box.appendChild(h("h3", { text: "Source" }));
    var p = h("p", null, [link(e.url, e.title)]);
    box.appendChild(p);
    box.appendChild(h("p", { text: (KINDS[e.kind] || e.kind || "Entry") + " from " + sourceName(e.source) + (e.region && e.region !== "global" ? ", " + e.region : "") + "." }));
    if (e.attribution) box.appendChild(h("p", { text: e.attribution }));
    if (e.publish_by) {
      var lag = typeof data.lag_days === "number" ? ", the event date minus " + plural(data.lag_days, "day", "days") + " for search engines to index the page" : "";
      box.appendChild(h("p", { text: "Publish by " + fmtDay(e.publish_by) + lag + "." }));
    }
    return box;
  }

  function upcomingSection(niche) {
    var rows = visibleUpcoming(data, niche.id);
    var sec = h("section", { "aria-labelledby": "h-up" }, [
      h("h2", { id: "h-up" }, ["Upcoming", h("span", { "class": "n", text: String(rows.length) })])
    ]);
    if (!rows.length) {
      sec.appendChild(h("p", { "class": "none" }, ["Nothing dated is coming up for this niche. Events appear here once a calendar or a deadline in your sources falls inside the look-ahead window."]));
      return sec;
    }
    sec.appendChild(h("p", { "class": "lede", text: "Publish by is the event date minus the time a new page needs to be indexed. " + "Red means publish within " + URGENT_DAYS + " days." }));
    var cols = [["Event date"], ["Event"], ["Publish by"], ["Time left"], ["Type"]];
    var body = h("tbody");
    rows.forEach(function (e) {
      var left = timeLeft(e.publish_by);
      var btn = h("button", { "class": "rowbtn", type: "button", "aria-expanded": "false", text: e.title });
      var tr = h("tr", { "class": "row up" }, [
        cell("date", [fmtDay(e.date, e.date_precision)], "Event date"),
        cell("title wide", [btn]),
        cell("by", [e.publish_by ? fmtDay(e.publish_by) : "–"], "Publish by"),
        cell("left" + (left.urgent ? " urgent" : ""), [left.text], "Time left"),
        cell("type", [KINDS[e.kind] || (e.kind ? e.kind.charAt(0).toUpperCase() + e.kind.slice(1) : "")], "Type")
      ]);
      expandable(tr, btn, cols.length, function () { return upcomingDetail(e); });
      body.appendChild(tr);
    });
    sec.appendChild(h("table", null, [head(cols), body]));
    return sec;
  }

  function gapDetail(g) {
    var box = h("div", { "class": "detail-body" });
    box.appendChild(h("h3", { text: "Why it is a gap" }));
    box.appendChild(h("p", { text: g.why || "" }));
    if (g.angle) {
      box.appendChild(h("h3", { text: "Suggested angle" }));
      box.appendChild(h("p", { text: g.angle }));
    }
    if (g.questions && g.questions.length) {
      box.appendChild(h("h3", { text: "Questions readers ask" }));
      box.appendChild(h("ul", null, g.questions.map(function (q) { return h("li", { text: q }); })));
    }
    if (safeUrl(g.page)) box.appendChild(h("p", null, ["Best page today: ", link(g.page, g.page)]));
    return box;
  }

  function gapsSection(niche) {
    var gaps = data.gaps || { rows: [] };
    var rows = (gaps.rows || []).filter(function (g) { return g.niche === niche.id || g.niche == null; });
    var sample = !!gaps.sample;
    var sec = h("section", { "aria-labelledby": "h-gaps" }, [
      h("h2", { id: "h-gaps" }, ["Gaps", h("span", { "class": "n", text: String(rows.length) }),
        sample ? h("span", { "class": "flag", text: gaps.label || "Sample data" }) : null])
    ]);
    if (sample && gaps.note) sec.appendChild(h("p", { "class": "lede", text: gaps.note }));
    else if (!sample) sec.appendChild(h("p", { "class": "lede", text: "Search Console queries where you rank poorly or have no page." }));
    if (!rows.length) {
      sec.appendChild(h("p", { "class": "none" }, sample
        ? ["No gaps in the sample data for this niche."]
        : ["No Search Console data yet. Connect it with ", h("code", { text: "nicheclock connect gsc" }), " and the next run lists the queries you are missing."]));
      return sec;
    }
    var hasNums = rows.some(function (g) { return typeof g.impressions === "number"; });
    var cols = [["Query"]];
    if (hasNums) cols.push(["Impressions", "num"], ["Position", "num"], ["Status"]);
    cols.push([hasNums ? "Suggested angle" : "Why"]);
    var body = h("tbody");
    rows.forEach(function (g) {
      var btn = h("button", { "class": "rowbtn", type: "button", "aria-expanded": "false", text: g.query });
      var cells = [cell("title", [btn])];
      if (hasNums) {
        cells.push(cell("num", [num(g.impressions)], "Impressions"));
        cells.push(cell("num", [typeof g.position === "number" ? num(g.position, 1) : ""], "Position"));
        cells.push(cell("", [g.status === "missing" ? "No page" : g.status === "weak" ? "Ranks low" : ""], "Status"));
      }
      cells.push(cell("reason", [hasNums ? (g.angle || "") : (g.why || "")], hasNums ? "Angle" : "Why"));
      var tr = h("tr", { "class": "row gap" }, cells);
      expandable(tr, btn, cols.length, function () { return gapDetail(g); });
      body.appendChild(tr);
    });
    sec.appendChild(h("table", null, [head(cols), body]));
    return sec;
  }

  /* ---------- page ---------- */

  function render() {
    clear(app);
    app.removeAttribute("aria-busy");
    var niches = data.niches;
    var tabs = h("div", { "class": "tabs", role: "tablist", "aria-label": "Niches" });
    niches.forEach(function (n) {
      var count = splitTrends(data.topics.filter(function (t) { return t.niche === n.id; })).strong.length;
      var selected = n.id === state.niche;
      tabs.appendChild(h("button", {
        "class": "tab", type: "button", role: "tab", id: "tab-" + n.id, "aria-selected": selected ? "true" : "false",
        tabindex: selected ? "0" : "-1", "aria-controls": "panel",
        onclick: function () { select(n.id); },
        onkeydown: function (e) {
          var i = niches.indexOf(n), j = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : -1;
          if (j < 0 || j >= niches.length) return;
          select(niches[j].id);
          document.getElementById("tab-" + niches[j].id).focus();
        }
      }, [n.name || n.id, h("span", { "class": "n", text: String(count) })]));
    });
    app.appendChild(tabs);
    var niche = niches.filter(function (n) { return n.id === state.niche; })[0];
    var panel = h("div", { id: "panel", role: "tabpanel", "aria-labelledby": "tab-" + niche.id });
    if (niche.blurb) panel.appendChild(h("p", { "class": "blurb", text: niche.blurb }));
    panel.appendChild(trendSection(niche));
    panel.appendChild(upcomingSection(niche));
    panel.appendChild(gapsSection(niche));
    app.appendChild(panel);
  }

  function select(id) {
    state.niche = id;
    state.all = false;
    state.watch = false;
    if (history.replaceState) history.replaceState(null, "", "#" + encodeURIComponent(id));
    render();
  }

  function valid(d) {
    return d && typeof d === "object" && d.schema === 1 && Array.isArray(d.niches) && Array.isArray(d.topics) && Array.isArray(d.upcoming);
  }

  function ready(d) {
    if (!valid(d)) {
      fail(d && d.schema && d.schema !== 1 ? "This file uses data format " + d.schema + ", which this page does not read." : "The file is not a nicheclock report.");
      return;
    }
    data = d;
    var stamp = document.getElementById("stamp");
    if (stamp) stamp.textContent = (app.dataset.stamp || "Generated {time} UTC").replace("{time}", fmtStamp(d.generated_at));
    var n = d.niches.length;
    [].forEach.call(document.querySelectorAll("[data-niche-count]"), function (e) {
      e.textContent = (WORDS[n] || String(n)) + " sample " + (n === 1 ? "niche" : "niches");
    });
    var foot = document.getElementById("foot-sources");
    if (foot) {
      var seen = {}, lines = [];
      d.topics.forEach(function (t) { (t.sources || []).forEach(function (s) { if (s.attribution && !seen[s.attribution]) { seen[s.attribution] = 1; lines.push(s.attribution); } }); });
      d.upcoming.forEach(function (e) { if (e.attribution && !seen[e.attribution]) { seen[e.attribution] = 1; lines.push(e.attribution); } });
      clear(foot);
      lines.forEach(function (l) { foot.appendChild(h("p", { text: l })); });
    }
    if (!d.niches.length) {
      showState("No niches in this data", [["Run ", h("code", { text: "nicheclock run" }), " after adding a niche to nicheclock.yaml."]]);
      return;
    }
    var want = decodeURIComponent((location.hash || "").slice(1));
    state.niche = d.niches.some(function (x) { return x.id === want; }) ? want : pickNiche(d).id;
    render();
  }

  function load() {
    var embedded = document.getElementById("nc-data");
    if (embedded) {
      try { ready(JSON.parse(embedded.textContent)); } catch (e) { fail("The data embedded in this page is not valid JSON."); }
      return;
    }
    var src = app.dataset.src;
    if (!src) { fail("This page has no data source."); return; }
    app.setAttribute("aria-busy", "true");
    showState("Loading data…", [["Reading " + src]]);
    fetch(src, { cache: "no-cache", credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("The server answered " + r.status + "."); return r.json(); })
      .then(ready)
      .catch(function (e) { fail(e && e.message ? e.message : "The request failed."); });
  }

  window.addEventListener("hashchange", function () {
    if (!data) return;
    var want = decodeURIComponent((location.hash || "").slice(1));
    if (want !== state.niche && data.niches.some(function (x) { return x.id === want; })) { state.niche = want; state.all = false; state.watch = false; render(); }
  });

  var copy = document.getElementById("copy");
  if (copy) {
    copy.addEventListener("click", function () {
      var text = document.getElementById("install").textContent;
      var done = function () { copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy"; }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
      else { fallback(text); done(); }
    });
  }
  function fallback(text) {
    var t = document.createElement("textarea");
    t.value = text; t.setAttribute("readonly", ""); t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) { /* the command stays visible, select it by hand */ }
    document.body.removeChild(t);
  }

  load();
})();

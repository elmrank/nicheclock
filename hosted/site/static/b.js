// Cookieless page-view counts (POST /b on this Worker, hosted/src/beacon.ts). Nothing is stored in the browser.
// When the reader leaves a page (tab hidden or page closed) one beacon goes out with: path (no query, no hash),
// visible seconds (cap 1800), max scroll bucket, device (mobile <= 767 px), kind of referrer (never its URL) and
// whether a link to another page of this site was clicked. Back in the tab and leaving again re-sends with
// `previous`, so the Worker moves the view instead of counting a second one.
(function () {
  var nav = navigator, win = window, doc = document, loc = win.location;
  if (nav.webdriver || typeof nav.sendBeacon !== "function" || !/^\/[A-Za-z0-9\/_\-.~%]{0,199}$/.test(loc.pathname)) return;

  var AI = ["chatgpt.com", "chat.openai.com", "perplexity.ai", "claude.ai", "gemini.google.com", "copilot.microsoft.com", "chat.deepseek.com", "grok.com", "chat.mistral.ai", "you.com", "phind.com", "poe.com"];
  var SEARCH = ["bing.com", "duckduckgo.com", "yahoo.com", "yandex.com", "yandex.ru", "baidu.com", "ecosia.org", "search.brave.com", "startpage.com", "qwant.com", "kagi.com"];
  var SOCIAL = ["bsky.app", "x.com", "twitter.com", "t.co", "reddit.com", "news.ycombinator.com", "facebook.com", "instagram.com", "linkedin.com", "lnkd.in", "youtube.com", "youtu.be", "threads.net", "mastodon.social", "lobste.rs", "discord.com", "t.me"];
  function within(host, list) { return list.some(function (d) { return host === d || host.slice(-d.length - 1) === "." + d; }); }
  function sourceKind(ref) {
    if (!ref) return "direct";
    var u;
    try { u = new URL(ref); } catch (e) { return "other"; }
    var h = u.hostname.toLowerCase().replace(/^www\./, "");
    if (h === loc.hostname.toLowerCase().replace(/^www\./, "")) return "internal";
    if (within(h, AI)) return "ai";
    if (/(^|\.)google\.[a-z.]{2,6}$/.test(h) || within(h, SEARCH)) return "search";
    if (within(h, SOCIAL)) return "social";
    return "other";
  }

  var source = sourceKind(doc.referrer), device = win.innerWidth <= 767 ? "mobile" : "desktop";
  var visibleMs = 0, since = doc.visibilityState === "visible" ? performance.now() : null;
  var seen = since !== null, scrollPct = 0, clicked = 0, sent = null;

  function measure() {
    var h = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
    var pct = h > 0 ? ((win.scrollY + win.innerHeight) / h) * 100 : 100;
    if (pct > scrollPct) scrollPct = Math.min(100, pct);
  }
  function bucket(p) {
    if (p >= 99) return 100;
    var b = [90, 75, 50, 25];
    for (var i = 0; i < b.length; i++) if (p >= b[i]) return b[i];
    return 0;
  }
  function flush() {
    if (!seen) return;
    measure();
    var running = since === null ? 0 : performance.now() - since;
    var snap = { seconds: Math.min(1800, Math.floor((visibleMs + running) / 1000)), scroll: bucket(scrollPct), clicks: clicked };
    if (sent && sent.seconds === snap.seconds && sent.scroll === snap.scroll && sent.clicks === snap.clicks) return;
    var body = { path: loc.pathname, device: device, source: source, seconds: snap.seconds, scroll: snap.scroll, clicks: snap.clicks };
    if (sent) body.previous = sent;
    sent = snap;
    nav.sendBeacon("/b", JSON.stringify(body));
  }
  function hide() { if (since !== null) { visibleMs += performance.now() - since; since = null; } flush(); }

  var queued = false;
  win.addEventListener("scroll", function () {
    if (queued) return;
    queued = true;
    win.requestAnimationFrame(function () { queued = false; measure(); });
  }, { passive: true });
  doc.addEventListener("visibilitychange", function () {
    if (doc.visibilityState === "hidden") hide();
    else { if (since === null) since = performance.now(); seen = true; }
  });
  win.addEventListener("pagehide", hide);
  doc.addEventListener("click", function (e) {
    var t = e.target, a = t && t.closest ? t.closest("a[href]") : null;
    if (!a) return;
    try {
      var u = new URL(a.getAttribute("href"), loc.href);
      if (u.origin === loc.origin && u.pathname !== loc.pathname) clicked = 1;
    } catch (err) { /* not a URL */ }
  }, true);
  measure();
})();

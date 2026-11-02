#!/usr/bin/env python3
"""Build the Trending now page if it is out of date, then serve it on http://localhost:4300/ .

    python hosted/site/trending/serve.py [--port 4300] [--host 127.0.0.1] [--data DIR] [--rebuild]

Python standard library only: no npm, no npx, no shell, no subprocess, so it runs the same on Windows, macOS and Linux.
The page is rebuilt when dist/ is missing or the content of trending.css, trending.js, build.py or the data files differs from what dist/ was built from.
Data: --data, else hosted/site/catalog-data (the real catalog) when it holds meta.json, trending.json and upcoming.json,
else the sample fixture (invented numbers, and the page says so). The response headers copy hosted/site/static/_headers
(same Content-Security-Policy), so anything the real site would block fails here too.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import re
import shutil
import socketserver
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402  (site/trending/build.py)

DIST = HERE / "dist"


def fingerprint(data: Path) -> str:
    """Hash of the data dir path and the bytes of every file the page is built from. Content, not mtimes: a checkout,
    a rebase or a restored file can leave dist/ newer than data it does not hold."""
    h = hashlib.sha256(str(data).encode())
    modules = [HERE / f for f in ("match.js", "profile.js", "niche.js")]
    for p in [HERE / "trending.css", HERE / "trending.js", HERE / "build.py", build.SITE_DIR / "static" / "favicon.svg"] + modules + build.data_files(data) + sorted((data / "match").glob("*.json")):
        h.update(p.name.encode())
        h.update(p.read_bytes() if p.exists() else b"-")
    return h.hexdigest()


def describe(data: Path) -> str:
    """One line that says which catalog build is served, so a stale checkout shows up at startup."""
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    world = json.loads((data / "trending.json").read_text(encoding="utf-8"))["topics"]
    return f"catalog build {meta.get('generated_at')} (data day {meta.get('date')}), Worldwide {len(world)} topics"


def build_if_needed(data: Path, force: bool) -> None:
    index = DIST / "index.html"
    marker = DIST / ".data-dir"
    fp = fingerprint(data)
    if not force and index.exists() and marker.exists() and marker.read_text(encoding="utf-8") == fp:
        print(f"dist is up to date ({data})")
        return
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    names = build.stage(DIST, data)
    shutil.copy(build.SITE_DIR / "static" / "favicon.svg", DIST / "favicon.svg")
    (DIST / "index.html").write_text(build.index_html(names), encoding="utf-8")
    marker.write_text(fp, encoding="utf-8")
    print(f"built {DIST} from {data}")


def security_headers() -> list[tuple[str, str]]:
    """The `/*` block of static/_headers, so the dev server enforces the same policy as the deployed site."""
    text = (build.SITE_DIR / "static" / "_headers").read_text(encoding="utf-8")
    block = re.split(r"^/", text.split("\n/*\n", 1)[1], maxsplit=1, flags=re.M)[0] if "\n/*\n" in text else ""
    out = []
    for line in block.splitlines():
        k, sep, v = line.strip().partition(":")
        if sep:
            out.append((k.strip(), v.strip()))
    return out


def make_handler(headers: list[tuple[str, str]]):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(DIST), **kw)

        def end_headers(self):
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    Handler.extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                              ".js": "text/javascript", ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=4300)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data", type=Path, default=None, help="dir with meta.json, trending.json, upcoming.json")
    ap.add_argument("--rebuild", action="store_true", help="rebuild even if dist/ looks current")
    args = ap.parse_args()
    data = (args.data or build.default_data()).resolve()
    build_if_needed(data, args.rebuild)
    kind = "SAMPLE fixture (invented numbers)" if data == build.FIXTURE.resolve() else "catalog data"
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.host, args.port), make_handler(security_headers())) as srv:
        print(f"Trending now on http://localhost:{args.port}/  ({kind}: {describe(data)}; Ctrl+C stops)")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("stopped")


if __name__ == "__main__":
    main()

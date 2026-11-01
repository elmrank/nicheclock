"""Shared helpers for the integrations slice tests (offline)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace


@dataclass
class Item:
    """Stand-in with the same fields as models.FeedItem (ARCHITECTURE.md)."""

    project: str
    niche: str
    kind: str
    title: str
    url: str
    why: str
    score: float
    at: datetime = field(default_factory=lambda: datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc))


def sample_items(project: str = "mysite") -> list[Item]:
    return [
        Item(project, "birdwatching", "topic", "Snow Goose demo goes live",
             "https://example.com/snow-goose", "Google Trends rank 3 (200K+ searches)", 9.1),
        Item(project, "birdwatching", "topic", "Merlin Bird ID patch 1.4 notes",
             "https://example.com/itt-patch", "Wikipedia pageviews x4.2 vs 7d avg", 7.5),
        Item(project, "birdwatching", "event", "Spring Bird Festival 2027",
             "https://example.com/sgf", "publish by 2027-05-28 (event 2027-06-06, lag 3d)", 6.0),
        Item(project, "birdwatching", "topic", "Low-score thing", "https://example.com/low", "meh", 0.5),
        Item(project, "bird photography", "gap", "bird treaty act compliance checklist",
             "", "1,240 impressions, best position 34.2, no page covers it", 8.0),
    ]


def make_cfg(tmp_path, notify, project="mysite"):
    proj = SimpleNamespace(name=project, urls=["https://example.com"], niches=[], notify=notify, gsc=None)
    return SimpleNamespace(
        identity=SimpleNamespace(user_agent="nicheclock-test/0 (+mailto:t@example.com)",
                                 contact_email="t@example.com"),
        data_dir=str(Path(tmp_path or ".") / "data"), out_dir=str(Path(tmp_path or ".") / "out"), default_lag_days=3,
        projects=[proj],
    )


class FakeServer:
    """Local HTTP server recording requests; ``responses`` is a queue of (status, headers, body)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.responses: list[tuple[int, dict, bytes]] = []
        self.responder = None  # optional fn(path, json) -> (status, headers, body_bytes)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                try:
                    parsed = json.loads(body)
                except ValueError:
                    parsed = None
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "json": parsed})
                if outer.responder:
                    status, headers, rbody = outer.responder(self.path, parsed)
                else:
                    status, headers, rbody = (outer.responses.pop(0) if outer.responses
                                              else (200, {}, b'{"ok": true}'))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(rbody)))
                self.end_headers()
                self.wfile.write(rbody)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

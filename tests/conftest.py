import json
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests

from nicheclock.config import Config, Identity, Niche, Project
from nicheclock.http import Client

FIX = Path(__file__).parent / "fixtures"


def report_data(path: Path) -> dict:
    """The JSON that a written report page (index.html) draws from: the ``nc-data`` block embedded in it."""
    m = re.search(r'<script type="application/json" id="nc-data">(.*?)</script>', Path(path).read_text(encoding="utf-8"), re.S)
    assert m, f"{path}: no embedded report data"
    return json.loads(m.group(1))


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class Resp(requests.Response):
    def __init__(self, status=200, body="", headers=None):
        super().__init__()
        self.status_code = status
        self._content = body.encode()
        self.headers.update(headers or {})
        self.url = ""


class RouteSession:
    """routes: list of (substring-of-url-with-query, fixture file | (status, body))."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        full = url + ("?" + urlencode(params, doseq=True) if params else "")
        self.calls.append(full)
        if url.endswith("/robots.txt"):
            return Resp(404)
        for sub, target in self.routes:
            if sub in full:
                if isinstance(target, tuple):
                    return Resp(*target)
                return Resp(200, fixture_text(target))
        return Resp(404)


def cfg_with_identity() -> Config:
    cfg = Config(identity=Identity("nc-test/1 (+mailto:t@example.com)", "t@example.com"))
    return cfg


def make_client(routes, cache_dir=None) -> tuple[Client, RouteSession]:
    ses = RouteSession(routes)
    return Client(cfg_with_identity(), session=ses, sleep=lambda s: None, cache_dir=cache_dir), ses


@pytest.fixture
def niche():
    return Niche(name="birdwatching", keywords=["bird watching", "bird feeder"], entities=["Merlin Bird ID"],
                 exclude=["tips"], sources=["google_trends"], formats=["tier list"])

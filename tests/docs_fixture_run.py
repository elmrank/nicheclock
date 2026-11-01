"""Run `nicheclock run` against the recorded test fixtures instead of the network (no requests leave the machine).

    python tests/docs_fixture_run.py OUTDIR                    # writes OUTDIR/nicheclock.yaml, then OUTDIR/out/example/{feed.json,...}
    python tests/docs_fixture_run.py OUTDIR --llm-url URL      # also judge with an Ollama-compatible server (tests/fake_ollama.py)

The numbers in the result are fixture data, not live trends. Used to make the screenshot in docs/img/ and by docs-smoke.
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nicheclock import cli                               # noqa: E402
from nicheclock.http import Client                       # noqa: E402
from tests.conftest import RouteSession                  # noqa: E402
from tests.test_pipeline import ROUTES                   # noqa: E402

CONFIG = {
    "identity": {"user_agent": "nicheclock-docs/0.1 (+mailto:you@example.com)", "contact_email": "you@example.com"},
    "data_dir": "./data", "out_dir": "./out", "default_lag_days": 3,
    "projects": [{
        "name": "example", "urls": ["https://example.com"],
        "niches": [{"name": "birdwatching", "keywords": ["bird watching", "bird feeder"],
                    "entities": ["Merlin Bird ID (app)"], "exclude": ["tips"], "formats": ["tier list"],
                    "sources": ["google_trends", "wikipedia", "hn", "github", "gdelt", "apple", "federal_register", "rss"],
                    "rss": ["https://blog.test/feed.xml"], "ics": ["https://cal.test/a.ics"], "upcoming": True}],
    }],
}


def main(outdir: str, llm_url: str | None = None) -> int:
    config = dict(CONFIG)
    if llm_url:
        config["llm"] = {"provider": "ollama", "model": "fake", "base_url": llm_url, "daily_budget_usd": 1.0}
    base = Path(outdir)
    base.mkdir(parents=True, exist_ok=True)
    cfg_path = base / "nicheclock.yaml"
    cfg_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    orig = Client.__init__

    def fixture_client(self, cfg, **kw):
        orig(self, cfg, session=RouteSession(ROUTES), sleep=lambda s: None, **kw)

    Client.__init__ = fixture_client
    return cli.main(["--config", str(cfg_path), "run", "--no-notify"])


if __name__ == "__main__":
    args = sys.argv[1:]
    url = args[args.index("--llm-url") + 1] if "--llm-url" in args else None
    raise SystemExit(main(args[0] if args and not args[0].startswith("--") else "docs-fixture", url))

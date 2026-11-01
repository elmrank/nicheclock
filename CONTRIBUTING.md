# Contributing

Issues and pull requests are welcome. By contributing you agree that your work is licensed under the GNU AGPL-3.0-or-later, the license of this repository.

## Set up and test

```bash
uv venv
uv pip install -e '.[test,gsc,mcp,catalog,onboard]'
uv run pytest -q
```

The Python tests run offline against recorded fixtures. No test touches the network. The Worker in `hosted/` has its own tests:

```bash
cd hosted && npm ci && npm run typecheck && npm test
node hosted/site/trending/test.js
```

`docs/*.md` pages are tested too (front matter, links, every config, JSON and shell snippet). If you change a docs page, rebuild the site files with `uv run -q --with markdown-it-py python hosted/site/build.py`.

## Rules

- **Sources:** only a source whose terms allow automated use. Link the terms page and quote the clause in the pull request. A source that cannot show one is not added.
- **Network:** every request to a data source goes through `nicheclock/http.py`: your User-Agent, one request per second per host, robots.txt, `Retry-After`.
- **Identity:** there is no default User-Agent or contact email, and there will not be one.
- **Secrets:** never in the repository or in the config file.
- **Models:** no default provider. Anything that sends text to a model must be listed in [docs/models.md](docs/models.md).
- **Telemetry:** none in the package.
- **Examples and fixtures:** use `example.com` and neutral topics. Do not commit real site data, keys, or generated catalog data.

## Pull requests

One change per pull request, with tests, and a line saying what you ran. Commit messages describe the change. For a bug, include the output of `nicheclock doctor --offline` with keys removed. The longer guide, including how to add a source, is [docs/contributing.md](docs/contributing.md).

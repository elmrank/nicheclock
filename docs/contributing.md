---
title: Contributing
description: Set up a development environment, run the tests and the docs checks, add a source, follow the rules the project keeps, and the AGPL-3.0 license.
order: 15
publish: true
---

# Contributing

nicheclock is licensed under the GNU AGPL-3.0-or-later. By contributing you agree your contribution is under the same license.

> **Status.** The repository is public under the AGPL-3.0. The short version of this page is [CONTRIBUTING.md](https://github.com/elmrank/nicheclock/blob/main/CONTRIBUTING.md).

## Set up

```bash
uv venv
uv pip install -e '.[test,gsc,mcp]'
uv run pytest -q
```

The tests run offline with recorded fixtures. No test touches the network. Add `onboard` to the extras to run the scikit-learn paths.

## The docs are tested

`docs/*.md` with `publish: true` in their front matter are the documentation, and the website builds its docs pages from them. `tests/test_docs.py` checks the front matter, the links, that every YAML snippet is a valid config (or a valid fragment of one: the first word after the fence says where it merges, `top`, `project` or `niche`), that every JSON snippet parses and every bash snippet has valid syntax.

The job `docs-smoke` in `.github/workflows/docs-smoke.yml` runs the commands for real in a clean container: build the image, `init https://example.com` (which must say `no URLs found`, because example.com has no sitemap), `init` on the local demo site in `examples/demo-site`, `docker compose up`, a first run against the live sources, the healthcheck, the report server, the MCP `list_tools` call, and a model run against a fake Ollama server (`tests/fake_ollama.py`). If you change a command in the docs, that job should fail until the docs match.

Docs style: English, plain, no hype, `example.com` for sample sites, no em or en dashes, one H1 per page, features that are not released tagged `<span class="tag soon">Coming</span>`.

## Rules the project keeps

- **Sources:** only sources whose terms allow automated use. A source module carries `NAME`, `TERMS_URL` and `TERMS_NOTE` (the clause it relies on) and exposes `collect(client, niche, day)`; dated sources add `collect_events`.
- **Network:** every request to a data source goes through the one client in `nicheclock/http.py`: identity User-Agent, one request per second per host, robots.txt, `Retry-After`. No source module opens its own connection.
- **Identity:** there is no default User-Agent or contact email, and there will not be.
- **Secrets:** never in the repository or the config file. Config fields name an environment variable or a file.
- **Models:** never a default. Anything that sends text to a provider must be listed in [Models](models.md#what-is-sent-and-what-is-not), and respects the denylist.
- **Language:** all user-facing text is English for now.

## Adding a source

1. Read the source's terms. If they do not allow automated use for this purpose, stop.
2. Add `nicheclock/sources/<name>.py` with the constants above, using an existing small one (`hn.py`) as the pattern, and register the name in `nicheclock/sources/__init__.py` and `nicheclock/config.py`.
3. Add a fixture under `tests/fixtures/` and a test. Tests run offline.
4. Add a row to [Sources](sources.md) with the terms link, and list any `options:` in [Configuration](configuration.md).

## Pull requests

Keep a change to one thing, with tests, and say in the description what you ran. Commit messages state the change, not the file. Report bugs with the output of `nicheclock doctor` and the run log, keys removed.

## Translations

English only for now. Translations of the interface and the docs are welcome later, once the strings are collected in one place.

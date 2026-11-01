# Architecture

nicheclock is a self-hosted niche trend radar. You give it your site's URL(s). It builds an editable niche config, collects trend signals from sources whose terms allow it, and gives you:
- a feed per niche;
- an "upcoming" calendar with publish-by dates;
- optionally, your Google Search Console gaps;
- push notifications and an MCP server.

This page is the map for contributors: the hard rules, the module layout, the config schema and the data models. User documentation is in `docs/`.

Licence AGPL-3.0-or-later. Everything here is new code under that licence; do not copy code from projects under an incompatible licence.

## Bring your own model

The model layer is optional. Providers: anthropic, openai, gemini, ollama, and Claude Code / Codex CLI sign-in. It uses the user's own key or subscription only; keys are never logged. A model can analyse: niche-fit scoring with a one-line reason, merging duplicate trends, angles and reader questions for GSC gaps, junk/ephemeral filtering, and a niche profile from at most 50 page titles at onboarding. Collection stays deterministic script code. The rule-based path is the fallback when no model is configured. Every model call records tokens and estimated cost against a user-set daily cap, and sends only what is needed (titles, snippets, queries; raw GSC rows only if the user enables it).

## Hard rules
- Ship only sources whose terms allow automated use. Each source module carries `TERMS_URL` and a one-line `TERMS_NOTE` quoting the clause.
  - Allowed defaults: Google Trends trending RSS, Wikimedia REST pageviews, Wikipedia/MediaWiki API (facts only), Hacker News Algolia API, GitHub REST API, Apple Marketing Tools RSS, GDELT DOC 2.0 API, Federal Register API, and user-supplied RSS/Atom/ICS URLs (the user is responsible for those).
  - Never ship: Reddit or Arctic Shift, Product Hunt, GitHub Trending HTML, Amazon, Google Trends internal RPC/explore, Chinese hot lists, and any store page, community site or fan database whose terms forbid automated collection. `nicheclock/onboard/feeds.py` never suggests these domains; add your own with the `NICHECLOCK_NEVER_SHIP` environment variable.
- Network:
  - User-Agent and contact email come from user config. There is no built-in default identity; a run refuses to fetch until they are set.
  - At most 1 request per second per host, enforced in one shared HTTP client.
  - Respect robots.txt (cached per host) and 429/Retry-After.
- No secrets in the repo. GSC credentials, bot tokens and SMTP passwords live in files or environment variables named in the config.
- Python 3.11 or newer, stdlib + `requests` + `pyyaml` (+ `mcp` for the MCP server). Optional extras: `scikit-learn` for onboarding TF-IDF (a pure-python TF-IDF is the fallback); `google-auth-oauthlib` for GSC. Tests use pytest, offline, with fixtures. No network in tests.
- Examples, fixtures and docs use a neutral niche (birdwatching) and `example.com` / `example.org` domains.

## Layout (package `nicheclock/`)
| Module | Owner slice | Purpose |
|---|---|---|
| `config.py` | Core | Dataclasses + YAML load/save/validate for `nicheclock.yaml` (schema below) |
| `http.py` | Core | `Client(cfg)`: per-host rate limit, robots cache, UA, retries, `get(url, **kw) -> requests.Response` |
| `store.py` | Core | SQLite at `<data_dir>/nicheclock.db`: signals, topics, events, runs, gsc_rows |
| `models.py` | Core | `Signal`, `Topic`, `Event`, `FeedItem` dataclasses (below) |
| `sources/*.py` | Core | One module per source: `NAME`, `TERMS_URL`, `TERMS_NOTE`, `collect(client, niche_cfg, day) -> list[Signal]` |
| `match.py`, `cluster.py` | Core | Signals → topics; topics → niches by keywords/entities/exclude |
| `upcoming.py` | Core | Events (from ICS/RSS with dates, Wikipedia early-risers) → `publish_by` = date − lag (lag from GSC if connected, else `default_lag_days`) |
| `output/*.py` | Core | Per project: `feed.json`, `feed.md`, `index.html` (one page, `output/app/`: data embedded, same files as the hosted demo), `feed.xml` (RSS) under `<out_dir>/<project>/` |
| `onboard/*.py` | Onboarding | `init` from URLs → config; `preview`; `edit` helpers |
| `notify/*.py` | Integrations | telegram, discord (webhook), email (SMTP), each `send(cfg, items: list[FeedItem]) -> None` |
| `gsc.py` | Integrations | OAuth installed-app flow using the user's own client_secret.json; `gaps(project) -> list[dict]` |
| `mcp_server.py` | Integrations | stdio MCP server: tools `trends_for_niche(project, niche, days=7)`, `upcoming(project, days=30)`, `gsc_gaps(project, limit=50)`, `explain_topic(project, topic, niche=None)` (evidence for the client's own model; no server-side model call); reads the store only |
| `llm/*.py` | Model layer | Providers anthropic / openai / gemini / ollama / claude-code / codex behind `complete(messages, schema, max_tokens, temperature) -> Result`; price table; usage ledger + daily budget (`llm_usage` table); tasks fit, junk, merge, gaps, profile (`llm/prompts/*.md`, versioned); source `denylist`; `llm test`, `llm usage`, `eval export` |
| `cli.py` | Core (integrates) | `nicheclock <cmd>`; each slice exposes `register(subparsers)` in its own module and Core wires it |

## Config schema (`nicheclock.yaml`)
```yaml
identity: {user_agent: "...", contact_email: "..."}      # required before any fetch
data_dir: ./data
out_dir: ./out
default_lag_days: 3
projects:
  - name: mysite
    urls: [https://example.com]
    niches:
      - name: birdwatching
        keywords: [bird watching, bird feeder]
        entities: [Merlin Bird ID]        # map to Wikipedia titles when possible
        formats: [buying guide, release notes]    # content formats, not matched as topics
        exclude: [tips, beginner]
        sources: [google_trends, wikipedia, hn, github, apple, gdelt, federal_register, rss]
        rss: []                             # user-supplied feeds (titles + links only)
        ics: []                             # user-supplied calendars for upcoming
        upcoming: true
    schedule: daily                         # daily | hourly | weekly
    notify: [{type: telegram, token_env: TG_TOKEN, chat_id: "..."}]
    gsc: {property: "sc-domain:example.com", client_secret: ~/.config/nicheclock/client_secret.json}
llm: null                                   # optional, see below
# llm: {provider: anthropic|openai|gemini|ollama|claude-code|codex, model, api_key_env | api_key_file, base_url,
#       daily_budget_usd, max_items_per_run, send_gsc_rows: false, tasks: [fit, junk, merge, gaps],
#       fit_weight: 0.6, timeout_s, price_per_mtok: [in, out], denylist: [domains]}
# projects[].profile: {title, description} tells the model who the site is (init fills it)
# niches[].rss / watch entries may be {url, llm: false}: that feed's items are never sent to a model
```

## Models
- `Signal(source, term, url, rank: int|None, score: float|None, geo: str|None, seen_at: datetime, extra: dict)`
- `Topic(key, label, signals: list[Signal], niche: str|None, score: float)`
- `Event(kind, title, date, date_precision: "day"|"month", source, url, publish_by: date|None, niche: str|None)`
- `FeedItem(project, niche, kind: "topic"|"event"|"gap", title, url, why: str, score: float, at: datetime)`

## CLI
`nicheclock init URL [URL…] [--project NAME]` · `preview [--project]` · `edit` (opens YAML in $EDITOR, then validates) · `run [--project] [--no-notify]` · `serve` (static out_dir on localhost) · `connect gsc --project` · `mcp` · `doctor` (config + identity + robots checks)

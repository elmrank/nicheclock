---
title: Self-host with Docker
description: Run nicheclock in Docker Compose with a schedule, healthcheck, volumes, a reverse proxy, backups, updates, and an optional local Ollama model.
order: 6
publish: true
---

# Self-host with Docker

The repository ships a `Dockerfile` and a `docker-compose.yml`. The container does two things: it serves the reports on port 8080 and it runs `nicheclock run` on a schedule. State lives in one directory on the host.

You need Docker with Compose v2.24 or newer (`docker compose version`) and a checkout of the repository.

## Set up

```bash
mkdir nicheclock-data
cp .env.example .env
docker compose build
docker compose run --rm nicheclock init https://your-site.example \
  --user-agent "mysite-radar/1.0 (+mailto:you@your-site.example)" \
  --contact-email you@your-site.example
docker compose up -d
```

`init` reads your site (about half a minute for a small one) and writes `nicheclock-data/nicheclock.yaml`. `up -d` starts the service; with no earlier run it starts one at once, which takes one to two minutes. Then open <http://127.0.0.1:8080/>.

Edit the niches the same way as anywhere else, through the file or through the CLI inside a throwaway container:

```bash
docker compose run --rm nicheclock edit --niche "Feeder" --add-keyword "suet cake"
docker compose run --rm nicheclock doctor --offline
```

The next scheduled run picks the change up. To run now: `docker compose exec nicheclock nicheclock run`.

Until a prebuilt image is published, `docker compose build` builds it from the checkout. A registry image is <span class="tag soon">Coming</span> at launch.

### Who owns the files

The container runs as the user in `NICHECLOCK_UID` and `NICHECLOCK_GID` (default 1000, set them in `.env` if `id -u` says otherwise) so that `nicheclock-data` stays writable for you and the container. Create the directory yourself before the first start, as above; if Docker creates it, it belongs to root and the container cannot write to it.

## What is where

Everything the container keeps is in one directory, mounted at `/nicheclock`:

```text
nicheclock-data/
  nicheclock.yaml     your config (init writes it; edit freely)
  data/               SQLite database, caches, GDELT queue
  out/                the reports this container serves
  secrets/            Search Console client secret and token
  .cache/             Wikipedia title lookups from init
  .last-run           timestamp of the last scheduled run
```

Why one directory and not three volumes: `init` writes `data_dir: ./data` and `out_dir: ./out` relative to the config file, so one mount makes the generated config work unchanged. Back up one directory, serve one directory. Point `NICHECLOCK_DIR` in `.env` somewhere else if you prefer another location.

The root filesystem of the container is read-only, all capabilities are dropped, and the memory limit is 512 MB.

## Keys and settings: `.env`

`docker compose` loads `.env` into the container. Keep keys there and name them in `nicheclock.yaml`, for example `api_key_env: ANTHROPIC_API_KEY`. `.env.example` lists what nicheclock reads:

| Variable | Meaning |
|---|---|
| `NICHECLOCK_USER_AGENT`, `NICHECLOCK_CONTACT_EMAIL` | Used by `init` when you leave out the flags. |
| `NICHECLOCK_UID`, `NICHECLOCK_GID` | Owner of the state directory. |
| `NICHECLOCK_HOST_PORT` | Host port of the reports (default 8080). |
| `NICHECLOCK_INTERVAL_HOURS` | Hours between runs (default 24). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | Only for the provider you use, see [Models](models.md). |
| `GITHUB_TOKEN`, `TG_TOKEN`, `DISCORD_WEBHOOK`, `SMTP_PASSWORD` | Optional sources and [notifications](configuration.md#notifications). |

`.env` is in `.gitignore`. Do not commit it.

## Schedule

The container runs `nicheclock run` every `NICHECLOCK_INTERVAL_HOURS` hours, and once at start if the last run is older than that. The timestamp in `.last-run` survives restarts, so restarting the container does not trigger a run and does not hammer the sources.

Why a loop in the entrypoint and not cron or supercronic: a run is one slow, polite job once a day. A shell loop needs no extra binary to download and verify, no crontab file, and no choice of time zone, and it is a short shell script you can read in `docker/entrypoint.sh`. The price is that you pick an interval, not a time of day. If you need "every day at 06:00", run the container with a very long interval and trigger `docker compose exec nicheclock nicheclock run` from your host's cron or a systemd timer.

A run that ends with status 1 (one or more sources failed, reports written) is logged and the loop carries on. The per-project `schedule:` field in the config is not read by the loop.

## Healthcheck

`docker compose ps` shows `healthy` when all three are true:

1. `nicheclock doctor --offline` passes: the config parses and the identity is set.
2. The report server answers on port 8080.
3. A run started within the last two intervals plus an hour.

Before the first `init` the container waits, prints the command to run, and reports `unhealthy`. Check with `docker inspect -f '{{.State.Health.Status}}' nicheclock`.

## Logs

```bash
docker compose logs -f nicheclock
docker compose logs --since 24h nicheclock
```

Each run prints its per-niche counts, the failed sources and the exit status. The reports are served with the standard Python HTTP server, so every request to them is logged too.

## Ports and the reverse proxy

The compose file publishes `127.0.0.1:8080` only. The report server has no login. Anyone who can reach the port can read your feed, so put a reverse proxy with authentication in front of it if the machine is on the internet. The reports are static files; the proxy needs nothing but `proxy_pass`.

Caddy (automatic HTTPS). Replace the host name and generate the hash with `caddy hash-password`:

```caddyfile
radar.example.com {
	basic_auth {
		me $2a$14$md2tInwfQlr0JeS9vbVEE.T0evcWNzzmyreItDXqrtaFHM1jl2ne.
	}
	reverse_proxy 127.0.0.1:8080
}
```

The hash above is for the throwaway password `example-only`. Do not use it.

nginx (bring your own certificate, for example with certbot). Create the password file with `printf 'me:%s\n' "$(openssl passwd -apr1)" | sudo tee /etc/nginx/nicheclock.htpasswd`:

```nginx
server {
    listen 80;
    server_name radar.example.com;
    auth_basic "nicheclock";
    auth_basic_user_file /etc/nginx/nicheclock.htpasswd;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
    }
}
```

If the proxy runs in another container on the same Compose network, use `http://nicheclock:8080` and drop the `ports:` entry.

## Resources

Measured on 2026-10-31 with the image built from this repository (Python 3.12 slim, extras `gsc,mcp`), one small project of three niches:

| | |
|---|---|
| Image | 273 MB on disk, 65 MB to pull (compressed) |
| Memory | peak 133 MiB during `init` and a first `run`; about 57 MiB idle with the report server |
| CPU | about 11 CPU-seconds for `init` plus a first `run` (two minutes of wall time, mostly waiting between requests); idle is 0.0% |
| Network | outbound HTTPS to the [sources](sources.md) you enabled |
| Port | 8080 in the container |

A small ARM board or the smallest VPS is enough. Wall time is dominated by the one-request-per-second rule, not by CPU. The `onboard` extra adds scikit-learn (about 150 MB more image); results are identical, so the image leaves it out. Build with `--build-arg EXTRAS=gsc,mcp,onboard` to include it.

## Update

```bash
git pull
docker compose build --pull
docker compose up -d
```

The state directory is not touched. `docker compose logs` shows the new version's first run. Read the changelog first if the major version changed.

## Back up and restore

Everything is in `nicheclock-data`. Stop the service so the SQLite file is quiet, archive the directory, start again:

```bash
docker compose stop
tar -czf nicheclock-backup.tgz nicheclock-data
docker compose start
```

Restore on a new host by unpacking the archive next to `docker-compose.yml` and running `docker compose up -d`. The archive contains `secrets/` (Search Console token) and `.env` is separate, so protect the archive like a password. The reports can be rebuilt by a run; the history in `data/` cannot.

## Optional: a local model with Ollama

The compose file has an `ollama` service in a profile, so it does not start unless you ask:

```bash
docker compose --profile ollama up -d
docker compose exec ollama ollama pull llama3.2
```

Then set `llm: {provider: ollama, model: llama3.2, base_url: "http://ollama:11434"}` in `nicheclock.yaml`. The service keeps its models in a named volume. It needs several GB of RAM and disk depending on the model. Judging in `run` is <span class="tag soon">Coming</span>; see [Models](models.md). The `init --llm` step already works with it.

## Search Console in Docker

The OAuth consent screen needs a browser on the machine that talks to Google, so do the `connect` step on your own computer and copy the token across. See [Search Console](search-console.md#in-docker).

## Stop and remove

```bash
docker compose down            # keeps nicheclock-data
rm -rf nicheclock-data         # deletes your config, history and reports
```

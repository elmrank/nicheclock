#!/usr/bin/env bash
# docs-smoke: runs the documented Docker flow end to end in a clean container.
#   bash tests/docs_smoke.sh            (needs docker with the compose plugin, network access)
# Steps: build, compose config, `init https://example.com` (no sitemap: must say so), `init` on the local demo
# site, `docker compose up`, wait for healthy, assert the report files and the HTTP server, MCP list_tools, down.
set -euo pipefail
cd "$(dirname "$0")/.."

STATE="$(mktemp -d)"
export NICHECLOCK_DIR="$STATE" NICHECLOCK_UID="$(id -u)" NICHECLOCK_GID="$(id -g)" NICHECLOCK_HOST_PORT="${NICHECLOCK_HOST_PORT:-18080}"
UA="nicheclock-docs-smoke/0.1 (+mailto:ci@example.com)"
EMAIL="ci@example.com"
cleanup() { docker compose down -v >/dev/null 2>&1 || true; rm -rf "$STATE" 2>/dev/null || true; }
trap cleanup EXIT
step() { printf '\n== %s\n' "$*"; }
fail() { echo "docs-smoke FAILED: $*" >&2; exit 1; }

step "build"
docker compose build
docker image inspect nicheclock:latest --format 'image size: {{.Size}} bytes'

step "compose files parse (with and without the ollama profile, and with .env.example as the env file)"
docker compose config -q
docker compose --profile ollama config -q
docker compose --env-file .env.example config -q

step "docs snippets: Docker override file parses, derived image builds, proxy configs validate"
TMP="$(mktemp -d)"
python3 tests/docs_blocks.py docs/models.md dockerfile > Dockerfile.cli
python3 tests/docs_blocks.py docs/models.md compose-override > docker-compose.cli.yml
docker compose -f docker-compose.yml -f docker-compose.cli.yml config -q
docker compose -f docker-compose.yml -f docker-compose.cli.yml build
docker run --rm --entrypoint sh nicheclock-cli:latest -c 'claude --version && codex --version 2>/dev/null | tail -1'
rm -f Dockerfile.cli docker-compose.cli.yml
python3 tests/docs_blocks.py docs/docker.md caddyfile > "$TMP/Caddyfile"
python3 tests/docs_blocks.py docs/docker.md nginx > "$TMP/default.conf"
printf 'me:%s\n' "$(openssl passwd -apr1 example-only)" > "$TMP/htpasswd"
docker run --rm -v "$TMP/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config /etc/caddy/Caddyfile
docker run --rm -v "$TMP/default.conf:/etc/nginx/conf.d/default.conf:ro" -v "$TMP/htpasswd:/etc/nginx/nicheclock.htpasswd:ro" nginx:alpine nginx -t

step "init https://example.com: the site has no sitemap, so init must say so and write nothing"
set +e
out="$(docker compose run --rm -T nicheclock init https://example.com --user-agent "$UA" --contact-email "$EMAIL" 2>&1)"
rc=$?
set -e
tail -5 <<<"$out"
[ "$rc" -ne 0 ] || fail "init https://example.com should exit non-zero"
grep -q "no URLs found" <<<"$out" || fail "expected 'no URLs found'"
[ ! -e "$STATE/nicheclock.yaml" ] || fail "init wrote a config for an empty site"

step "init on the local demo site (examples/demo-site, served inside the container)"
docker compose run --rm -T -v "$PWD/examples/demo-site:/demo:ro" --entrypoint sh nicheclock -c \
  "python -m http.server 8099 --bind 127.0.0.1 --directory /demo >/dev/null 2>&1 & sleep 1; exec nicheclock init http://127.0.0.1:8099 --project example --user-agent '$UA' --contact-email $EMAIL"
[ -s "$STATE/nicheclock.yaml" ] || fail "no nicheclock.yaml"
docker compose run --rm -T nicheclock doctor --offline

step "edit from the CLI (docs/docker.md), and drop GDELT so the live run stays short: it is the slowest and most rate-limited source"
docker compose run --rm -T nicheclock edit --niche "Feeder" --add-keyword "suet cake"
for n in "Feeder" "Optics" "Field"; do
  docker compose run --rm -T nicheclock edit --niche "$n" --remove-source gdelt
done

step "up: serve + first scheduled run"
docker compose up -d
for i in $(seq 1 120); do
  [ -s "$STATE/out/example/feed.json" ] && break
  sleep 5
done
[ -s "$STATE/out/example/feed.json" ] || { docker compose logs --tail 50; fail "no feed.json after 10 minutes"; }
for f in feed.json feed.md feed.xml index.html; do [ -s "$STATE/out/example/$f" ] || fail "missing out/example/$f"; done
[ -s "$STATE/out/index.html" ] || fail "missing out/index.html"
[ -s "$STATE/data/nicheclock.db" ] || fail "missing data/nicheclock.db"

step "healthcheck turns healthy"
for i in $(seq 1 30); do
  h="$(docker inspect -f '{{.State.Health.Status}}' nicheclock)"
  [ "$h" = healthy ] && break
  sleep 5
done
[ "$h" = healthy ] || { docker compose logs --tail 50; fail "container is $h"; }

step "HTTP: reports are served"
curl -fsS -o "$STATE.html" "http://127.0.0.1:${NICHECLOCK_HOST_PORT}/example/index.html"
grep -qi "<html" "$STATE.html" || fail "index.html not served"
rm -f "$STATE.html"
curl -fsS "http://127.0.0.1:${NICHECLOCK_HOST_PORT}/example/feed.json" | python3 -c 'import json,sys; json.load(sys.stdin)'
docker stats --no-stream --format 'memory: {{.MemUsage}}  cpu: {{.CPUPerc}}' nicheclock

step "MCP over stdio answers list_tools and a tool call: inside the container, and through docker compose run as a client would"
docker compose run --rm -T -v "$PWD/tests/mcp_list_tools.py:/t/mcp_list_tools.py:ro" --entrypoint python nicheclock /t/mcp_list_tools.py upcoming example
NC_MCP_CMD="docker compose run --rm -T nicheclock mcp" python3 tests/mcp_list_tools.py upcoming example

step "docker compose exec and the model layer (fake Ollama inside the container)"
docker compose exec -T nicheclock nicheclock --version
docker compose run --rm -T -v "$PWD/tests/fake_ollama.py:/t/fake_ollama.py:ro" --entrypoint sh nicheclock -c '
python /t/fake_ollama.py 11500 &
sleep 1
sed "s|^llm: null\$|llm: {provider: ollama, model: fake, base_url: \"http://127.0.0.1:11500\"}|" /nicheclock/nicheclock.yaml > /tmp/with-llm.yaml
grep -q "^llm: {provider: ollama" /tmp/with-llm.yaml
nicheclock -c /tmp/with-llm.yaml doctor --offline
nicheclock -c /tmp/with-llm.yaml llm test
'

step "backup and restore of the state directory"
docker compose stop
tar -czf "$STATE.tgz" -C "$(dirname "$STATE")" "$(basename "$STATE")"
tar -tzf "$STATE.tgz" > "$STATE.list"
grep -q nicheclock.yaml "$STATE.list"
rm -f "$STATE.list"
rm -f "$STATE.tgz"
docker compose start

step "logs"
docker compose logs --tail 15 nicheclock
echo; echo "docs-smoke OK"

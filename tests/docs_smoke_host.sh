#!/usr/bin/env bash
# docs-smoke (host): the Quickstart, command by command, from a source checkout into an isolated tool install.
#   bash tests/docs_smoke_host.sh        (needs pipx or uv, python >= 3.11, network access)
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
WORK="$(mktemp -d)"
step() { printf '\n== %s\n' "$*"; }
fail() { echo "docs-smoke (host) FAILED: $*" >&2; exit 1; }
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT

step "install: pipx (or uv tool) install '.[gsc,mcp]' into an isolated location"
if command -v pipx >/dev/null; then
  export PIPX_HOME="$WORK/pipx" PIPX_BIN_DIR="$WORK/bin"
  pipx install '.[gsc,mcp]'
else
  export UV_TOOL_DIR="$WORK/uvtools" UV_TOOL_BIN_DIR="$WORK/bin"
  uv tool install '.[gsc,mcp]'
fi
export PATH="$WORK/bin:$PATH"
nicheclock --version
PY="$(head -1 "$(command -v nicheclock)" | sed 's/^#!//; s/ .*//')"

mkdir -p "$WORK/site" && cd "$WORK/site"
UA="nicheclock-docs-smoke/1.0 (+mailto:ci@example.com)"

step "init https://example.com: no sitemap, must say so and write nothing"
set +e
out="$(nicheclock init https://example.com --user-agent "$UA" --contact-email ci@example.com 2>&1)"; rc=$?
set -e
tail -3 <<<"$out"
[ "$rc" -ne 0 ] || fail "init example.com should fail"
grep -q "no URLs found" <<<"$out" || fail "expected 'no URLs found'"
[ ! -e nicheclock.yaml ] || fail "config written for an empty site"

step "init on the demo site (examples/demo-site on 127.0.0.1:8099)"
python3 -m http.server 8099 --bind 127.0.0.1 --directory "$REPO/examples/demo-site" >/dev/null 2>&1 &
PIDS+=($!)
sleep 1
nicheclock init http://127.0.0.1:8099 --project example --user-agent "$UA" --contact-email ci@example.com
kill "${PIDS[0]}" || true; PIDS=()
[ -s nicheclock.yaml ] || fail "no nicheclock.yaml"

step "edit, preview, doctor"
nicheclock edit --niche "Feeder" --add-keyword "suet cake" --add-exclude cheap
nicheclock edit --niche "Optics" --remove-source gdelt
nicheclock preview --live --limit 5
nicheclock doctor

step "run (live sources): exit 0 or 1 is fine, files must exist"
set +e
nicheclock run; rc=$?
set -e
[ "$rc" -le 1 ] || fail "run exited $rc"
for f in feed.json feed.md feed.xml index.html; do [ -s "out/example/$f" ] || fail "missing out/example/$f"; done

step "serve and fetch the report"
PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
nicheclock serve --port "$PORT" >/dev/null 2>&1 &
PIDS+=($!)
sleep 2
curl -fsS -o report.html "http://127.0.0.1:$PORT/example/index.html"
grep -qi "<html" report.html || fail "report not served"
kill "${PIDS[0]}" || true; PIDS=()

step "MCP: list_tools and a tool call over stdio"
NICHECLOCK_CONFIG="$PWD/nicheclock.yaml" "$PY" "$REPO/tests/mcp_list_tools.py" upcoming example

step "Search Console: connect starts the OAuth flow with the read-only scope (stops before Google)"
mkdir -p cfg
cat > cfg/client_secret.json <<'JSON'
{"installed":{"client_id":"123.apps.googleusercontent.com","project_id":"demo","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","client_secret":"x","redirect_uris":["http://localhost"]}}
JSON
"$PY" - <<'PY'
import yaml
d = yaml.safe_load(open("nicheclock.yaml"))
d["projects"][0]["gsc"] = {"property": "sc-domain:example.com", "client_secret": "cfg/client_secret.json"}
yaml.safe_dump(d, open("nicheclock.yaml", "w"), sort_keys=False)
PY
set +e
NICHECLOCK_CONFIG_DIR="$PWD/cfg/tok" timeout 10 "$PY" -u -m nicheclock connect gsc --project example --no-browser --port 0 > connect.out 2>&1
echo "connect exit status: $? (124 = stopped by the timeout while waiting for the browser redirect)"
set -e
url="$(cat connect.out)"
echo "${url:0:200}..."
grep -q "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fwebmasters.readonly&" <<<"$url" || fail "consent URL lacks the read-only scope"
if grep -q "auth%2Fwebmasters&" <<<"$url"; then fail "full-access scope requested"; fi

echo; echo "docs-smoke (host) OK"

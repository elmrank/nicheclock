#!/bin/sh
# nicheclock container entrypoint.
#   start        (default) serve out/ on :8080 and run `nicheclock run` every NICHECLOCK_INTERVAL_HOURS
#   healthcheck  exit 0 when the config is valid, the report server answers and the last run is recent
#   anything else is passed to nicheclock: `init https://...`, `run`, `doctor`, `mcp`, `connect gsc ...`
set -eu

CONFIG="${NICHECLOCK_CONFIG:-/nicheclock/nicheclock.yaml}"
STATE="$(dirname "$CONFIG")"
STAMP="$STATE/.last-run"
HOURS="${NICHECLOCK_INTERVAL_HOURS:-24}"
PORT="${NICHECLOCK_PORT:-8080}"
case "$HOURS" in ''|*[!0-9]*|0) echo "NICHECLOCK_INTERVAL_HOURS must be a whole number of hours >= 1" >&2; exit 2;; esac
INTERVAL=$((HOURS * 3600))

stamp_age() { # seconds since the last run started, or a huge number when there was none
  if [ -f "$STAMP" ]; then echo $(( $(date +%s) - $(stat -c %Y "$STAMP") )); else echo 999999999; fi
}

case "${1:-start}" in
healthcheck)
  nicheclock doctor --offline >/dev/null
  # any HTTP answer counts: before the first run finishes there is no index.html and `/` is a 404
  python - "$PORT" <<'PY'
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=5)
except urllib.error.HTTPError:
    pass
PY
  # a run is overdue when none started in two intervals plus an hour of grace
  [ "$(stamp_age)" -le $((INTERVAL * 2 + 3600)) ]
  ;;
start)
  while [ ! -f "$CONFIG" ]; do
    echo "no $CONFIG yet. Create it with:"
    echo "  docker compose run --rm nicheclock init https://your-site.example --user-agent 'name/1.0 (+mailto:you@your-site.example)' --contact-email you@your-site.example"
    sleep 30
  done
  nicheclock serve --host 0.0.0.0 --port "$PORT" &
  SERVER=$!
  RUNNER=""
  stop() { kill $SERVER $RUNNER 2>/dev/null || true; wait 2>/dev/null || true; exit 0; }
  trap stop TERM INT
  while :; do
    kill -0 $SERVER 2>/dev/null || { echo "report server stopped; exiting" >&2; exit 1; }
    if [ -z "$RUNNER" ] && [ "$(stamp_age)" -ge "$INTERVAL" ]; then
      touch "$STAMP"
      echo "[$(date -u +%FT%TZ)] nicheclock run"
      nicheclock run &
      RUNNER=$!
    fi
    if [ -n "$RUNNER" ] && ! kill -0 $RUNNER 2>/dev/null; then
      rc=0; wait $RUNNER || rc=$?
      echo "[$(date -u +%FT%TZ)] run finished, exit $rc (1 = some sources failed; the reports are still written)"
      RUNNER=""
    fi
    sleep 15 &
    wait $! || true
  done
  ;;
*)
  exec nicheclock "$@"
  ;;
esac

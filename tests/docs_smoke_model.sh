#!/usr/bin/env bash
# docs-smoke (model): no-model fixture run, then the same run judged by a fake Ollama server.
#   pip install -e '.[test,gsc,mcp]' first. No network: sources and the model are both faked.
set -euo pipefail
cd "$(dirname "$0")/.."
WORK="$(mktemp -d)"
PORT="${FAKE_OLLAMA_PORT:-11500}"
PID=""
cleanup() { [ -n "$PID" ] && kill "$PID" 2>/dev/null || true; rm -rf "$WORK"; }
trap cleanup EXIT
step() { printf '\n== %s\n' "$*"; }
fail() { echo "docs-smoke (model) FAILED: $*" >&2; exit 1; }

step "no model: fixture run writes the reports and nothing about a model"
python tests/docs_fixture_run.py "$WORK/plain"
for f in feed.json feed.md feed.xml index.html; do [ -s "$WORK/plain/out/example/$f" ] || fail "missing $f"; done
python - "$WORK/plain/out/example/feed.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["items"] and "llm_fit" not in d["items"][0] and not d.get("llm"), "a model left traces in a no-model run"
PY

step "with a model: fake Ollama on :$PORT"
python tests/fake_ollama.py "$PORT" &
PID=$!
for _ in $(seq 1 20); do (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null && break; sleep 0.5; done
python tests/docs_fixture_run.py "$WORK/model" --llm-url "http://127.0.0.1:$PORT" | tee "$WORK/run.txt"
grep -q "llm ollama/fake: 3 calls" "$WORK/run.txt" || fail "run did not report the model usage line"
python - "$WORK/model/out/example/feed.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
top = d["items"][0]
assert top["llm_fit"] == 85 and top["llm_reason"] and top["rule_score"] != top["score"], top
assert d["llm"]["provider"] == "ollama" and d["llm"]["calls"] == 3, d["llm"]
PY
C=(nicheclock -c "$WORK/model/nicheclock.yaml")
"${C[@]}" llm test
"${C[@]}" llm usage | tee "$WORK/usage.txt"
grep -q "fit" "$WORK/usage.txt" || fail "llm usage shows no fit calls"
"${C[@]}" eval export --project example --out "$WORK/eval.csv"
[ -s "$WORK/eval.csv" ] || fail "no eval csv"

echo; echo "docs-smoke (model) OK"

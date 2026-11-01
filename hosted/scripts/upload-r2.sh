#!/usr/bin/env bash
# Upload an exported R2 layout with wrangler (needs CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID in the environment).
#   scripts/upload-r2.sh ./hosted-out [bucket]
# Equivalent to: python -m nicheclock.hosted_export ... --print-upload <bucket> | sh
set -euo pipefail
dir="${1:?usage: upload-r2.sh <export-dir> [bucket]}"
bucket="${2:-nicheclock-data}"
here="$(cd "$(dirname "$0")/.." && pwd)"
wrangler() { (cd "$here" && node_modules/.bin/wrangler "$@" </dev/null); }   # the project's own wrangler, whatever the cwd
dir="$(cd "$dir" && pwd)"
cd "$dir"
# niches.json last: a niche only appears in the listing once its files are in place
find . -name '*.json' ! -name niches.json -type f | sort | while read -r f; do
  wrangler r2 object put "$bucket/${f#./}" --file "$dir/${f#./}" --content-type application/json --remote
done
find . -name niches.json -type f | sort | while read -r f; do
  wrangler r2 object put "$bucket/${f#./}" --file "$dir/${f#./}" --content-type application/json --remote
done

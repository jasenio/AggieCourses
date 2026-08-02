#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-http://127.0.0.1}"

curl --fail --silent --show-error "$base_url/healthz" >/dev/null
ready_payload="$(curl --fail --silent --show-error "$base_url/readyz")"
grep -q '"paradedb":"ok"' <<< "$ready_payload"
search_payload="$(curl --fail --silent --show-error "$base_url/search?q=accounting&limit=3")"
grep -q '"results"' <<< "$search_payload"

echo "Deployment checks passed at $base_url"

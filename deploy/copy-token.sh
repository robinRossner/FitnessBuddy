#!/usr/bin/env bash
# Copy API_TOKEN from backend/.env.production to the macOS clipboard without printing it.
# Paste on the iPhone via Universal Clipboard (same Apple ID, Bluetooth + Wi-Fi on).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVF="$ROOT/backend/.env.production"

[[ -f "$ENVF" ]] || { echo "error: $ENVF not found; run deploy/make-prod-env.sh first" >&2; exit 1; }
command -v pbcopy >/dev/null || { echo "error: pbcopy not found (macOS only)" >&2; exit 1; }

token="$(grep -E '^(export[[:space:]]+)?API_TOKEN=' "$ENVF" | tail -n1 | sed -E 's/^(export[[:space:]]+)?API_TOKEN=//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/' || true)"
[[ -n "$token" ]] || { echo "error: no API_TOKEN in $ENVF" >&2; exit 1; }

printf '%s' "$token" | pbcopy
echo "API_TOKEN copied to clipboard (${#token} chars). Paste it into the iPhone app now;"
echo "clear the clipboard afterwards with:  printf '' | pbcopy"

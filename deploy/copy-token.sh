#!/usr/bin/env bash
# Copy an app's bearer token from backend/.env.production to the macOS clipboard without printing it.
#   deploy/copy-token.sh              fitness: API_TOKEN (default; paste on the iPhone via
#                                     Universal Clipboard, same Apple ID, Bluetooth + Wi-Fi on)
#   deploy/copy-token.sh receipts     RECEIPTS_API_TOKEN (for the Receipts app)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVF="$ROOT/backend/.env.production"

case "${1:-fitness}" in
  fitness) key=API_TOKEN ;;
  receipts) key=RECEIPTS_API_TOKEN ;;
  *) echo "usage: $0 [fitness|receipts]" >&2; exit 2 ;;
esac

[[ -f "$ENVF" ]] || { echo "error: $ENVF not found; run deploy/make-prod-env.sh first" >&2; exit 1; }
command -v pbcopy >/dev/null || { echo "error: pbcopy not found (macOS only)" >&2; exit 1; }

token="$(grep -E "^(export[[:space:]]+)?$key=" "$ENVF" | tail -n1 | sed -E "s/^(export[[:space:]]+)?$key=//; s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true)"
[[ -n "$token" ]] || { echo "error: no $key in $ENVF" >&2; exit 1; }

printf '%s' "$token" | pbcopy
echo "$key copied to clipboard (${#token} chars). Paste it into the app now;"
echo "clear the clipboard afterwards with:  printf '' | pbcopy"

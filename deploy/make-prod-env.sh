#!/usr/bin/env bash
# Create backend/.env.production from backend/.env and apply production overrides.
# Safe to re-run: existing API_TOKEN (fitness) and RECEIPTS_API_TOKEN in .env.production are kept;
# other keys are re-synced from backend/.env. Prints key NAMES only, never values.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/backend/.env"
DST="$ROOT/backend/.env.production"

[[ -f "$SRC" ]] || { echo "error: $SRC not found (create it first, see backend/.env.example)" >&2; exit 1; }
command -v openssl >/dev/null || { echo "error: openssl not found" >&2; exit 1; }

# Keys this script owns (removed from the copy, then appended with the production value).
OVERRIDE_KEYS="API_TOKEN RECEIPTS_API_TOKEN DOCS_ENABLED SCHEDULER_ENABLED ENGINE_AUTO_RUN DATABASE_URL USER_TZ"

# Value of KEY in .env.production (last assignment, quotes stripped); empty if absent.
existing_value() {
  [[ -f "$DST" ]] || return 0
  grep -E "^(export[[:space:]]+)?$1=" "$DST" | tail -n1 \
    | sed -E "s/^(export[[:space:]]+)?$1=//; s/^\"(.*)\"\$/\1/; s/^'(.*)'\$/\1/" || true
}

# Keep existing tokens so a re-run does not log the iPhone out (or the Receipts app).
api_token="$(existing_value API_TOKEN)"
receipts_token="$(existing_value RECEIPTS_API_TOKEN)"
api_token_kept=1
receipts_token_kept=1
if [[ -z "$api_token" ]]; then
  api_token="$(openssl rand -hex 32)"
  api_token_kept=0
fi
if [[ -z "$receipts_token" ]]; then
  receipts_token="$(openssl rand -hex 32)"
  receipts_token_kept=0
fi

umask 077
tmp="$(mktemp "$ROOT/backend/.env.production.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

pattern="^(export[[:space:]]+)?($(echo "$OVERRIDE_KEYS" | tr ' ' '|'))[[:space:]]*="
grep -Ev "$pattern" "$SRC" > "$tmp" || true
# make sure the copy ends with a newline before appending
[[ ! -s "$tmp" || -z "$(tail -c1 "$tmp")" ]] || echo >> "$tmp"
# Keys set only in .env.production (e.g. CLAUDE_CODE_OAUTH_TOKEN) must survive a re-run.
if [[ -f "$DST" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*= ]] || continue
    key="${BASH_REMATCH[2]}"
    [[ " $OVERRIDE_KEYS " == *" $key "* ]] && continue
    grep -Eq "^(export[[:space:]]+)?$key[[:space:]]*=" "$SRC" || printf '%s\n' "$line" >> "$tmp"
  done < "$DST"
fi
{
  echo "# --- production overrides (deploy/make-prod-env.sh) ---"
  echo "API_TOKEN=$api_token"
  echo "RECEIPTS_API_TOKEN=$receipts_token"
  echo "DOCS_ENABLED=false"
  echo "SCHEDULER_ENABLED=true"
  echo "ENGINE_AUTO_RUN=true"
  echo "DATABASE_URL=sqlite:////app/var/fitness.db"
  echo "USER_TZ=Europe/Vienna"
} >> "$tmp"

chmod 600 "$tmp"
mv -f "$tmp" "$DST"
trap - EXIT
chmod 600 "$DST"

echo "wrote backend/.env.production (mode 600)"
echo "keys set/overridden:"
for k in $OVERRIDE_KEYS; do echo "  $k"; done
if [[ $api_token_kept -eq 1 ]]; then echo "API_TOKEN: kept existing"; else echo "API_TOKEN: newly generated"; fi
if [[ $receipts_token_kept -eq 1 ]]; then
  echo "RECEIPTS_API_TOKEN: kept existing"
else
  echo "RECEIPTS_API_TOKEN: newly generated"
fi
echo "other keys copied from backend/.env:"
grep -E '^(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$SRC" | sed -E 's/^(export[[:space:]]+)?//; s/=.*//' \
  | grep -Evx "$(echo "$OVERRIDE_KEYS" | tr ' ' '|')" | sed 's/^/  /' || true

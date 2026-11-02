#!/usr/bin/env bash
# Run only the backend test groups affected by your changes (see scripts/test-groups.sh).
#
#   scripts/test-changed.sh [BASE] [pytest args...]      # or: BASE=origin/main make test-changed
#
# Changed files = commits since the merge base with BASE (default: origin/main if it exists, else
# HEAD~1) + uncommitted changes (staged and unstaged) + untracked files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

base="${BASE:-}"
if [[ $# -gt 0 && "$1" != -* ]]; then base="$1"; shift; fi
if [[ -z "$base" ]]; then
  if git rev-parse --verify -q origin/main >/dev/null; then
    base=origin/main
  elif git rev-parse --verify -q HEAD~1 >/dev/null; then
    base=HEAD~1
  fi
fi

changed="$(
  {
    if [[ -n "$base" ]]; then git diff --name-only "$base"...HEAD; fi
    git diff --name-only HEAD
    git ls-files --others --exclude-standard
  } | sort -u
)"

groups="$(printf '%s\n' "$changed" | "$ROOT/scripts/test-groups.sh")"
echo "base: ${base:-<none>}"
echo "groups: ${groups:-<none>}"

if [[ -z "$groups" ]]; then
  echo "no backend tests affected"
  exit 0
fi

cd "$ROOT/backend"
if [[ "$groups" == all ]]; then
  exec uv run pytest "$@"
fi

dirs=()
for g in $groups; do
  if [[ -d "tests/$g" ]]; then
    dirs+=("tests/$g")
  else
    echo "tests/$g does not exist yet; running the full suite" >&2
    exec uv run pytest "$@"
  fi
done
exec uv run pytest "${dirs[@]}" "$@"

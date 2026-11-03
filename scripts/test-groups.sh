#!/usr/bin/env bash
# Map changed paths to the backend test groups that must run. Single source of truth for
# `make test-changed` (scripts/test-changed.sh) and the CI matrix (.github/workflows/backend.yml).
#
#   scripts/test-groups.sh path...          # paths as arguments
#   git diff --name-only main | scripts/test-groups.sh
#
# Prints the affected groups space-separated in a fixed order
# (engine advisor garmin importers api gyms sync analytics withings), `all` when a shared file changed,
# or an empty line when no backend test is affected. Paths are repo-relative.
set -euo pipefail

GROUPS_ORDER="engine advisor garmin importers api gyms sync analytics withings"
hit=" "
all=0

add() {
  local g
  for g in "$@"; do
    case "$hit" in *" $g "*) ;; *) hit="$hit$g " ;; esac
  done
}

classify() {
  local p="${1#./}"
  case "$p" in
    "" | \#*) return ;;
  esac
  case "$p" in
    backend/apps/fitness/engine/*) add engine analytics advisor ;;
    backend/apps/fitness/advisor/*) add advisor ;;
    backend/apps/fitness/garmin/*) add garmin sync ;;
    backend/apps/fitness/withings/* | backend/apps/fitness/jobs/withings_jobs.py) add withings ;;
    backend/apps/fitness/importers/*) add importers ;;
    backend/apps/fitness/analytics/* | backend/apps/fitness/stats.py | \
      backend/apps/fitness/bodyweight/*)
      add analytics ;;
    backend/apps/fitness/gyms/*) add gyms ;;
    backend/apps/fitness/exercises/*) add api importers ;;
    backend/apps/fitness/sync/*) add sync api ;;
    backend/apps/fitness/api/* | backend/core/* | backend/server.py | backend/apps/fitness/db/* | \
      backend/apps/fitness/alembic/* | backend/apps/fitness/settings.py | \
      backend/apps/fitness/errors.py | \
      backend/apps/fitness/config/* | backend/pyproject.toml | backend/uv.lock | \
      backend/tests/conftest.py | backend/tests/support/*)
      all=1 ;;
    backend/tests/engine/* | backend/tests/advisor/* | backend/tests/garmin/* | \
      backend/tests/importers/* | backend/tests/api/* | backend/tests/gyms/* | \
      backend/tests/sync/* | backend/tests/analytics/* | backend/tests/withings/*)
      p="${p#backend/tests/}"; add "${p%%/*}" ;;
    backend/*) all=1 ;;
    *) ;;
  esac
}

if [[ $# -gt 0 ]]; then
  for path in "$@"; do classify "$path"; done
else
  while IFS= read -r path || [[ -n "$path" ]]; do classify "$path"; done
fi

if [[ $all -eq 1 ]]; then
  echo all
  exit 0
fi
out=""
for g in $GROUPS_ORDER; do
  case "$hit" in *" $g "*) out="${out:+$out }$g" ;; esac
done
echo "$out"

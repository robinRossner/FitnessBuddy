#!/usr/bin/env bash
# Screenpass walk for the iOS app: builds the FitnessBuddyUITests target and runs the Screenpass
# test plan on a dedicated simulator, saving one PNG per screen into $SCREENPASS_DIR.
#
#   SCREENPASS_DIR=/some/dir ios/scripts/screenpass.sh     (default: ios/.screenpass/shots)
#   ONLY=coach,health ios/scripts/screenpass.sh             (only those tabs; default: all)
#
# ONLY is a comma-separated list of tab names (summary, workout, progress, health, coach); each maps to
# -only-testing:FitnessBuddyUITests/TabScreens/test<Name> plus that tab's own class <Name>Screens.
# The dedicated simulator (default "FitnessBuddy Screenpass", iPhone 17 Pro, newest iOS runtime; SCREENPASS_SIM
# picks another name, so parallel runs don't share one) is created on demand, so the user's own simulators are
# never touched. Build products go to ios/.screenpass/DerivedData.
set -euo pipefail

IOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$IOS_DIR/.screenpass"
LOG="$STATE_DIR/last.log"
SIM_NAME="${SCREENPASS_SIM:-FitnessBuddy Screenpass}"
export SCREENPASS_DIR="${SCREENPASS_DIR:-$STATE_DIR/shots}"
mkdir -p "$STATE_DIR" "$SCREENPASS_DIR"
find "$SCREENPASS_DIR" -maxdepth 1 -name '*.png' -delete

# --- dedicated simulator -------------------------------------------------------------------------
UDID="$(xcrun simctl list devices available -j | python3 -c '
import json, sys
name = sys.argv[1]
for devs in json.load(sys.stdin)["devices"].values():
    for d in devs:
        if d["name"] == name:
            print(d["udid"]); raise SystemExit
' "$SIM_NAME")"
if [ -z "$UDID" ]; then
  RUNTIME="$(xcrun simctl list runtimes available -j | python3 -c '
import json, sys
rts = [r for r in json.load(sys.stdin)["runtimes"] if r["identifier"].startswith("com.apple.CoreSimulator.SimRuntime.iOS-")]
rts.sort(key=lambda r: [int(x) for x in r["version"].split(".")])
print(rts[-1]["identifier"])
')"
  UDID="$(xcrun simctl create "$SIM_NAME" com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro "$RUNTIME")"
  echo "screenpass: created simulator $SIM_NAME ($UDID)"
fi
xcrun simctl bootstatus "$UDID" -b >/dev/null 2>&1
xcrun simctl ui "$UDID" appearance light
xcrun simctl status_bar "$UDID" override --time 9:41 --dataNetwork wifi --wifiMode active --wifiBars 3 \
  --cellularMode active --cellularBars 4 --batteryState charged --batteryLevel 100

# --- ONLY filter ---------------------------------------------------------------------------------
ONLY_ARGS=()
if [ -n "${ONLY:-}" ]; then
  IFS=',' read -r -a TABS <<< "$ONLY"
  for tab in "${TABS[@]}"; do
    tab="$(echo "$tab" | tr -d ' ')"
    [ -n "$tab" ] || continue
    first="$(echo "${tab:0:1}" | tr '[:lower:]' '[:upper:]')"
    ONLY_ARGS+=("-only-testing:FitnessBuddyUITests/TabScreens/test${first}${tab:1}"
                "-only-testing:FitnessBuddyUITests/${first}${tab:1}Screens")
  done
fi

# --- build + test --------------------------------------------------------------------------------
cd "$IOS_DIR"
xcodegen generate >/dev/null

XCB=(xcodebuild -project FitnessBuddy.xcodeproj -scheme FitnessBuddy -testPlan Screenpass
     -destination "id=$UDID" -derivedDataPath "$STATE_DIR/DerivedData")

report_failure() {
  echo "screenpass: FAILED during $1 (full log: $LOG)" >&2
  grep -E "Test Case .* failed|error:|\*\* (BUILD|TEST) FAILED|screenpass:" "$LOG" | sed -E "s/^[0-9-]+ [0-9:.]+ xcodebuild\[[0-9:]+\] //" | cut -c1-300 | awk '!seen[$0]++' | head -40 >&2 || true
  exit 1
}

: > "$LOG"
"${XCB[@]}" -quiet build-for-testing >>"$LOG" 2>&1 || report_failure "build-for-testing"
TEST_RUNNER_SCREENPASS_DIR="$SCREENPASS_DIR" "${XCB[@]}" ${ONLY_ARGS[@]+"${ONLY_ARGS[@]}"} \
  test-without-building >>"$LOG" 2>&1 || report_failure "test-without-building"

grep "screenpass: unstable" "$LOG" || true
# A filtered run carries the baseline over for the screens it skipped, so they don't show as "removed"
# (and an `accept` afterwards can't drop them from the baseline).
if [ -n "${ONLY:-}" ] && [ -d "$STATE_DIR/baseline" ]; then
  for base in "$STATE_DIR/baseline"/*.png; do
    [ -e "$base" ] || continue
    [ -e "$SCREENPASS_DIR/$(basename "$base")" ] || cp "$base" "$SCREENPASS_DIR/"
  done
  echo "screenpass: ONLY=$ONLY, skipped screens copied from the baseline (not run)"
fi
echo "screenpass: saved $(find "$SCREENPASS_DIR" -maxdepth 1 -name '*.png' | wc -l | tr -d ' ') screens to $SCREENPASS_DIR"

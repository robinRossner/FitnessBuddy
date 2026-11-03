#!/usr/bin/env bash
# Screenpass walk for the iOS app, saving one PNG per screen into $SCREENPASS_DIR. Two tiers:
# - snapshots (default): the FitnessBuddySnapshots unit-test target (TestPlans/Snapshots.xctestplan) renders every screen
#   inside the hosted app, one fresh demo app state per screen, no relaunch. Fast; no status bar in the pictures.
# - flows: the FitnessBuddyUITests XCUITest target (TestPlans/Screenpass.xctestplan): the navigation walk and the
#   flow-*.png shots, by real taps. Slow.
#
#   SCREENPASS_DIR=/some/dir ios/scripts/screenpass.sh     (default: ios/.screenpass/shots)
#   ONLY=coach,health ios/scripts/screenpass.sh             (snapshots of those tabs only)
#   ONLY=flows ios/scripts/screenpass.sh                    (only the XCUITest flows)
#   ONLY=all ios/scripts/screenpass.sh                      (both tiers)
#
# ONLY is a comma-separated list of tab names (summary, workout, progress, health, coach), `flows` and/or `all`. Screens
# a run skips are copied from the baseline so they don't show as removed (unset ONLY: only the flow-*.png).
# The dedicated simulator (default "FitnessBuddy Screenpass", iPhone 17 Pro, newest iOS runtime; SCREENPASS_SIM
# picks another name, so parallel runs don't share one) is created on demand, so the user's own simulators are
# never touched; the snapshot tier also needs it, as the host of the app. Build products go to ios/.screenpass/DerivedData.
set -euo pipefail

IOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$IOS_DIR/.screenpass"
LOG="$STATE_DIR/last.log"
SIM_NAME="${SCREENPASS_SIM:-FitnessBuddy Screenpass}"
export SCREENPASS_DIR="${SCREENPASS_DIR:-$STATE_DIR/shots}"
mkdir -p "$STATE_DIR" "$SCREENPASS_DIR"
find "$SCREENPASS_DIR" -maxdepth 1 -name '*.png' -delete
rm -rf "$SCREENPASS_DIR/failed"  # what the snapshot tier saw when a screen never became ready

# --- ONLY filter ---------------------------------------------------------------------------------
RUN_SNAPSHOTS=0
RUN_FLOWS=0
TABS=""
if [ -z "${ONLY:-}" ]; then
  RUN_SNAPSHOTS=1
else
  IFS=',' read -r -a PARTS <<< "$ONLY"
  for part in "${PARTS[@]}"; do
    part="$(echo "$part" | tr -d ' ')"
    case "$part" in
      "") ;;
      all) RUN_SNAPSHOTS=1; RUN_FLOWS=1 ;;
      flows) RUN_FLOWS=1 ;;
      summary|workout|progress|health|coach) RUN_SNAPSHOTS=1; TABS="${TABS:+$TABS,}$part" ;;
      *) echo "screenpass: unknown ONLY value '$part' (summary, workout, progress, health, coach, flows, all)" >&2; exit 2 ;;
    esac
  done
  case ",$ONLY," in *,all,*) TABS="" ;; esac
fi

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

# --- build + test --------------------------------------------------------------------------------
cd "$IOS_DIR"
xcodegen generate >/dev/null

xcb() {  # xcb <test plan> <action> [args...]
  local plan="$1"; shift
  xcodebuild -project FitnessBuddy.xcodeproj -scheme FitnessBuddy -testPlan "$plan" \
    -destination "id=$UDID" -derivedDataPath "$STATE_DIR/DerivedData" -parallel-testing-enabled NO "$@"
}

report_failure() {
  echo "screenpass: FAILED during $1 (full log: $LOG)" >&2
  grep -E "Test Case .* failed|error:|\*\* (BUILD|TEST) FAILED|screenpass:" "$LOG" | grep -v "screenpass: shot " | sed -E "s/^[0-9-]+ [0-9:.]+ xcodebuild\[[0-9:]+\] //" | cut -c1-300 | awk '!seen[$0]++' | head -40 >&2 || true
  exit 1
}

# timed <label> <command...>: runs it, logs into $LOG, prints how long it took.
timed() {
  local label="$1"; shift
  local t0=$SECONDS
  "$@" >>"$LOG" 2>&1 || report_failure "$label"
  TIMINGS="${TIMINGS:+$TIMINGS, }$label $((SECONDS - t0))s"
}

: > "$LOG"
TIMINGS=""
if [ "$RUN_SNAPSHOTS" = 1 ]; then
  timed "build snapshots" xcb Snapshots -quiet build-for-testing
  TEST_RUNNER_SCREENPASS_DIR="$SCREENPASS_DIR" TEST_RUNNER_SCREENPASS_ONLY="$TABS" \
    timed "snapshots" xcb Snapshots test-without-building
fi
if [ "$RUN_FLOWS" = 1 ]; then
  timed "build flows" xcb Screenpass -quiet build-for-testing
  TEST_RUNNER_SCREENPASS_DIR="$SCREENPASS_DIR" timed "flows" xcb Screenpass test-without-building
fi
echo "screenpass: $TIMINGS"

grep "screenpass: unstable" "$LOG" || true
# Screens this run skipped are carried over from the baseline, so they don't show as "removed" (and an `accept`
# afterwards can't drop them from the baseline). The plain run only skips the flows.
if [ -d "$STATE_DIR/baseline" ] && { [ "$RUN_SNAPSHOTS" = 0 ] || [ "$RUN_FLOWS" = 0 ] || [ -n "$TABS" ]; }; then
  pattern='*.png'
  [ -z "${ONLY:-}" ] && pattern='flow-*.png'
  for base in "$STATE_DIR/baseline"/$pattern; do
    [ -e "$base" ] || continue
    [ -e "$SCREENPASS_DIR/$(basename "$base")" ] || cp "$base" "$SCREENPASS_DIR/"
  done
  if [ -z "${ONLY:-}" ]; then
    echo "screenpass: flows not run (make ios-flows), any flow-*.png copied from the baseline"
  else
    echo "screenpass: ONLY=$ONLY, skipped screens copied from the baseline (not run)"
  fi
fi
echo "screenpass: saved $(find "$SCREENPASS_DIR" -maxdepth 1 -name '*.png' | wc -l | tr -d ' ') screens to $SCREENPASS_DIR"

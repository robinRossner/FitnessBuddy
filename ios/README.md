# FitnessBuddy iOS

SwiftUI + SwiftData, iOS 18+, Swift 6. Offline-first: logging never touches the network; a sync
engine pushes dirty records and pulls changes when a connection is available.

## Build

```sh
brew install xcodegen
cd ios
cp Config/Local.xcconfig.example Config/Local.xcconfig   # put your DEVELOPMENT_TEAM in it (gitignored)
xcodegen generate                                        # project.yml is the source of truth
open FitnessBuddy.xcodeproj
```

The generated `FitnessBuddy.xcodeproj` is gitignored; re-run `xcodegen generate` after pulling or adding files.
Signing is automatic; with an empty `DEVELOPMENT_TEAM` the simulator build still works.

Tests (from the repo root; `make` runs `xcodegen generate` first, pipes through `xcbeautify` if
installed, `SIM="iPhone 16"` picks another simulator):

```sh
make ios-test-fast    # TestPlans/Fast.xctestplan: the quick subset, run on every PR in CI
make ios-test         # TestPlans/Full.xctestplan: everything, run on main in CI
make ios-build        # Debug build only
```

Plain `xcodebuild`: `cd ios && xcodebuild -scheme FitnessBuddy -testPlan Fast -destination
'platform=iOS Simulator,name=iPhone 17 Pro' test`. CI (`.github/workflows/ios.yml`) picks whatever
iPhone simulator the runner has.

## App icon

The icon ("Pulse Lift": a dumbbell whose handle is a heartbeat trace, on a teal field) is drawn by
`scripts/make_app_icon.py`; the PNGs and `Contents.json` in `Resources/Assets.xcassets/AppIcon.appiconset` are its output
(iOS 18 single-size 1024 px: light, dark = transparent background, tinted = grayscale). Regenerate after changing it:

```sh
uv run --with pillow python ios/scripts/make_app_icon.py --preview   # --preview also writes docs/screenshots/app-icon-preview.png
```

## First run

Settings (gear on Today): backend URL (`http://<lan-ip>:8000` is allowed for local networks, anything else needs
https), API token (Keychain), current gym (after the first sync), "Test connection". Debug builds also have
Settings > Debug > "Load demo data" (bundled sample JSON, pins "today" to the sample week's Monday).

## Layout

- `FitnessBuddy/Core/API`: `APIClient` (all paths in `APIClient.Endpoints`), `DTOs`, snake_case + tolerant ISO-8601 coding.
- `Core/Persistence`: SwiftData models (`LocalSession/LocalSet/LocalBodyMeasurement` are synced, `Cached*` are server-owned).
- `Core/Sync`: `Outbox` (collect/apply push results), `SyncMerge` (LWW pull merge), `PlanCache`, `SyncEngine` (actor),
  `SyncCoordinator` (triggers, backoff, status), `NetworkMonitor`.
- `Core/Security`: Keychain wrapper, `SettingsStore`. `Core/Design`: theme, components.
- `Features/{Today,Workout,Progress,Health,Coach,Settings}`; `Preview/` has DEBUG-only demo data.
- `FitnessBuddyTests`: Swift Testing; fixtures are read straight from `docs/api/examples/*.json` (bundled as `examples/`), with `ContractTests` decoding every file. Test plans `Fast` (tag `fast`) and `Full` live in `TestPlans/`.

## Sync rules (short)

Every local edit calls `touch()` (updatedAt = now, dirty = true). Push sends dirty, performed sets, their sessions and body
measurements; `applied`/`stale` clear `dirty` (unless edited again meanwhile), `rejected` keeps it flagged and skips it until
edited. Pull merges per record by `updated_at` (newer server wins, a dirty local record is never overwritten by an
equal/older server copy, tombstones are applied). Unchecked planned sets are local only and never pushed.

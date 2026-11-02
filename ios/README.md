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

Tests. The fastest loop is the package, on macOS, no simulator and no app host:

```sh
cd ios/FitnessCore && swift test   # FitnessCoreTests: DTO decoding, the contract check, logic, transforms (~10 s cold)
```

The app's own tests (and the package tests again, on the simulator) run through the test plans, from the repo
root (`make` runs `xcodegen generate` first, pipes through `xcbeautify` if installed, `SIM="iPhone 16"` picks
another simulator):

```sh
make ios-test-fast    # TestPlans/Fast.xctestplan: FitnessCoreTests + app tests tagged `fast`; every PR in CI
make ios-test         # TestPlans/Full.xctestplan: FitnessCoreTests + every app test; main in CI
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

- `FitnessCore/` (local Swift package, iOS 18 + macOS 15, Foundation only: no SwiftUI / SwiftData / UIKit):
  - `API`: `APIClient` (all paths in `APIClient.Endpoints`) behind the `APITransport` protocol (typed calls in its
    extension, `ClosureTransport` for the demo and fakes), `DTOs`, `StatsDTOs`, snake_case + tolerant ISO-8601 coding,
    Garmin sync DTOs and feedback logic.
  - `Coach`, `Gyms`, `Stats`: DTOs, endpoints, errors, pure logic, transforms, the raw-JSON `DiskStatsCache`.
  - `Today`, `Workout`, `Progress`, `Health`: the view models' pure rules (`SummaryLogic`, `WeekRunsModel`,
    `WorkoutChooserLogic`, `TemplateChange`, `RestTime`, ...). `Sync/SyncThrottle`, `Support` (clock, formatting,
    `Domain` / `ReadinessLevel` without their colours).
  - `Tests/FitnessCoreTests`: everything pure, incl. `ContractTests`; fixtures come from `docs/api/examples` through the
    `Resources/examples` symlink (processed flat into the test bundle).
- `FitnessBuddy/App`: `AppEnvironment` (composition root + tab/navigation state; `testing:` is set once by
  `FitnessBuddyApp`), `WorkoutFlow` (start / finish), `BackendResetController` (backend change, reset, wipe).
- `Core/API/RemoteRepository`: base of `StatsRepository`, `CoachRepository`, `GymRepository` (namespace, demo flag,
  transport, cache, `canWrite`, off-main decoding).
- `Core/Persistence`: SwiftData models (`LocalSession/LocalSet/LocalBodyMeasurement` are synced, `Cached*` are server-owned).
- `Core/Sync`: `Outbox` (collect/apply push results), `SyncMerge` (LWW pull merge), `PlanCache`, `SyncEngine` (actor),
  `SyncCoordinator` (triggers, backoff, foreground throttle, status), `NetworkMonitor`. These work on SwiftData models,
  so they stay in the app.
- `Core/Security`: Keychain wrapper, `SettingsStore`. `Core/Design`: theme (colours of `Domain` etc.), components,
  `LoadableContent` (the one loading / empty / error / offline placeholder), `Memo` (memoised transforms in views).
- `Features/{Today,Workout,Progress,Health,Coach,Settings}`; `Preview/` has DEBUG-only demo data.
- `FitnessBuddyTests`: Swift Testing, hosted in the app: what needs SwiftData, SwiftUI, the repositories or the
  environment. `FakeTransport` stands in for the backend; fixtures from `docs/api/examples` (bundled as `examples/`).
  Test plans `Fast` (package tests + tag `fast`) and `Full` live in `TestPlans/`.

## Sync rules (short)

Every local edit calls `touch()` (updatedAt = now, dirty = true). Push sends dirty, performed sets, their sessions and body
measurements; `applied`/`stale` clear `dirty` (unless edited again meanwhile), `rejected` keeps it flagged and skips it until
edited. Pull merges per record by `updated_at` (newer server wins, a dirty local record is never overwritten by an
equal/older server copy, tombstones are applied). Unchecked planned sets are local only and never pushed.

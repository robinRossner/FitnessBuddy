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

```sh
xcodebuild -scheme FitnessBuddy -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build test
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
- `FitnessBuddyTests`: Swift Testing; fixtures are copies of `docs/api/examples/*.json`.

## Sync rules (short)

Every local edit calls `touch()` (updatedAt = now, dirty = true). Push sends dirty, performed sets, their sessions and body
measurements; `applied`/`stale` clear `dirty` (unless edited again meanwhile), `rejected` keeps it flagged and skips it until
edited. Pull merges per record by `updated_at` (newer server wins, a dirty local record is never overwritten by an
equal/older server copy, tombstones are applied). Unchecked planned sets are local only and never pushed.

# UI Lab

A separate XcodeGen project for trying UI ideas with hardcoded data, without touching the real app.
Nothing here imports `ios/FitnessBuddy/`, and `ios/project.yml` doesn't know about it.

```
cd ios/UILab && xcodegen generate
xcodebuild -project FitnessBuddyUILab.xcodeproj -scheme LabSandbox \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
```

## Layout
- `Shared/`: compiled into every target.
  - `LabData.swift`: placeholder data and models (runs, workouts with sets, lifts with e1RM history,
    templates, metrics, coach entries) plus `LabFormat`.
  - `LabInsightData.swift` / `LabInsights.swift`: example correlations and `LabInsightCard`,
    `LabInsightRow`, `LabInsightsList`, `LabStrengthMeter`.
  - `LabKit.swift`: `MetricText`, `Sparkline`, `InfoButton`, `TrendBadge`, `MetricDetailView` (W/M/6M/Y).
    Its header notes the Swift Charts rule: `BarMark` with `width: .ratio` needs a String x value.
- `Sandbox/`: the one target kept as a starting point.
- `project.yml`: one target per experiment.

## Adding an experiment
1. Copy `Sandbox/` to a new folder (e.g. `F_Idea/`), rename the `@main` struct, prefix types with the letter.
2. Add a target in `project.yml` with `sources: [Shared, F_Idea]`, a bundle ID and a display name.
   With the free signing team, reuse an already-registered ID (`com.robinrossner.FitnessBuddyLabA`…`LabE`)
   to stay under the App ID limit; the phone holds at most three free-team apps (the real app included).
3. `xcodegen generate`, build, and install on the phone with `-allowProvisioningUpdates`.

## History
October 2026: variants A (polish), B (Apple Health style), C (Apple Fitness style), D (4 tabs) and
E (5 tabs) were compared. E won, with D's compact coach card and settings, and went into the real app.
A–C are in git history on branch `claude/sweet-heisenberg-qe1e2y`; D and E were never committed.

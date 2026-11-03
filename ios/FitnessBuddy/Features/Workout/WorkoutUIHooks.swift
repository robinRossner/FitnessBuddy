import SwiftUI

extension View {
    /// Sheets of the screenshot pass hide the home indicator: it fades on its own clock, so pictures would differ by
    /// timing. Only under `-uiNoAnimations` (DEBUG); otherwise nothing changes.
    func stableForScreenshots() -> some View {
        #if DEBUG
        persistentSystemOverlays(UITestSupport.animationsDisabled ? .hidden : .automatic)
        #else
        self
        #endif
    }
}

#if DEBUG
import FitnessCore
import SwiftData
import SwiftUI

/// Launch arguments of the Workout tab's screenshot pass (DEBUG only). All of them are honoured ONLY with `-uiTab workout`:
/// - `-uiRoute plan|detail`: pushes the next planned session's plan preview / the most recent finished workout.
/// - `-uiSheet chooser`: the "Start workout" chooser.
/// - `-uiWorkout active`: starts the next planned workout 23 min before the frozen clock, logs three sets and runs a
///   rest of 90 s; combine with `-uiSheet rest|picker|newExercise|exerciseSetup|reorder|edit` for the sheets over it.
/// - `-uiWorkout summary`: starts the same workout, logs its first two exercises and finishes it (summary sheet).
enum WorkoutUIHooks {
    private static var defaults: UserDefaults { .standard }
    nonisolated(unsafe) private static var consumed: Set<String> = []

    /// Forgets which requests ran, as on a fresh launch (the in-process snapshot pass mounts the app once per screen).
    static func resetConsumed() { consumed = [] }

    private static var enabled: Bool { defaults.string(forKey: "uiTab") == "workout" }
    static var mode: String? { enabled ? defaults.string(forKey: "uiWorkout") : nil }
    static var sheet: String? { enabled ? defaults.string(forKey: "uiSheet") : nil }
    static var route: String? { enabled ? defaults.string(forKey: "uiRoute") : nil }

    /// The `-uiSheet` value if it is one of `names`, once per launch for each `slot`.
    static func takeSheet(_ names: Set<String>, slot: String) -> String? {
        guard let sheet, names.contains(sheet), consumed.insert(slot).inserted else { return nil }
        return sheet
    }

    /// Opens the requested sheet of `slot` once, after `delay` (the sheet under it settles first). A view that goes
    /// away before then (SwiftUI can build a sheet's content twice and cancel the first `.task`) leaves the request to
    /// the view that stays.
    @MainActor
    static func openSheet(_ names: Set<String>, slot: String, after delay: Duration = .milliseconds(400),
                          _ open: (String) -> Void) async {
        guard let sheet, names.contains(sheet), !consumed.contains(slot) else { return }
        try? await Task.sleep(for: delay)
        guard !Task.isCancelled, let name = takeSheet(names, slot: slot) else { return }
        open(name)
    }

    /// Runs the launch request that changes data or navigation of the idle screen, once. Returns the routes to push.
    @MainActor
    static func prepare(_ env: AppEnvironment) async -> [WorkoutRoute] {
        guard enabled, consumed.insert("prepare").inserted else { return [] }
        await waitForDemoPlan(env)
        if let mode, mode == "active" || mode == "summary" { startWorkout(env, finish: mode == "summary") }
        if sheet == "chooser" { env.workoutFlow.showChooser = true }
        switch route {
        case "plan":
            if let row = nextPlanned(env) { return [.plan(row.id)] }
        case "detail":
            let finished = FetchDescriptor<LocalSession>(predicate: #Predicate { $0.endedAt != nil && $0.deletedAt == nil },
                                                         sortBy: [SortDescriptor(\.startedAt, order: .reverse)])
            if let session = (try? env.context.fetch(finished))?.first { return [.detail(session.id)] }
        default: break
        }
        return []
    }

    @MainActor
    private static func nextPlanned(_ env: AppEnvironment) -> CachedPlannedSession? {
        for choice in WorkoutChooserLogic.choices(in: env.context) {
            guard case .planned(let id) = choice.source else { continue }
            return try? env.context.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first
        }
        return nil
    }

    /// The demo data loads in `AppEnvironment.start()`, which can run after this tab appeared.
    @MainActor
    private static func waitForDemoPlan(_ env: AppEnvironment) async {
        for _ in 0..<80 where nextPlanned(env) == nil { try? await Task.sleep(for: .milliseconds(250)) }
    }

    @MainActor
    private static func startWorkout(_ env: AppEnvironment, finish: Bool) {
        guard let row = nextPlanned(env) else { return }
        env.workoutFlow.start(from: row)
        let service = env.workout
        guard let session = service.activeSession() else { return }
        session.startedAt = AppClock.instant.addingTimeInterval(-23 * 60)
        service.save()
        let all = service.sets(of: session)
        let orders = Set(all.map(\.exerciseOrder)).sorted()
        var lastTicked: LocalSet?
        for set in all {
            let take = finish ? orders.prefix(2).contains(set.exerciseOrder) : set.exerciseOrder == orders.first && set.setOrder <= 3
            if take, service.toggleComplete(set) { lastTicked = set }
        }
        if finish {
            env.workoutFlow.finish(session)
        } else if let lastTicked {
            env.restTimer.start(seconds: 90, variantId: lastTicked.variantId)
        }
    }
}
#endif

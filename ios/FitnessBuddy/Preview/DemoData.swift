#if DEBUG
import Foundation
import SwiftData

/// Bundled sample JSON (shaped exactly like the backend responses, trimmed + anonymised).
enum Samples {
    static func data(_ name: String) -> Data {
        guard let url = Bundle.main.url(forResource: name, withExtension: "json"),
              let data = try? Data(contentsOf: url) else { fatalError("Missing sample \(name).json") }
        return data
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) -> T {
        do { return try APICoding.makeDecoder().decode(T.self, from: data(name)) }
        catch { fatalError("Sample \(name) does not decode: \(error)") }
    }
}

/// DEBUG-only: fills the local store as if a sync had happened, so the UI can be exercised without a backend.
enum DemoData {
    /// The sample week is re-dated to the current week; "today" is pinned to its Monday (an Upper day, yellow readiness).
    @MainActor
    static func load(into ctx: ModelContext, settings: SettingsStore?) {
        var pull = Samples.decode(PullResponse.self, "sync_pull")
        pull.plannedSessions = []
        _ = try? SyncMerge.apply(pull, in: ctx)

        let days = AppClock.weekDays(containing: Date())
        var week = Samples.decode(PlanWeekDTO.self, "plan_week")
        week.days = week.days.enumerated().map { i, day in
            var d = day
            d.date = days[i]
            d.sessions = day.sessions.map { var s = $0; s.date = days[i]; return s }
            return d
        }
        week.start = days[0]
        PlanCache.store(week: week, in: ctx)

        var today = Samples.decode(PlanTodayDTO.self, "plan_today")
        today.date = days[0]
        today.sessions = []  // already stored via the week
        PlanCache.store(today: today, in: ctx)

        UserDefaults.standard.set(days[0], forKey: AppClock.overrideKey)
        settings?.currentGymId = 3
        try? ctx.save()
    }

    @MainActor
    static func clear(in ctx: ModelContext) {
        try? ctx.delete(model: LocalSet.self)
        try? ctx.delete(model: LocalSession.self)
        try? ctx.delete(model: LocalBodyMeasurement.self)
        try? ctx.delete(model: CachedPlannedSession.self)
        try? ctx.delete(model: CachedVariant.self)
        try? ctx.delete(model: CachedExercise.self)
        try? ctx.delete(model: CachedGym.self)
        let state = SyncState.current(in: ctx)
        state.cursor = nil
        state.readinessData = nil
        state.configData = nil
        UserDefaults.standard.removeObject(forKey: AppClock.overrideKey)
        try? ctx.save()
    }
}

enum PreviewData {
    /// In-memory environment with demo data loaded (no network, no Keychain).
    @MainActor
    static func environment(startWorkout: Bool = false) -> AppEnvironment {
        let container = try! Persistence.makeContainer(inMemory: true)
        let settings = SettingsStore(defaults: UserDefaults(suiteName: "preview") ?? .standard, loadToken: false)
        let env = AppEnvironment(container: container, settings: settings)
        env.sync.enabled = false
        DemoData.load(into: container.mainContext, settings: settings)
        if startWorkout {
            let rows = (try? container.mainContext.fetch(FetchDescriptor<CachedPlannedSession>())) ?? []
            if let row = rows.first(where: { $0.date == AppClock.todayString && $0.kind == "upper" }) {
                env.startWorkout(from: row)
            }
        }
        return env
    }
}
#endif

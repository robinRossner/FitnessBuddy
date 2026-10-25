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
        // Demo is a separate mode: rows it creates are flagged `isDemo` (never pushed, wiped as soon as a real
        // backend is saved) and sync is paused (`SyncState.demoActive`).
        let knownSessions = Set(((try? ctx.fetch(FetchDescriptor<LocalSession>())) ?? []).map(\.id))
        let knownSets = Set(((try? ctx.fetch(FetchDescriptor<LocalSet>())) ?? []).map(\.id))
        let knownBodies = Set(((try? ctx.fetch(FetchDescriptor<LocalBodyMeasurement>())) ?? []).map(\.id))
        var pull = Samples.decode(PullResponse.self, "sync_pull")
        pull.plannedSessions = []
        _ = try? SyncMerge.apply(pull, in: ctx)
        for s in (try? ctx.fetch(FetchDescriptor<LocalSession>())) ?? [] where !knownSessions.contains(s.id) { s.isDemo = true; s.dirty = false }
        for s in (try? ctx.fetch(FetchDescriptor<LocalSet>())) ?? [] where !knownSets.contains(s.id) { s.isDemo = true; s.dirty = false }
        for b in (try? ctx.fetch(FetchDescriptor<LocalBodyMeasurement>())) ?? [] where !knownBodies.contains(b.id) { b.isDemo = true; b.dirty = false }
        SyncState.current(in: ctx).demoActive = true

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
        let state = SyncState.current(in: ctx)
        state.coachPendingCount = 1
        state.coachLatestPendingId = DemoCoach.pendingId

        UserDefaults.standard.set(days[0], forKey: AppClock.overrideKey)
        settings?.currentGymId = 3
        try? ctx.save()
    }

    /// Removes the demo rows and caches (real unsynced records stay), ends demo mode.
    @MainActor
    static func clear(in ctx: ModelContext) {
        BackendReset.reset(in: ctx, fingerprint: SyncState.current(in: ctx).backendFingerprint)
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

import Foundation
import SwiftData

struct SyncReport: Sendable, Equatable {
    var pushed = 0
    var rejected = 0
    var pulledPages = 0
    var planRefreshed = false

    /// "Pushed 2, pulled 1 page, plan updated"
    var summary: String {
        "Pushed \(pushed), pulled \(pulledPages) page\(pulledPages == 1 ? "" : "s")" + (planRefreshed ? ", plan updated" : ", plan not updated")
            + (rejected > 0 ? ", \(rejected) rejected" : "")
    }
}

/// Push dirty records, pull since the stored cursor, refresh the plan cache.
/// Uses a fresh `ModelContext` per phase so it always sees the latest saved state of the main context
/// and never holds model objects across an `await`.
actor SyncEngine {
    private let container: ModelContainer
    private let maxPushRounds = 10
    private let maxPullPages = 500

    init(container: ModelContainer) {
        self.container = container
    }

    func sync(using client: APIClient) async throws -> SyncReport {
        var report = SyncReport()
        // Demo data is local-only: never talk to a server while it is loaded.
        if SyncState.current(in: ModelContext(container)).demoActive { return report }
        let deviceId = SyncState.current(in: ModelContext(container)).deviceId

        // 1. push (loop: more than one batch may be waiting)
        for _ in 0..<maxPushRounds {
            try Task.checkCancellation()
            let batch = try Outbox.collect(in: ModelContext(container))
            if batch.isEmpty { break }
            let response = try await client.push(PushRequest(deviceId: deviceId, sessions: batch.sessions, sets: batch.sets,
                                                             bodyMeasurements: batch.bodyMeasurements))
            let outcome = try Outbox.apply(results: response.results, batch: batch, in: ModelContext(container))
            report.pushed += outcome.cleared
            report.rejected += outcome.rejected
            if outcome.cleared == 0 { break }  // nothing progressed (all rejected / unknown): don't spin
        }

        // 2. pull (merge + cursor commit per page)
        var cursor = SyncState.current(in: ModelContext(container)).cursor
        for _ in 0..<maxPullPages {
            try Task.checkCancellation()
            let page = try await client.pull(since: cursor)
            let ctx = ModelContext(container)
            try SyncMerge.apply(page, in: ctx)
            if let next = page.cursor, !next.isEmpty {
                SyncState.current(in: ctx).cursor = next
                try ctx.save()
                cursor = next
            }
            report.pulledPages += 1
            if !page.hasMore { break }
        }

        try Task.checkCancellation()

        // 3. plan + readiness for the local plan cache (an error is thrown only if every plan call failed)
        report.planRefreshed = try await refreshPlan(using: client)

        let ctx = ModelContext(container)
        let state = SyncState.current(in: ctx)
        state.lastSyncAt = .now
        state.lastAttemptAt = .now
        state.lastError = nil
        state.lastSummary = report.summary
        try ctx.save()
        return report
    }

    func recordFailure(_ message: String) {
        let ctx = ModelContext(container)
        let state = SyncState.current(in: ctx)
        state.lastError = message
        state.lastAttemptAt = .now
        try? ctx.save()
    }

    /// Returns true if at least one plan call worked. Throws the first error only when none did, so a wrong
    /// contract / token / URL is never swallowed (a single flaky call among working ones is not an error).
    func refreshPlan(using client: APIClient) async throws -> Bool {
        var ok = false
        var firstError: Error?
        let thisWeek = AppClock.weekStart(of: AppClock.now)
        do {
            let week = try await client.planWeek(start: AppClock.dayString(thisWeek))
            PlanCache.store(week: week, in: ModelContext(container))
            ok = true
        } catch { firstError = firstError ?? error }
        // next week, so Sunday evening already shows Monday's plan
        if let next = AppClock.calendar.date(byAdding: .day, value: 7, to: thisWeek) {
            do {
                let week = try await client.planWeek(start: AppClock.dayString(next))
                PlanCache.store(week: week, in: ModelContext(container))
            } catch { firstError = firstError ?? error }
        }
        do {
            let today = try await client.planToday()
            PlanCache.store(today: today, in: ModelContext(container))
            ok = true
        } catch { firstError = firstError ?? error }
        if !ok, let firstError { throw firstError }
        return ok
    }
}

import Foundation
import SwiftData

struct SyncReport: Sendable, Equatable {
    var pushed = 0
    var rejected = 0
    var pulledPages = 0
    var planRefreshed = false
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
        let deviceId = SyncState.current(in: ModelContext(container)).deviceId

        // 1. push (loop: more than one batch may be waiting)
        for _ in 0..<maxPushRounds {
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

        // 3. plan + readiness for the local plan cache (best effort: sync itself already succeeded)
        report.planRefreshed = await refreshPlan(using: client)

        let ctx = ModelContext(container)
        let state = SyncState.current(in: ctx)
        state.lastSyncAt = .now
        state.lastError = nil
        try ctx.save()
        return report
    }

    func recordFailure(_ message: String) {
        let ctx = ModelContext(container)
        SyncState.current(in: ctx).lastError = message
        try? ctx.save()
    }

    func refreshPlan(using client: APIClient) async -> Bool {
        var ok = false
        let thisWeek = AppClock.weekStart(of: AppClock.now)
        if let week = try? await client.planWeek(start: AppClock.dayString(thisWeek)) {
            let ctx = ModelContext(container)
            PlanCache.store(week: week, in: ctx)
            ok = true
        }
        // next week, so Sunday evening already shows Monday's plan
        if let next = AppClock.calendar.date(byAdding: .day, value: 7, to: thisWeek),
           let week = try? await client.planWeek(start: AppClock.dayString(next)) {
            PlanCache.store(week: week, in: ModelContext(container))
        }
        if let today = try? await client.planToday() {
            PlanCache.store(today: today, in: ModelContext(container))
            ok = true
        }
        return ok
    }
}

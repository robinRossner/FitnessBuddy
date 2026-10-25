import Foundation
import SwiftData

/// Writes server plan items into the local cache.
enum PlanCache {
    /// Replace cached rows for every date that the response covers with at least one session.
    /// (Past days the backend no longer reports keep their last cached copy.)
    static func store(week: PlanWeekDTO, in ctx: ModelContext) {
        for day in week.days where !day.sessions.isEmpty {
            let date = day.date
            let existing = (try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.date == date }))) ?? []
            let incomingIds = Set(day.sessions.map(\.cacheId))
            for row in existing where !incomingIds.contains(row.id) { ctx.delete(row) }
            for item in day.sessions { upsert(item, fallbackDate: date, in: ctx) }
        }
        try? ctx.save()
    }

    static func store(today: PlanTodayDTO, in ctx: ModelContext) {
        store(week: PlanWeekDTO(start: today.date, days: [PlanDayDTO(date: today.date, sessions: today.sessions)]), in: ctx)
        let state = SyncState.current(in: ctx)
        if let r = today.readiness, let data = try? APICoding.makeEncoder().encode(r) {
            state.readinessData = data
            state.readinessDate = today.date
        }
        try? ctx.save()
    }

    /// Pull-sync path: LWW by `updatedAt`; a stored item replaces a preview for the same date + kind.
    static func merge(planned: [PlanItemDTO], in ctx: ModelContext) {
        for item in planned {
            let id = item.cacheId
            let local = try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first
            if item.deletedAt != nil {
                if let local { ctx.delete(local) }
                continue
            }
            if let local, let l = local.updatedAt, let s = item.updatedAt, s <= l { continue }
            upsert(item, fallbackDate: item.date, in: ctx)
        }
    }

    private static func upsert(_ item: PlanItemDTO, fallbackDate: String, in ctx: ModelContext) {
        guard let payload = try? APICoding.makeEncoder().encode(item) else { return }
        let id = item.cacheId
        if item.id != nil {
            let date = item.date, kind = item.kind
            let previews = (try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(
                predicate: #Predicate { $0.date == date && $0.kind == kind && $0.isPreview }))) ?? []
            for p in previews { ctx.delete(p) }
        }
        let row = try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == id })).first
        if let row {
            row.date = item.date
            row.kind = item.kind
            row.status = item.status ?? "proposed"
            row.title = item.title ?? item.kind
            row.updatedAt = item.updatedAt
            row.isPreview = item.id == nil
            row.payload = payload
        } else {
            ctx.insert(CachedPlannedSession(id: id, date: item.date, kind: item.kind, status: item.status ?? "proposed",
                                            title: item.title ?? item.kind, updatedAt: item.updatedAt,
                                            isPreview: item.id == nil, payload: payload))
        }
    }
}

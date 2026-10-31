import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

@MainActor
struct SyncMergeTests {
    @Test func newerServerWinsOverCleanLocal() throws {
        let ctx = try makeContext()
        let id = UUID()
        let local = LocalSession(id: id, name: "Old", updatedAt: date("2026-10-26T10:00:00.000Z"), dirty: false)
        ctx.insert(local)
        try SyncMerge.apply(PullResponse(sessions: [sessionDTO(id, updated: "2026-10-26T11:00:00.000Z", name: "Server")]), in: ctx)
        let s = try #require(ctx.fetch(FetchDescriptor<LocalSession>()).first)
        #expect(s.name == "Server")
        #expect(!s.dirty)
    }

    @Test func dirtyLocalWinsOverOlderServer() throws {
        let ctx = try makeContext()
        let id = UUID()
        ctx.insert(LocalSession(id: id, name: "Mine", updatedAt: date("2026-10-26T12:00:00.000Z"), dirty: true))
        let stats = try SyncMerge.apply(PullResponse(sessions: [sessionDTO(id, updated: "2026-10-26T11:00:00.000Z", name: "Server")]), in: ctx)
        let s = try #require(ctx.fetch(FetchDescriptor<LocalSession>()).first)
        #expect(s.name == "Mine")
        #expect(s.dirty)
        #expect(stats.kept == 1)
    }

    @Test func equalTimestampKeepsLocal() {
        let t = date("2026-10-26T12:00:00.000Z")
        #expect(LWW.decide(localUpdatedAt: t, localDirty: true, serverUpdatedAt: t) == .keepLocal)
        #expect(LWW.decide(localUpdatedAt: t, localDirty: false, serverUpdatedAt: t) == .keepLocal)
        #expect(LWW.decide(localUpdatedAt: nil, localDirty: false, serverUpdatedAt: t) == .applyServer)
    }

    @Test func olderServerDoesNotOverwriteCleanLocal() throws {
        let ctx = try makeContext()
        let id = UUID()
        ctx.insert(LocalSession(id: id, name: "Newer", updatedAt: date("2026-10-26T12:00:00.000Z"), dirty: false))
        try SyncMerge.apply(PullResponse(sessions: [sessionDTO(id, updated: "2026-10-26T11:00:00.000Z", name: "Older")]), in: ctx)
        #expect(try ctx.fetch(FetchDescriptor<LocalSession>()).first?.name == "Newer")
    }

    @Test func tombstoneFromServerDeletesLocal() throws {
        let ctx = try makeContext()
        let sid = UUID(), setId = UUID()
        ctx.insert(LocalSet(id: setId, sessionId: sid, variantId: 42, exerciseOrder: 1, setOrder: 1,
                            updatedAt: date("2026-10-26T10:00:00.000Z"), dirty: false))
        try SyncMerge.apply(PullResponse(sets: [setDTO(setId, session: sid, updated: "2026-10-26T11:00:00.000Z",
                                                       deleted: "2026-10-26T11:00:00.000Z")]), in: ctx)
        let set = try #require(ctx.fetch(FetchDescriptor<LocalSet>()).first)
        #expect(set.deletedAt != nil)
    }

    @Test func tombstoneForUnknownRecordIsIgnored() throws {
        let ctx = try makeContext()
        try SyncMerge.apply(PullResponse(sessions: [sessionDTO(UUID(), updated: "2026-10-26T11:00:00.000Z",
                                                               deleted: "2026-10-26T11:00:00.000Z")]), in: ctx)
        #expect(try ctx.fetch(FetchDescriptor<LocalSession>()).isEmpty)
    }

    @Test func dirtyLocalTombstoneBeatsOlderServerCopy() throws {
        let ctx = try makeContext()
        let sid = UUID(), setId = UUID()
        let local = LocalSet(id: setId, sessionId: sid, variantId: 42, exerciseOrder: 1, setOrder: 1,
                             updatedAt: date("2026-10-26T12:00:00.000Z"), deletedAt: date("2026-10-26T12:00:00.000Z"), dirty: true)
        ctx.insert(local)
        try SyncMerge.apply(PullResponse(sets: [setDTO(setId, session: sid, updated: "2026-10-26T11:00:00.000Z")]), in: ctx)
        #expect(try ctx.fetch(FetchDescriptor<LocalSet>()).first?.deletedAt != nil)
    }

    @Test func pageOrderDoesNotMatterAndServerSessionsAreNotActive() throws {
        let ctx = try makeContext()
        let sid = UUID()
        // set listed before its session, session without ended_at (server-written row)
        let pull = PullResponse(sessions: [sessionDTO(sid, updated: "2026-10-26T11:00:00.000Z", ended: nil)],
                                sets: [setDTO(UUID(), session: sid, updated: "2026-10-26T11:00:00.000Z")])
        try SyncMerge.apply(pull, in: ctx)
        let s = try #require(ctx.fetch(FetchDescriptor<LocalSession>()).first)
        #expect(!s.isActive)
        #expect(WorkoutService(context: ctx).activeSession() == nil)
        #expect(try ctx.fetch(FetchDescriptor<LocalSet>()).count == 1)
    }

    @Test func realSnapshotMergesAndCachesCatalogAndPlan() throws {
        let ctx = try makeContext()
        let pull = try Fixture.decode(PullResponse.self, "sync_pull")
        try SyncMerge.apply(pull, in: ctx)
        #expect(try ctx.fetchCount(FetchDescriptor<LocalSession>()) == pull.sessions.count)
        #expect(try ctx.fetchCount(FetchDescriptor<LocalSet>()) == pull.sets.count)
        #expect(try ctx.fetchCount(FetchDescriptor<CachedVariant>()) == pull.variants.count)
        #expect(try ctx.fetchCount(FetchDescriptor<CachedGym>()) == pull.gyms.count)
        #expect(try ctx.fetchCount(FetchDescriptor<CachedPlannedSession>()) == pull.plannedSessions.count)
        #expect(SyncState.current(in: ctx).config?.version == "v3.1")
        // applying the same snapshot again is a no-op
        let stats = try SyncMerge.apply(pull, in: ctx)
        #expect(stats.applied == 0)
    }

    @Test func storedPlanReplacesPreviewForSameDayAndKind() throws {
        let ctx = try makeContext()
        let preview = PlanItemDTO(id: nil, date: "2026-10-26", kind: "upper", title: "Upper", session: PrescriptionDTO(type: "lifting"))
        PlanCache.store(week: PlanWeekDTO(start: "2026-10-26", days: [PlanDayDTO(date: "2026-10-26", sessions: [preview])]), in: ctx)
        #expect(try ctx.fetch(FetchDescriptor<CachedPlannedSession>()).first?.isPreview == true)
        let stored = PlanItemDTO(id: UUID().uuidString.lowercased(), date: "2026-10-26", kind: "upper", title: "Upper",
                                 session: PrescriptionDTO(type: "lifting"), updatedAt: date("2026-10-26T01:00:00.000Z"))
        PlanCache.merge(planned: [stored], in: ctx)
        let rows = try ctx.fetch(FetchDescriptor<CachedPlannedSession>())
        #expect(rows.count == 1)
        #expect(rows[0].isPreview == false)
    }
}

@MainActor
struct OutboxTests {
    private func dirtyCompletedSet(in ctx: ModelContext, session: LocalSession) -> LocalSet {
        let s = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 1, weightKg: 50, reps: 8,
                         completedAt: .now)
        ctx.insert(s)
        return s
    }

    @Test func collectsDirtyPerformedSetsAndTheirSessionOnly() throws {
        let ctx = try makeContext()
        let session = LocalSession(name: "Upper")
        ctx.insert(session)
        let done = dirtyCompletedSet(in: ctx, session: session)
        let planned = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 2, weightKg: 50, reps: 8)
        ctx.insert(planned)  // never performed -> stays local
        let clean = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 3, completedAt: .now, dirty: false)
        ctx.insert(clean)
        let batch = try Outbox.collect(in: ctx)
        #expect(batch.sets.map(\.id) == [done.id.uuidString.lowercased()])
        #expect(batch.sessions.count == 1)  // in-progress session is pushable because it has a performed set
        #expect(batch.sessions[0].id == session.id.uuidString.lowercased())
    }

    @Test func emptyInProgressSessionIsNotPushed() throws {
        let ctx = try makeContext()
        ctx.insert(LocalSession(name: "Workout"))
        #expect(try Outbox.collect(in: ctx).isEmpty)
    }

    @Test func appliedAndStaleClearDirtyRejectedKeepsFlag() throws {
        let ctx = try makeContext()
        let session = LocalSession(name: "Upper", endedAt: .now)
        ctx.insert(session)
        let a = dirtyCompletedSet(in: ctx, session: session)
        let b = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 2, weightKg: 1, reps: 1, completedAt: .now)
        ctx.insert(b)
        let c = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 3, weightKg: 1, reps: 1, completedAt: .now)
        ctx.insert(c)
        let batch = try Outbox.collect(in: ctx)
        let results = [
            PushResultDTO(entity: "session", id: session.id.uuidString.lowercased(), status: "applied", reason: nil),
            PushResultDTO(entity: "set", id: a.id.uuidString.lowercased(), status: "applied", reason: nil),
            PushResultDTO(entity: "set", id: b.id.uuidString.lowercased(), status: "stale", reason: nil),
            PushResultDTO(entity: "set", id: c.id.uuidString.lowercased(), status: "rejected", reason: "unknown variant_id 42"),
        ]
        let outcome = try Outbox.apply(results: results, batch: batch, in: ctx)
        #expect(outcome.cleared == 3 && outcome.rejected == 1)
        #expect(!session.dirty && !a.dirty && !b.dirty)
        #expect(a.hasBeenPushed)
        #expect(c.dirty && c.rejectedReason == "unknown variant_id 42")
        // rejected records are not re-sent until edited again
        #expect(try Outbox.collect(in: ctx).isEmpty)
        c.touch()
        #expect(try Outbox.collect(in: ctx).sets.count == 1)
    }

    @Test func recordEditedWhileInFlightStaysDirty() throws {
        let ctx = try makeContext()
        let session = LocalSession(name: "Upper", endedAt: .now)
        ctx.insert(session)
        let set = dirtyCompletedSet(in: ctx, session: session)
        let batch = try Outbox.collect(in: ctx)
        set.reps = 9
        set.touch(Date().addingTimeInterval(5))
        _ = try Outbox.apply(results: [PushResultDTO(entity: "set", id: set.id.uuidString.lowercased(), status: "applied", reason: nil)],
                             batch: batch, in: ctx)
        #expect(set.dirty)
    }

    @Test func tombstonesOfPushedSetsAreSentButNeverPerformedDeletesVanish() throws {
        let ctx = try makeContext()
        let session = LocalSession(name: "Upper")
        ctx.insert(session)
        let svc = WorkoutService(context: ctx)
        let performed = dirtyCompletedSet(in: ctx, session: session)
        performed.hasBeenPushed = true
        performed.dirty = false
        let planned = LocalSet(sessionId: session.id, variantId: 42, exerciseOrder: 1, setOrder: 2)
        ctx.insert(planned)
        svc.deleteSet(performed)
        svc.deleteSet(planned)
        let batch = try Outbox.collect(in: ctx)
        #expect(batch.sets.count == 1 && batch.sets[0].deletedAt != nil)
        #expect(try ctx.fetchCount(FetchDescriptor<LocalSet>()) == 1)
    }
}

struct SyncBackoffTests {
    @Test func exponentialWithCap() {
        #expect(SyncCoordinator.backoffDelay(failures: 1) == 5)
        #expect(SyncCoordinator.backoffDelay(failures: 2) == 10)
        #expect(SyncCoordinator.backoffDelay(failures: 3) == 20)
        #expect(SyncCoordinator.backoffDelay(failures: 20) == 300)
    }
}

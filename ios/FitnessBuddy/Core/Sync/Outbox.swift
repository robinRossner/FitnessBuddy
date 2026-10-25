import Foundation
import SwiftData

/// A snapshot of everything that needs pushing, plus the `updatedAt` stamps that were sent so we can tell
/// afterwards whether a record was edited again while the request was in flight.
struct OutboxBatch: Sendable {
    var sessions: [SyncSessionDTO] = []
    var sets: [SyncSetDTO] = []
    var bodyMeasurements: [SyncBodyDTO] = []
    var stamps: [String: Date] = [:]

    var isEmpty: Bool { sessions.isEmpty && sets.isEmpty && bodyMeasurements.isEmpty }
    var count: Int { sessions.count + sets.count + bodyMeasurements.count }

    static func key(_ entity: String, _ id: String) -> String { "\(entity):\(id.lowercased())" }
}

enum Outbox {
    static let sessionEntity = "session"
    static let setEntity = "set"
    static let bodyEntity = "body_measurement"

    /// Which entity names the server may use in results; all map back to our three tables.
    static func normalize(entity: String) -> String {
        switch entity {
        case "sessions", "session", "training_session": sessionEntity
        case "sets", "set", "exercise_set": setEntity
        case "body_measurements", "body_measurement", "bodyweight": bodyEntity
        default: entity
        }
    }

    /// Demo rows (`isDemo`) are never collected: they must not reach a real server.
    static func collect(in ctx: ModelContext, limit: Int = 300) throws -> OutboxBatch {
        var batch = OutboxBatch()

        // Sets: only ones that were performed (or that the server already knows about / were deleted).
        let dirtySets = try ctx.fetch(FetchDescriptor<LocalSet>(
            predicate: #Predicate { $0.dirty && $0.rejectedReason == nil && !$0.isDemo },
            sortBy: [SortDescriptor(\.updatedAt)]))
            .filter { $0.completedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed }
        let sets = Array(dirtySets.prefix(limit))
        let setSessionIds = Set(sets.map(\.sessionId))

        // Sessions: finished / deleted / known to the server / has a performed set. Empty in-progress ones stay local.
        let dirtySessions = try ctx.fetch(FetchDescriptor<LocalSession>(
            predicate: #Predicate { $0.dirty && $0.rejectedReason == nil && !$0.isDemo },
            sortBy: [SortDescriptor(\.updatedAt)]))
            .filter { $0.endedAt != nil || $0.deletedAt != nil || $0.hasBeenPushed || setSessionIds.contains($0.id) }
        let sessions = Array(dirtySessions.prefix(limit))

        for s in sessions {
            let dto = SyncSessionDTO(id: s.id.uuidString.lowercased(), plannedSessionId: s.plannedSessionId, gymId: s.gymId,
                                     kind: s.kind, name: s.name, startedAt: s.startedAt, endedAt: s.endedAt, notes: s.notes,
                                     updatedAt: s.updatedAt, deletedAt: s.deletedAt)
            batch.sessions.append(dto)
            batch.stamps[OutboxBatch.key(sessionEntity, dto.id)] = s.updatedAt
        }
        for s in sets {
            let dto = SyncSetDTO(id: s.id.uuidString.lowercased(), sessionId: s.sessionId.uuidString.lowercased(),
                                 variantId: s.variantId, exerciseOrder: s.exerciseOrder, setOrder: s.setOrder,
                                 setType: s.setType, weightKg: s.weightKg, reps: s.reps, durationS: s.durationS,
                                 completedAt: s.completedAt, notes: s.notes, updatedAt: s.updatedAt, deletedAt: s.deletedAt)
            batch.sets.append(dto)
            batch.stamps[OutboxBatch.key(setEntity, dto.id)] = s.updatedAt
        }
        let bodies = try ctx.fetch(FetchDescriptor<LocalBodyMeasurement>(
            predicate: #Predicate { $0.dirty && $0.rejectedReason == nil && !$0.isDemo },
            sortBy: [SortDescriptor(\.updatedAt)]))
        for b in bodies.prefix(limit) {
            let dto = SyncBodyDTO(id: b.id.uuidString.lowercased(), measuredAt: b.measuredAt, weightKg: b.weightKg,
                                  note: b.note, updatedAt: b.updatedAt, deletedAt: b.deletedAt)
            batch.bodyMeasurements.append(dto)
            batch.stamps[OutboxBatch.key(bodyEntity, dto.id)] = b.updatedAt
        }
        return batch
    }

    /// Number of records that still wait for a push (for the status badge / tests).
    static func pendingCount(in ctx: ModelContext) -> Int {
        (try? collect(in: ctx, limit: 10_000).count) ?? 0
    }

    /// - applied / stale: clear `dirty` if untouched since the snapshot (a stale record is refreshed by the next pull).
    /// - rejected: keep `dirty`, flag with the reason; excluded from pushes until the user edits it again.
    /// - missing from results: left dirty, retried next time.
    @discardableResult
    static func apply(results: [PushResultDTO], batch: OutboxBatch, in ctx: ModelContext) throws -> (cleared: Int, rejected: Int) {
        var cleared = 0, rejected = 0
        for r in results {
            guard let id = UUID(uuidString: r.id) else { continue }
            let entity = normalize(entity: r.entity)
            guard let stamp = batch.stamps[OutboxBatch.key(entity, r.id)] else { continue }
            switch entity {
            case sessionEntity:
                guard let rec = try ctx.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.id == id })).first else { continue }
                if handle(r, stamp: stamp, updatedAt: rec.updatedAt, dirty: &rec.dirty, pushed: &rec.hasBeenPushed, reason: &rec.rejectedReason) {
                    cleared += 1
                } else if r.status == "rejected" { rejected += 1 }
            case setEntity:
                guard let rec = try ctx.fetch(FetchDescriptor<LocalSet>(predicate: #Predicate { $0.id == id })).first else { continue }
                if handle(r, stamp: stamp, updatedAt: rec.updatedAt, dirty: &rec.dirty, pushed: &rec.hasBeenPushed, reason: &rec.rejectedReason) {
                    cleared += 1
                } else if r.status == "rejected" { rejected += 1 }
            case bodyEntity:
                guard let rec = try ctx.fetch(FetchDescriptor<LocalBodyMeasurement>(predicate: #Predicate { $0.id == id })).first else { continue }
                if handle(r, stamp: stamp, updatedAt: rec.updatedAt, dirty: &rec.dirty, pushed: &rec.hasBeenPushed, reason: &rec.rejectedReason) {
                    cleared += 1
                } else if r.status == "rejected" { rejected += 1 }
            default: continue
            }
        }
        try ctx.save()
        return (cleared, rejected)
    }

    /// Returns true when the record was marked clean.
    private static func handle(_ r: PushResultDTO, stamp: Date, updatedAt: Date, dirty: inout Bool,
                               pushed: inout Bool, reason: inout String?) -> Bool {
        switch r.status {
        case "applied", "stale":
            pushed = true
            if updatedAt == stamp { dirty = false; reason = nil; return true }
            return false
        case "rejected":
            if updatedAt == stamp { reason = r.reason ?? "rejected by server" }
            return false
        default:
            return false
        }
    }
}

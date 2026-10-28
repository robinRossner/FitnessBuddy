import Foundation
import SwiftData

/// Last-write-wins rule shared by every synced entity.
enum LWW {
    enum Decision: Equatable { case applyServer, keepLocal }

    /// - A missing local record always takes the server's.
    /// - A newer server record wins over a clean *and* over a dirty local one (true last-write-wins).
    /// - A dirty local record is never overwritten by an equal-or-older server record.
    static func decide(localUpdatedAt: Date?, localDirty: Bool, serverUpdatedAt: Date) -> Decision {
        guard let localUpdatedAt else { return .applyServer }
        _ = localDirty  // kept in the signature to document that dirtiness only matters for ties
        return serverUpdatedAt > localUpdatedAt ? .applyServer : .keepLocal
    }
}

/// Applies a pull page to the local store. Pure function of (context, response): easy to test.
enum SyncMerge {
    struct Stats: Equatable {
        var applied = 0
        var kept = 0
    }

    @discardableResult
    static func apply(_ pull: PullResponse, in ctx: ModelContext) throws -> Stats {
        var stats = Stats()
        mergeReference(pull, in: ctx)
        for dto in pull.sessions { merge(session: dto, in: ctx, stats: &stats) }
        for dto in pull.sets { merge(set: dto, in: ctx, stats: &stats) }
        for dto in pull.bodyMeasurements { merge(body: dto, in: ctx, stats: &stats) }
        PlanCache.merge(planned: pull.plannedSessions, in: ctx)
        if let cfg = pull.config, let data = try? APICoding.makeEncoder().encode(cfg) {
            SyncState.current(in: ctx).configData = data
            RestSettings.reconcile(cfg.lifting, in: ctx)
        }
        try ctx.save()
        return stats
    }

    // MARK: Reference data (server-owned: always overwritten)

    private static func mergeReference(_ pull: PullResponse, in ctx: ModelContext) {
        let gyms = Dictionary(uniqueKeysWithValues: ((try? ctx.fetch(FetchDescriptor<CachedGym>())) ?? []).map { ($0.id, $0) })
        for g in pull.gyms {
            if let e = gyms[g.id] { e.name = g.name; e.deletedAt = g.deletedAt }
            else { ctx.insert(CachedGym(id: g.id, name: g.name, deletedAt: g.deletedAt)) }
        }
        let exercises = Dictionary(uniqueKeysWithValues: ((try? ctx.fetch(FetchDescriptor<CachedExercise>())) ?? []).map { ($0.id, $0) })
        for x in pull.exercises { upsert(exercise: x, existing: exercises[x.id], in: ctx) }
        let variants = Dictionary(uniqueKeysWithValues: ((try? ctx.fetch(FetchDescriptor<CachedVariant>())) ?? []).map { ($0.id, $0) })
        for v in pull.variants { upsert(variant: v, existing: variants[v.id], in: ctx) }
    }

    /// Also used right after the app created an exercise or variant (so it can be logged before the next pull).
    @discardableResult
    static func upsert(exercise x: ExerciseDTO, existing: CachedExercise?, in ctx: ModelContext) -> CachedExercise {
        if let e = existing {
            e.name = x.name; e.muscleGroup = x.muscleGroup; e.movementPattern = x.movementPattern
            e.bodyRegion = x.bodyRegion ?? e.bodyRegion
            e.defaultEquipment = x.defaultEquipment ?? e.defaultEquipment
            e.isTimed = x.isTimed ?? e.isTimed
            return e
        }
        let e = CachedExercise(id: x.id, name: x.name, muscleGroup: x.muscleGroup, movementPattern: x.movementPattern,
                               bodyRegion: x.bodyRegion, defaultEquipment: x.defaultEquipment, isTimed: x.isTimed ?? false)
        ctx.insert(e)
        return e
    }

    @discardableResult
    static func upsert(variant v: VariantDTO, existing: CachedVariant?, in ctx: ModelContext) -> CachedVariant {
        let ladder = v.weightLadder ?? []
        if let e = existing {
            if e.name != v.name { relabelSets(ofVariant: e.id, from: e.name, to: v.name, in: ctx) }
            e.exerciseId = v.exerciseId ?? e.exerciseId
            e.name = v.name
            e.gymId = v.gymId
            e.equipmentType = v.equipmentType ?? e.equipmentType
            e.loadConvention = v.loadConvention ?? e.loadConvention
            e.active = v.active ?? true
            e.weightLadder = ladder
            e.repRangeLow = v.repRange?.first
            e.repRangeHigh = v.repRange?.last
            e.deletedAt = v.deletedAt
            e.incrementKg = v.incrementKg
            return e
        }
        let e = CachedVariant(id: v.id, exerciseId: v.exerciseId ?? 0, name: v.name, gymId: v.gymId,
                              equipmentType: v.equipmentType ?? "other",
                              loadConvention: v.loadConvention ?? "total", active: v.active ?? true,
                              weightLadder: ladder, repRangeLow: v.repRange?.first, repRangeHigh: v.repRange?.last,
                              deletedAt: v.deletedAt)
        e.incrementKg = v.incrementKg
        ctx.insert(e)
        return e
    }

    /// Sets keep their own copy of the variant name for display; a renamed variant (pulled from the server) updates it.
    /// Only the local-only copy changes: the set stays clean or dirty as it was, so nothing is pushed or overwritten.
    /// A set whose copy is something else than the old name (unknown, or empty) is left as it is.
    private static func relabelSets(ofVariant id: Int, from old: String, to new: String, in ctx: ModelContext) {
        let sets = (try? ctx.fetch(FetchDescriptor<LocalSet>(predicate: #Predicate { $0.variantId == id }))) ?? []
        for set in sets where set.variantName == old { set.variantName = new }
    }

    // MARK: Sessions

    private static func merge(session dto: SyncSessionDTO, in ctx: ModelContext, stats: inout Stats) {
        guard let id = UUID(uuidString: dto.id) else { return }
        let local = try? ctx.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.id == id })).first
        if local == nil && dto.deletedAt != nil { return }
        switch LWW.decide(localUpdatedAt: local?.updatedAt, localDirty: local?.dirty ?? false, serverUpdatedAt: dto.updatedAt) {
        case .keepLocal:
            stats.kept += 1
        case .applyServer:
            let s = local ?? { let n = LocalSession(id: id, name: dto.name); n.startedHere = false; ctx.insert(n); return n }()
            s.plannedSessionId = dto.plannedSessionId
            s.gymId = dto.gymId
            s.kind = dto.kind
            s.name = dto.name
            s.startedAt = dto.startedAt
            s.endedAt = dto.endedAt
            s.notes = dto.notes
            s.updatedAt = dto.updatedAt
            s.deletedAt = dto.deletedAt
            s.dirty = false
            s.rejectedReason = nil
            s.hasBeenPushed = true
            stats.applied += 1
        }
    }

    // MARK: Sets

    private static func merge(set dto: SyncSetDTO, in ctx: ModelContext, stats: inout Stats) {
        guard let id = UUID(uuidString: dto.id), let sessionId = UUID(uuidString: dto.sessionId) else { return }
        let local = try? ctx.fetch(FetchDescriptor<LocalSet>(predicate: #Predicate { $0.id == id })).first
        if local == nil && dto.deletedAt != nil { return }
        switch LWW.decide(localUpdatedAt: local?.updatedAt, localDirty: local?.dirty ?? false, serverUpdatedAt: dto.updatedAt) {
        case .keepLocal:
            stats.kept += 1
        case .applyServer:
            let s: LocalSet
            if let local { s = local } else {
                s = LocalSet(id: id, sessionId: sessionId, variantId: dto.variantId,
                             variantName: variantName(dto.variantId, in: ctx), exerciseOrder: dto.exerciseOrder,
                             setOrder: dto.setOrder)
                ctx.insert(s)
            }
            s.sessionId = sessionId
            s.variantId = dto.variantId
            s.exerciseOrder = dto.exerciseOrder
            s.setOrder = dto.setOrder
            s.setType = dto.setType
            s.weightKg = dto.weightKg
            s.reps = dto.reps
            s.durationS = dto.durationS
            s.completedAt = dto.completedAt
            s.notes = dto.notes
            s.updatedAt = dto.updatedAt
            s.deletedAt = dto.deletedAt
            s.dirty = false
            s.rejectedReason = nil
            s.hasBeenPushed = true
            if s.variantName.isEmpty { s.variantName = variantName(dto.variantId, in: ctx) }
            stats.applied += 1
        }
    }

    private static func variantName(_ id: Int, in ctx: ModelContext) -> String {
        let v = try? ctx.fetch(FetchDescriptor<CachedVariant>(predicate: #Predicate { $0.id == id })).first
        return v?.name ?? ""
    }

    // MARK: Body measurements

    private static func merge(body dto: SyncBodyDTO, in ctx: ModelContext, stats: inout Stats) {
        guard let id = UUID(uuidString: dto.id) else { return }
        let local = try? ctx.fetch(FetchDescriptor<LocalBodyMeasurement>(predicate: #Predicate { $0.id == id })).first
        if local == nil && dto.deletedAt != nil { return }
        switch LWW.decide(localUpdatedAt: local?.updatedAt, localDirty: local?.dirty ?? false, serverUpdatedAt: dto.updatedAt) {
        case .keepLocal:
            stats.kept += 1
        case .applyServer:
            let b = local ?? { let n = LocalBodyMeasurement(id: id, weightKg: dto.weightKg); ctx.insert(n); return n }()
            b.measuredAt = dto.measuredAt
            b.weightKg = dto.weightKg
            b.note = dto.note
            b.updatedAt = dto.updatedAt
            b.deletedAt = dto.deletedAt
            b.dirty = false
            b.rejectedReason = nil
            b.hasBeenPushed = true
            stats.applied += 1
        }
    }
}

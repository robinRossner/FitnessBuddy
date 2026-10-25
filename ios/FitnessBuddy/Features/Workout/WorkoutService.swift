import Foundation
import SwiftData

/// All mutations of an in-progress workout. Every method saves immediately so the workout survives an
/// app kill at any point, and every change goes through `touch()` so it lands in the sync outbox.
@MainActor
struct WorkoutService {
    let context: ModelContext

    /// While demo data is loaded, everything logged is demo too and can never be pushed.
    private var demoMode: Bool { SyncState.current(in: context).demoActive }

    // MARK: Queries

    func activeSession() -> LocalSession? {
        let d = FetchDescriptor<LocalSession>(
            predicate: #Predicate { $0.endedAt == nil && $0.deletedAt == nil && $0.startedHere },
            sortBy: [SortDescriptor(\.startedAt, order: .reverse)])
        return (try? context.fetch(d))?.first
    }

    func sets(of session: LocalSession) -> [LocalSet] {
        let id = session.id
        let d = FetchDescriptor<LocalSet>(
            predicate: #Predicate { $0.sessionId == id && $0.deletedAt == nil },
            sortBy: [SortDescriptor(\.exerciseOrder), SortDescriptor(\.setOrder)])
        return (try? context.fetch(d)) ?? []
    }

    func variant(_ id: Int) -> CachedVariant? {
        try? context.fetch(FetchDescriptor<CachedVariant>(predicate: #Predicate { $0.id == id })).first
    }

    /// Working sets of the most recent earlier, finished session that contained this variant
    /// (local history incl. pulled server history; server-imported sets may lack `completed_at`).
    func previousPerformance(variantId: Int, excluding sessionId: UUID?) -> [LastSetDTO] {
        let sets = (try? context.fetch(FetchDescriptor<LocalSet>(
            predicate: #Predicate { $0.variantId == variantId && $0.deletedAt == nil && $0.reps != nil }))) ?? []
        let usable = sets.filter { $0.setType != "warmup" && $0.sessionId != sessionId }
        let ids = Set(usable.map(\.sessionId))
        guard !ids.isEmpty else { return [] }
        let sessions = ((try? context.fetch(FetchDescriptor<LocalSession>(predicate: #Predicate { $0.deletedAt == nil }))) ?? [])
            .filter { ids.contains($0.id) && ($0.endedAt != nil || !$0.startedHere) }
        guard let latest = sessions.max(by: { $0.startedAt < $1.startedAt }) else { return [] }
        return usable.filter { $0.sessionId == latest.id }
            .sorted { $0.setOrder < $1.setOrder }
            .map { LastSetDTO(weight: $0.weightKg, reps: $0.reps, durationS: $0.durationS) }
    }

    private func gymId(named name: String) -> Int? {
        let gyms = (try? context.fetch(FetchDescriptor<CachedGym>(predicate: #Predicate { $0.name == name }))) ?? []
        return (gyms.first { $0.deletedAt == nil } ?? gyms.first)?.id
    }

    // MARK: Start

    @discardableResult
    func startEmpty(gymId: Int?) -> LocalSession {
        if let active = activeSession() { return active }
        let s = LocalSession(gymId: gymId, kind: "other", name: "Workout")
        s.isDemo = demoMode
        context.insert(s)
        save()
        return s
    }

    /// Creates the session with all planned sets prefilled (uncompleted, so nothing is logged until ticked).
    @discardableResult
    func start(from planned: CachedPlannedSession, gymId: Int?) -> LocalSession? {
        if let active = activeSession() { return active }
        guard let item = planned.item, let exercises = item.session?.exercises else { return nil }
        let kind = ["upper", "lower", "full"].contains(item.kind) ? item.kind : "other"
        // No gym chosen in Settings: resolve the plan's gym name ("Gym 3") against the synced gyms.
        let resolvedGym = gymId ?? item.session?.gym.flatMap(gymId(named:))
        let s = LocalSession(plannedSessionId: planned.isPreview ? nil : planned.id, gymId: resolvedGym, kind: kind,
                             name: item.title ?? item.kind.capitalized)
        let demo = demoMode
        s.isDemo = demo
        context.insert(s)
        var order = 0
        for ex in exercises {
            guard let variantId = ex.variantId else { continue }  // unresolved exercises can't be logged yet
            order += 1
            let last = ex.last?.sets ?? []
            for (i, ps) in ex.sets.enumerated() {
                let prev = i < last.count ? last[i] : last.last
                let set = LocalSet(sessionId: s.id, variantId: variantId, variantName: ex.name, exerciseOrder: order,
                                   setOrder: i + 1, setType: ps.setType,
                                   weightKg: ps.targetWeight ?? prev?.weight,
                                   reps: ps.targetReps ?? ex.repRange?.last,
                                   durationS: ps.targetDurationS)
                set.isDemo = demo
                set.targetWeightKg = ps.targetWeight
                set.targetReps = ps.targetReps
                set.targetDurationS = ps.targetDurationS
                set.prevWeightKg = prev?.weight
                set.prevReps = prev?.reps
                set.prevDurationS = prev?.durationS
                context.insert(set)
            }
        }
        save()
        return s
    }

    // MARK: Exercises

    func addExercise(_ variant: CachedVariant, to session: LocalSession, setCount: Int = 3) {
        let order = (sets(of: session).map(\.exerciseOrder).max() ?? 0) + 1
        let prev = previousPerformance(variantId: variant.id, excluding: session.id)
        let n = prev.isEmpty ? setCount : min(max(prev.count, 1), 6)
        for i in 0..<n {
            let p = i < prev.count ? prev[i] : prev.last
            let set = LocalSet(sessionId: session.id, variantId: variant.id, variantName: variant.name, exerciseOrder: order,
                               setOrder: i + 1, weightKg: p?.weight, reps: p?.reps ?? variant.repRangeHigh,
                               durationS: p?.durationS)
            set.isDemo = session.isDemo
            set.prevWeightKg = p?.weight
            set.prevReps = p?.reps
            set.prevDurationS = p?.durationS
            context.insert(set)
        }
        session.touch()
        save()
    }

    func swapExercise(in session: LocalSession, order: Int, to variant: CachedVariant) {
        let prev = previousPerformance(variantId: variant.id, excluding: session.id)
        let group = sets(of: session).filter { $0.exerciseOrder == order }
        for (i, set) in group.enumerated() {
            let p = i < prev.count ? prev[i] : prev.last
            set.variantId = variant.id
            set.variantName = variant.name
            set.targetWeightKg = nil
            set.targetReps = nil
            set.targetDurationS = nil
            set.prevWeightKg = p?.weight
            set.prevReps = p?.reps
            set.prevDurationS = p?.durationS
            if !set.isCompleted {
                set.weightKg = p?.weight
                set.reps = p?.reps ?? set.reps
                set.durationS = p?.durationS
            }
            set.touch()
        }
        session.touch()
        save()
    }

    func skipExercise(in session: LocalSession, order: Int) {
        for set in sets(of: session) where set.exerciseOrder == order { remove(set) }
        session.touch()
        save()
    }

    func setExerciseNote(in session: LocalSession, order: Int, text: String) {
        guard let first = sets(of: session).first(where: { $0.exerciseOrder == order }) else { return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        first.notes = trimmed.isEmpty ? nil : trimmed
        first.touch()
        save()
    }

    func setSessionNotes(_ session: LocalSession, text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        session.notes = trimmed.isEmpty ? nil : trimmed
        session.touch()
        save()
    }

    // MARK: Sets

    @discardableResult
    func addSet(to session: LocalSession, order: Int) -> LocalSet? {
        let group = sets(of: session).filter { $0.exerciseOrder == order }
        guard let last = group.last else { return nil }
        let set = LocalSet(sessionId: session.id, variantId: last.variantId, variantName: last.variantName,
                           exerciseOrder: order, setOrder: last.setOrder + 1, weightKg: last.weightKg, reps: last.reps,
                           durationS: last.durationS)
        set.isDemo = session.isDemo
        set.prevWeightKg = last.prevWeightKg
        set.prevReps = last.prevReps
        set.prevDurationS = last.prevDurationS
        context.insert(set)
        session.touch()
        save()
        return set
    }

    func deleteSet(_ set: LocalSet) {
        remove(set)
        save()
    }

    /// Never-performed, never-pushed sets just vanish; anything the server may know about becomes a tombstone.
    private func remove(_ set: LocalSet) {
        if set.completedAt == nil && !set.hasBeenPushed {
            context.delete(set)
        } else {
            set.deletedAt = .now
            set.touch()
        }
    }

    /// Toggles completion. Returns true if the set is now completed (caller starts the rest timer).
    @discardableResult
    func toggleComplete(_ set: LocalSet) -> Bool {
        if set.completedAt == nil {
            if set.weightKg == nil { set.weightKg = set.targetWeightKg ?? set.prevWeightKg }
            if set.reps == nil && !set.isTimed { set.reps = set.targetReps ?? set.prevReps }
            if set.durationS == nil && set.targetDurationS != nil { set.durationS = set.targetDurationS }
            set.completedAt = .now
        } else {
            set.completedAt = nil
        }
        set.touch()
        save()
        return set.completedAt != nil
    }

    /// Edits the weight; later *uncompleted* sets that still had the old weight follow along.
    func setWeight(_ set: LocalSet, _ value: Double?, in session: LocalSession? = nil) {
        let old = set.weightKg
        guard old != value else { return }
        set.weightKg = value
        set.touch()
        if let session {
            for other in sets(of: session) where other.exerciseOrder == set.exerciseOrder
                && other.setOrder > set.setOrder && !other.isCompleted && other.weightKg == old {
                other.weightKg = value
                other.touch()
            }
        }
        save()
    }

    func setReps(_ set: LocalSet, _ value: Int?) {
        guard set.reps != value else { return }
        set.reps = value
        set.touch()
        save()
    }

    func setDuration(_ set: LocalSet, _ value: Double?) {
        guard set.durationS != value else { return }
        set.durationS = value
        set.touch()
        save()
    }

    func setType(_ set: LocalSet, _ type: String) {
        set.setType = type
        set.touch()
        save()
    }

    // MARK: Finish

    /// Returns nil (and discards the session) if nothing was performed.
    func finish(_ session: LocalSession) -> WorkoutSummary? {
        let all = sets(of: session)
        for s in all where !s.isCompleted { remove(s) }
        let performed = all.filter(\.isCompleted)
        guard !performed.isEmpty else {
            discard(session)
            return nil
        }
        let end = Date()
        session.endedAt = end
        session.touch(end)
        save()
        return WorkoutSummary.compute(name: session.name, startedAt: session.startedAt, endedAt: end, sets: performed)
    }

    func discard(_ session: LocalSession) {
        for s in sets(of: session) { remove(s) }
        if session.hasBeenPushed {
            session.deletedAt = .now
            session.endedAt = session.endedAt ?? .now
            session.touch()
        } else {
            context.delete(session)
        }
        save()
    }

    func save() {
        do { try context.save() } catch { assertionFailure("save failed: \(error)") }
    }
}

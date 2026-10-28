import Foundation

/// A finished session, reduced to what the "This Week" list needs.
struct FinishedWeekRef: Equatable {
    var id: UUID
    var plannedId: String?
    /// "YYYY-MM-DD" of `startedAt`
    var day: String
    var name: String
}

/// One line of the Workout tab's "This Week" card.
struct WorkoutWeekRow: Identifiable, Equatable {
    enum State: Equatable {
        case done
        /// A plan row for today or later.
        case planned
        /// A past plan row that was not done.
        case missed
    }

    var id: String
    var day: String
    var title: String
    var state: State
    /// The planned session (to open its plan), when there is one.
    var plannedId: String?
    /// The finished session (to open its detail), when done.
    var sessionId: UUID?
}

enum WorkoutHomeLogic {
    /// Cached plan rows and finished sessions in the shapes the chooser and the week list work with. Same rules as
    /// `WorkoutChooserLogic.choices(in:)`: a planned session counts as done when its row says so or a finished session
    /// links to it.
    static func refs(planned rows: [CachedPlannedSession], finished sessions: [LocalSession])
        -> (planned: [PlannedLiftRef], finished: [FinishedLiftRef]) {
        let doneIds = Set(sessions.compactMap(\.plannedSessionId))
        let planned = rows.compactMap { row -> PlannedLiftRef? in
            guard WorkoutChooserLogic.liftingKinds.contains(row.kind) else { return nil }
            return PlannedLiftRef(id: row.id, date: row.date, kind: row.kind, title: row.title,
                                  done: row.status == "done" || doneIds.contains(row.id),
                                  exerciseCount: row.item?.session?.exercises?.count ?? 0)
        }
        let finished = sessions.map { FinishedLiftRef(id: $0.id, kind: $0.kind, name: $0.name, startedAt: $0.startedAt) }
        return (planned, finished)
    }

    /// Lifting sessions of the week, in day order: planned ones (done, still to do, or missed) and finished ones nobody
    /// planned. A finished session on the day of an open plan row counts for that row (same rule as the Summary strip).
    static func week(days: [String], planned: [PlannedLiftRef], finished: [FinishedWeekRef], today: String) -> [WorkoutWeekRow] {
        let inWeek = Set(days)
        var rows: [WorkoutWeekRow] = []
        var used = Set<UUID>()

        let plans = planned.filter { inWeek.contains($0.date) }.sorted { ($0.date, $0.title) < ($1.date, $1.title) }
        // Sessions linked to a plan row first, so an early session is never taken for another row.
        var linked: [String: UUID] = [:]
        for p in plans {
            if let f = finished.first(where: { $0.plannedId == p.id }) { linked[p.id] = f.id; used.insert(f.id) }
        }
        for p in plans {
            var sessionId = linked[p.id]
            if sessionId == nil, !p.done {
                // done on the planned day without the link
                if let f = finished.first(where: { $0.plannedId == nil && $0.day == p.date && !used.contains($0.id) }) {
                    used.insert(f.id)
                    sessionId = f.id
                }
            }
            let state: WorkoutWeekRow.State = (p.done || sessionId != nil) ? .done : (p.date < today ? .missed : .planned)
            rows.append(WorkoutWeekRow(id: "plan-\(p.id)", day: p.date, title: p.title, state: state,
                                       plannedId: p.id, sessionId: sessionId))
        }
        let planIds = Set(plans.map(\.id))
        for f in finished where inWeek.contains(f.day) && !used.contains(f.id) && !(f.plannedId.map(planIds.contains) ?? false) {
            rows.append(WorkoutWeekRow(id: "done-\(f.id.uuidString)", day: f.day, title: f.name.isEmpty ? "Workout" : f.name,
                                       state: .done, plannedId: nil, sessionId: f.id))
        }
        return rows.sorted { ($0.day, $0.id) < ($1.day, $1.id) }
    }
}

// MARK: - Finished workout detail

/// One logged set of a finished workout, independent of SwiftData so it can be tested.
struct LoggedSet: Equatable {
    var variantId: Int
    var variantName: String
    var exerciseOrder: Int
    var setOrder: Int
    var setType: String
    var weightKg: Double?
    var reps: Int?
    var durationS: Double?

    var isWarmup: Bool { setType == "warmup" }
}

enum WorkoutDetailLogic {
    struct SetLine: Identifiable, Equatable {
        var id: Int
        var label: String
        var weightKg: Double?
        var reps: Int?
        var durationS: Double?
        var e1rm: Double?
        var isPR: Bool
        var isWarmup: Bool
    }

    struct Block: Identifiable, Equatable {
        var id: Int
        var variantId: Int
        var name: String
        var sets: [SetLine]
        var volumeKg: Double
        var hasPR: Bool
    }

    /// Epley, as the backend does it (`engine/lifting.py`). Needs a load; bodyweight sets have none.
    static func e1rm(weightKg: Double?, reps: Int?) -> Double? {
        guard let weightKg, weightKg > 0, let reps, reps > 0 else { return nil }
        return weightKg * (1 + Double(reps) / 30)
    }

    /// Done sets only count when they were logged: sessions pulled from the server may lack `completed_at`.
    static func isLogged(completedAt: Date?, deletedAt: Date?, reps: Int?, durationS: Double?, startedHere: Bool) -> Bool {
        guard deletedAt == nil, reps != nil || durationS != nil else { return false }
        return completedAt != nil || !startedHere
    }

    static func volume(_ sets: [LoggedSet]) -> Double {
        sets.filter { !$0.isWarmup }.reduce(0) { $0 + (($1.weightKg ?? 0) * Double($1.reps ?? 0)) }
    }

    /// Exercises in workout order. The set with the best e1RM of an exercise is a PR when it beats everything logged for
    /// that variant before this workout (`priorBest`, keyed by variant id; no history, no PR).
    static func blocks(sets: [LoggedSet], priorBest: [Int: Double]) -> [Block] {
        let byOrder = Dictionary(grouping: sets, by: \.exerciseOrder)
        return byOrder.keys.sorted().map { order in
            let group = byOrder[order, default: []].sorted { $0.setOrder < $1.setOrder }
            let variantId = group[0].variantId
            var bestIndex: Int?
            var bestValue = 0.0
            for (i, s) in group.enumerated() where !s.isWarmup {
                if let v = e1rm(weightKg: s.weightKg, reps: s.reps), v > bestValue { bestValue = v; bestIndex = i }
            }
            let prior = priorBest[variantId] ?? 0
            let prIndex: Int? = (prior > 0 && bestValue > prior + 0.0001) ? bestIndex : nil
            var working = 0
            let lines = group.enumerated().map { i, s -> SetLine in
                if !s.isWarmup { working += 1 }
                return SetLine(id: i, label: s.isWarmup ? "W" : "\(working)", weightKg: s.weightKg, reps: s.reps,
                               durationS: s.durationS, e1rm: s.isWarmup ? nil : e1rm(weightKg: s.weightKg, reps: s.reps),
                               isPR: i == prIndex, isWarmup: s.isWarmup)
            }
            let name = group.first { !$0.variantName.isEmpty }?.variantName.withoutGymSuffix ?? "Exercise"
            return Block(id: order, variantId: variantId, name: name, sets: lines, volumeKg: volume(group),
                         hasPR: prIndex != nil)
        }
    }
}

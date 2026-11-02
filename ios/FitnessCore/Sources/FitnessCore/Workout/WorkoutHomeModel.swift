import Foundation

/// A finished session, reduced to what the "This Week" list needs.
public struct FinishedWeekRef: Equatable, Sendable {
    public var id: UUID
    public var plannedId: String?
    /// "YYYY-MM-DD" of `startedAt`
    public var day: String
    public var name: String

    public init(id: UUID, plannedId: String? = nil, day: String, name: String) {
        self.id = id
        self.plannedId = plannedId
        self.day = day
        self.name = name
    }
}

/// One line of the Workout tab's "This Week" card.
public struct WorkoutWeekRow: Identifiable, Equatable, Sendable {
    public enum State: Equatable, Sendable {
        case done
        /// A plan row for today or later.
        case planned
        /// A past plan row that was not done.
        case missed
    }

    public var id: String
    public var day: String
    public var title: String
    public var state: State
    /// The planned session (to open its plan), when there is one.
    public var plannedId: String?
    /// The finished session (to open its detail), when done.
    public var sessionId: UUID?
}

public enum WorkoutHomeLogic: Sendable {
    /// Lifting sessions of the week, in day order: planned ones (done, still to do, or missed) and finished ones nobody
    /// planned. A finished session on the day of an open plan row counts for that row (same rule as the Summary strip).
    public static func week(days: [String], planned: [PlannedLiftRef], finished: [FinishedWeekRef], today: String) -> [WorkoutWeekRow] {
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
public struct LoggedSet: Equatable, Sendable {
    public var variantId: Int
    public var variantName: String
    public var exerciseOrder: Int
    public var setOrder: Int
    public var setType: String
    public var weightKg: Double?
    public var reps: Int?
    public var durationS: Double?

    public var isWarmup: Bool { setType == "warmup" }

    public init(variantId: Int, variantName: String, exerciseOrder: Int, setOrder: Int, setType: String, weightKg: Double? = nil, reps: Int? = nil, durationS: Double? = nil) {
        self.variantId = variantId
        self.variantName = variantName
        self.exerciseOrder = exerciseOrder
        self.setOrder = setOrder
        self.setType = setType
        self.weightKg = weightKg
        self.reps = reps
        self.durationS = durationS
    }
}

public enum WorkoutDetailLogic: Sendable {
    public struct SetLine: Identifiable, Equatable, Sendable {
        public var id: Int
        public var label: String
        public var weightKg: Double?
        public var reps: Int?
        public var durationS: Double?
        public var e1rm: Double?
        public var isPR: Bool
        public var isWarmup: Bool
    }

    public struct Block: Identifiable, Equatable, Sendable {
        public var id: Int
        public var variantId: Int
        public var name: String
        public var sets: [SetLine]
        public var volumeKg: Double
        public var hasPR: Bool
    }

    /// Epley, as the backend does it (`engine/lifting.py`). Needs a load; bodyweight sets have none.
    public static func e1rm(weightKg: Double?, reps: Int?) -> Double? {
        guard let weightKg, weightKg > 0, let reps, reps > 0 else { return nil }
        return weightKg * (1 + Double(reps) / 30)
    }

    /// Done sets only count when they were logged: sessions pulled from the server may lack `completed_at`.
    public static func isLogged(completedAt: Date?, deletedAt: Date?, reps: Int?, durationS: Double?, startedHere: Bool) -> Bool {
        guard deletedAt == nil, reps != nil || durationS != nil else { return false }
        return completedAt != nil || !startedHere
    }

    public static func volume(_ sets: [LoggedSet]) -> Double {
        sets.filter { !$0.isWarmup }.reduce(0) { $0 + (($1.weightKg ?? 0) * Double($1.reps ?? 0)) }
    }

    /// Exercises in workout order. The set with the best e1RM of an exercise is a PR when it beats everything logged for
    /// that variant before this workout (`priorBest`, keyed by variant id; no history, no PR).
    public static func blocks(sets: [LoggedSet], priorBest: [Int: Double]) -> [Block] {
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

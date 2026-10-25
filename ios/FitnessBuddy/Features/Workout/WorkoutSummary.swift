import Foundation

struct WorkoutSummary: Equatable, Identifiable {
    var id = UUID()
    var name: String
    var duration: TimeInterval
    var setCount: Int
    var exerciseCount: Int
    var volumeKg: Double

    /// Completed, non-deleted sets only. Volume = sum(weight x reps) of non-warm-up sets.
    static func compute(name: String, startedAt: Date, endedAt: Date, sets: [LocalSet]) -> WorkoutSummary {
        let done = sets.filter { $0.completedAt != nil && $0.deletedAt == nil }
        return WorkoutSummary(
            name: name,
            duration: max(0, endedAt.timeIntervalSince(startedAt)),
            setCount: done.count,
            exerciseCount: Set(done.map(\.exerciseOrder)).count,
            volumeKg: volume(of: done))
    }

    static func volume(of sets: [LocalSet]) -> Double {
        sets.filter { $0.completedAt != nil && $0.deletedAt == nil && $0.setType != "warmup" }
            .reduce(0) { $0 + (($1.weightKg ?? 0) * Double($1.reps ?? 0)) }
    }
}

import FitnessCore
import Foundation

/// The SwiftData side of `WorkoutHomeLogic` (the pure rules live in FitnessCore).
extension WorkoutHomeLogic {
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
}

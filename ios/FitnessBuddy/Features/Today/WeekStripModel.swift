import Foundation

/// What a day of the "This week" strip shows, merged from the cached plan, the workouts finished on this device and the
/// runs the stats know about.
struct PlannedDayRef: Equatable {
    var id: String
    var date: String
    var kind: String
    var status: String
}

struct FinishedDayRef: Equatable {
    /// `planned_session_id` it was linked to (doing a session early counts for that planned session).
    var plannedId: String?
    var day: String
    var kind: String
}

struct WeekCellModel: Equatable, Identifiable {
    enum State: Equatable {
        /// Finished (lifting from local data, runs from the runs list, or a plan row marked done).
        case done
        /// A plan row for today or later.
        case planned
        /// A past plan row that was not done.
        case missed
        /// No plan row: the usual weekday pattern, drawn faint.
        case expected
        /// The plan or the pattern says rest.
        case rest
    }

    var day: String
    var kind: String
    var state: State
    var id: String { day }
}

enum WeekStripModel {
    /// Mon ... Sun
    static let template = ["upper", "lower", "easy_run", "upper", "intervals", "rest", "long_run"]

    static func cells(days: [String], planned: [PlannedDayRef], finished: [FinishedDayRef], runDays: Set<String>,
                      today: String) -> [WeekCellModel] {
        let doneIds = Set(finished.compactMap(\.plannedId))
        return days.enumerated().map { index, day in
            let row = planned.first { $0.date == day }
            let templateKind = template[min(index, template.count - 1)]
            let isPastOrToday = day <= today
            let rowDone = row.map { $0.status == "done" || doneIds.contains($0.id) } ?? false

            if rowDone, let row { return WeekCellModel(day: day, kind: row.kind, state: .done) }
            if isPastOrToday {
                if let f = finished.first(where: { $0.day == day }) {
                    let kind = ["upper", "lower", "full"].contains(f.kind) ? f.kind : (row?.kind ?? "full")
                    return WeekCellModel(day: day, kind: kind, state: .done)
                }
                if runDays.contains(day) {
                    let runKind = row.flatMap { Domain.forKind($0.kind) == .running ? $0.kind : nil }
                        ?? (Domain.forKind(templateKind) == .running ? templateKind : "easy_run")
                    return WeekCellModel(day: day, kind: runKind, state: .done)
                }
            }
            if let row {
                if row.kind == "rest" { return WeekCellModel(day: day, kind: "rest", state: .rest) }
                return WeekCellModel(day: day, kind: row.kind, state: day < today ? .missed : .planned)
            }
            return WeekCellModel(day: day, kind: templateKind, state: templateKind == "rest" ? .rest : .expected)
        }
    }
}

import Foundation

/// What a day of the "This week" strip shows, merged from the cached plan, the workouts finished on this device and the
/// runs the stats know about.
public struct PlannedDayRef: Equatable, Sendable {
    public var id: String
    public var date: String
    public var kind: String
    public var status: String

    public init(id: String, date: String, kind: String, status: String) {
        self.id = id
        self.date = date
        self.kind = kind
        self.status = status
    }
}

public struct FinishedDayRef: Equatable, Sendable {
    /// `planned_session_id` it was linked to (doing a session early counts for that planned session).
    public var plannedId: String?
    public var day: String
    public var kind: String

    public init(plannedId: String? = nil, day: String, kind: String) {
        self.plannedId = plannedId
        self.day = day
        self.kind = kind
    }
}

public struct WeekCellModel: Equatable, Identifiable, Sendable {
    public enum State: Equatable, Sendable {
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

    public var day: String
    public var kind: String
    public var state: State
    public var id: String { day }
}

public enum WeekStripModel: Sendable {
    /// Mon ... Sun
    public static let template = ["upper", "lower", "easy_run", "upper", "intervals", "rest", "long_run"]

    public static func cells(days: [String], planned: [PlannedDayRef], finished: [FinishedDayRef], runDays: Set<String>,
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

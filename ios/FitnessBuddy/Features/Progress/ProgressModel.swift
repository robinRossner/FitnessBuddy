import Foundation

// Pure helpers for the Progress tab (Lifting | Running | Insights), built from the real stats and the cached plan only.
// Unit-tested in ProgressTabTests.

/// The next planned target of one lift, from the cached week plan.
struct NextLiftTarget: Equatable {
    /// `YYYY-MM-DD` of the planned session.
    var date: String
    /// "3 × 8 @ 80 kg"
    var text: String
    /// The progression engine's note for it ("+2.5 kg after 3 × 10"), if any.
    var note: String?
}

/// One week of a Progress bar chart. `key` is the String x value (a BarMark with `.ratio` width needs one).
struct ProgressWeekBar: Equatable, Identifiable {
    var weekStart: String
    var value: Double
    var isCurrent: Bool
    var key: String { weekStart }
    var id: String { weekStart }
    var date: Date { DayKey.date(weekStart) ?? .distantPast }
}

/// Numbers of the Running segment.
struct RunningOverview: Equatable {
    var kmThisWeek: Double
    /// Sum of the planned run distances of this week; nil without a plan.
    var kmPlannedThisWeek: Double?
    var kmLastWeek: Double?
    /// Distance-weighted average pace of the easy runs of the last 28 days (seconds per km).
    var easyPaceSPerKm: Double?
    var easyRuns: Int
    /// The last `weeks` weeks, oldest first, zero-filled; the last one is the current week.
    var weeks: [ProgressWeekBar]
    /// Average of the full weeks shown (the current week is left out); nil when none had a run.
    var averageKm: Double?
}

/// A row of "This Week's Runs": a run that was done (opens its analysis) or a planned run still to do (opens the plan).
struct ProgressWeekRun: Identifiable, Equatable {
    enum Target: Equatable {
        case run(id: String)
        case planned(PlanItemDTO)
    }

    var id: String
    var date: String
    var title: String
    var detail: String
    /// "Today", "Missed", "Skipped" for planned rows.
    var badge: String?
    var target: Target

    var isDone: Bool { if case .run = target { true } else { false } }
}

enum ProgressModel {
    // MARK: Lifting

    /// The first lifting session from today on, not done or skipped, that has this lift with sets.
    static func nextTarget(variantId: Int, items: [PlanItemDTO], doneIds: Set<String> = [], today: String) -> NextLiftTarget? {
        let open = items
            .filter { $0.date >= today && $0.deletedAt == nil && WorkoutChooserLogic.liftingKinds.contains($0.kind) }
            .filter { $0.planStatus != .done && $0.planStatus != .skipped && !($0.id.map(doneIds.contains) ?? false) }
            .sorted { $0.date < $1.date }
        for item in open {
            guard let ex = item.session?.exercises?.first(where: { $0.variantId == variantId }), !ex.sets.isEmpty else { continue }
            let working = ex.sets.filter { $0.setType == "working" }
            let text = PlanFormat.setsSummary(working.isEmpty ? ex.sets : working)
            guard !text.isEmpty else { continue }
            let note = [ex.progressionNote, ex.reason].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
                .first { !$0.isEmpty }
            return NextLiftTarget(date: item.date, text: text, note: note)
        }
        return nil
    }

    /// e1RM values of the last `count` sessions with one, oldest first (card sparkline).
    static func e1rmSparkline(_ points: [LiftPointDTO], count: Int = 12) -> [Double] {
        Array(points.sorted { $0.date < $1.date }.compactMap(\.bestE1rm).suffix(count))
    }

    /// Change of the first to the last e1RM of the points, in percent; nil with fewer than two.
    static func e1rmChangePct(_ points: [LiftPointDTO]) -> Double? {
        let values = points.sorted { $0.date < $1.date }.compactMap(\.bestE1rm)
        guard values.count >= 2, let first = values.first, let last = values.last, first > 0 else { return nil }
        return (last - first) / first * 100
    }

    // MARK: Running

    static func runningOverview(_ stats: RunningStatsDTO, kpis: RunKpiValues, plan: [PlanItemDTO], weeks count: Int = 8,
                                now: Date = AppClock.now, calendar: Calendar = .current) -> RunningOverview {
        let byWeek = Dictionary(stats.weekly.map { (String($0.weekStart.prefix(10)), $0.km ?? 0) }, uniquingKeysWith: +)
        let current = DayKey.weekStart(now, calendar: calendar)
        let starts = (0..<max(1, count)).reversed().map { i in
            DayKey.weekStart(calendar.date(byAdding: .day, value: -7 * i, to: now) ?? now, calendar: calendar)
        }
        let bars = starts.map { ProgressWeekBar(weekStart: $0, value: byWeek[$0] ?? 0, isCurrent: $0 == current) }
        let full = bars.filter { !$0.isCurrent }
        let average = full.contains { $0.value > 0 } ? full.map(\.value).reduce(0, +) / Double(full.count) : nil
        let lastWeekStart = DayKey.weekStart(calendar.date(byAdding: .day, value: -7, to: now) ?? now, calendar: calendar)

        let weekDays = Set((0..<7).compactMap { calendar.date(byAdding: .day, value: $0, to: DayKey.date(current, calendar: calendar) ?? now) }
            .map { DayKey.string($0, calendar: calendar) })
        let plannedKm = plan
            .filter { weekDays.contains($0.date) && $0.deletedAt == nil && WeekRunsModel.runKinds.contains($0.kind) && $0.planStatus != .skipped }
            .compactMap { $0.session?.distanceKm ?? $0.session?.totalKm }
            .reduce(0, +)

        let cutoff = DayKey.string(DayKey.daysAgo(28, from: now, calendar: calendar), calendar: calendar)
        let easy = stats.efficiency.filter { $0.date >= cutoff && $0.type?.lowercased() == "easy" && ($0.avgPaceSPerKm ?? 0) > 0 }
        return RunningOverview(
            kmThisWeek: kpis.kmThisWeek, kmPlannedThisWeek: plannedKm > 0 ? plannedKm : nil,
            kmLastWeek: byWeek[lastWeekStart], easyPaceSPerKm: easyPace(easy), easyRuns: easy.count,
            weeks: bars, averageKm: average)
    }

    /// Distance-weighted when the distances are known, a plain mean otherwise.
    static func easyPace(_ runs: [EfficiencyPointDTO]) -> Double? {
        let paced = runs.compactMap { r in r.avgPaceSPerKm.flatMap { $0 > 0 ? (pace: $0, m: r.distanceM ?? 0) : nil } }
        guard !paced.isEmpty else { return nil }
        let metres = paced.map(\.m).reduce(0, +)
        if metres > 0, paced.allSatisfy({ $0.m > 0 }) {
            return paced.map { $0.pace * $0.m }.reduce(0, +) / metres
        }
        return paced.map(\.pace).reduce(0, +) / Double(paced.count)
    }

    /// Runs done this week plus the planned runs not done yet, by date (done first on the same day).
    static func weekRuns(runs: [RunSummaryDTO], planned: [WeekRunRow], days: [String]) -> [ProgressWeekRun] {
        let week = Set(days)
        let done = runs.filter { $0.day.map(week.contains) ?? false }.map { r in
            ProgressWeekRun(id: "run-\(r.id)", date: r.day ?? "", title: r.title ?? "Run",
                            detail: [r.distanceM.map { "\(Fmt.km($0 / 1000)) km" }, r.avgPaceSPerKm.map { "\(Fmt.pace($0)) /km" }]
                                .compactMap { $0 }.joined(separator: " · "),
                            badge: nil, target: .run(id: r.id))
        }
        let open = planned.filter { !$0.isDone }.map { row in
            ProgressWeekRun(id: "plan-\(row.id)", date: row.date, title: row.title,
                            detail: [row.distanceText, row.structureText, row.hrLabel ?? row.paceText].compactMap { $0 }
                                .joined(separator: " · "),
                            badge: badge(row.state), target: .planned(row.item))
        }
        return (done + open).enumerated().sorted { a, b in
            if a.element.date != b.element.date { return a.element.date < b.element.date }
            if a.element.isDone != b.element.isDone { return a.element.isDone }
            return a.offset < b.offset
        }.map(\.element)
    }

    private static func badge(_ state: WeekRunRow.State) -> String? {
        switch state {
        case .today: "Today"
        case .missed: "Missed"
        case .skipped: "Skipped"
        case .done, .upcoming: nil
        }
    }
}

import Foundation

/// What the "This week's running" card on Today shows, merged from the cached week plan, the runs list and the
/// workouts finished on this device. Pure (no UI) so the rules are unit-tested.
public struct WeekRunRow: Identifiable, Equatable, Sendable {
    public enum State: Equatable, Sendable {
        /// Plan row done, or a run was logged for it.
        case done
        /// Planned for today, not run yet.
        case today
        case upcoming
        /// Past and not done.
        case missed
        /// The plan marks it skipped.
        case skipped
    }

    public var id: String
    public var item: PlanItemDTO
    public var date: String
    public var kind: String
    public var title: String
    public var state: State
    /// Date the session was moved from (`moved_from`), if any.
    public var movedFrom: String?
    /// Distance of the matched run in km.
    public var actualKm: Double?
    /// Day the run was actually done when that is not the planned day.
    public var ranOn: String?
    /// "8 km" (planned) or "8.4 km" (actual); has a trailing "*" when it is the last text shown and the item may change.
    public var distanceText: String?
    /// "5×1000 m" for intervals; carries the "*" when present.
    public var structureText: String?
    /// "Z1–Z2" or "124–152 bpm".
    public var hrLabel: String?
    /// "5:20–5:40" when no HR range is known.
    public var paceText: String?
    /// Future item that may still change (`adaptive`).
    public var asterisk: Bool

    public var isDone: Bool { state == .done }
    /// Nothing else to mark the asterisk on: the row shows it after the title.
    public var asteriskOnTitle: Bool { asterisk && distanceText == nil && structureText == nil }
}

public struct WeekLiftDay: Equatable, Identifiable, Sendable {
    public var id: String
    public var date: String
    public var kind: String
    public var done: Bool
}

public struct WeekRunsModel: Equatable, Sendable {
    public var rows: [WeekRunRow]
    public var lifts: [WeekLiftDay]

    public static let footnote = "* may change based on your last run, readiness and weekly volume"

    public var showsFootnote: Bool { rows.contains(where: \.asterisk) }

    /// "Upper Mon ✓ · Lower Tue ✓ · Upper Thu"; nil when no lifting is planned this week.
    public var liftLine: String? {
        guard !lifts.isEmpty else { return nil }
        return lifts.map { day in
            "\(PlanFormat.shortName(forKind: day.kind)) \(DayLabel.weekday(day.date))" + (day.done ? " ✓" : "")
        }.joined(separator: " · ")
    }

    public static let runKinds: Set<String> = ["easy_run", "intervals", "long_run"]

    public static func build(days: [String], items: [PlanItemDTO], runs: [RunSummaryDTO], finished: [FinishedDayRef],
                      today: String) -> WeekRunsModel {
        let inWeek = items.filter { days.contains($0.date) && $0.deletedAt == nil }
        return WeekRunsModel(rows: runRows(inWeek, days: days, runs: runs, today: today),
                             lifts: liftDays(inWeek, finished: finished, today: today))
    }

    // MARK: Runs

    private static func runRows(_ all: [PlanItemDTO], days: [String], runs: [RunSummaryDTO], today: String) -> [WeekRunRow] {
        let planned = all.filter { runKinds.contains($0.kind) }
        // A row that was moved away (status `moved`, no `moved_from`) is superseded by its moved copy of the same kind.
        let movedKinds = Set(planned.filter { $0.movedFrom != nil }.map(\.kind))
        let visible = planned.filter { !($0.planStatus == .moved && $0.movedFrom == nil && movedKinds.contains($0.kind)) }
            .sorted { ($0.date, order($0.kind)) < ($1.date, order($1.kind)) }

        // Only runs up to today can complete a session.
        var available = runs.filter {
            ($0.activityType == nil || ($0.activityType ?? "").lowercased().contains("run"))
                && ($0.day ?? "9999") <= today
        }
        // 1. A run on the planned day completes that session.
        var matched: [String: RunSummaryDTO] = [:]
        for item in visible {
            if let index = available.firstIndex(where: { $0.day == item.date }) { matched[item.cacheId] = available.remove(at: index) }
        }
        // 2. A session still open (not done/skipped) takes the best unmatched run within 2 days in the same week:
        //    a run of the same type first (or of unknown type), then the closest day.
        for item in visible where matched[item.cacheId] == nil && ![.done, .modified, .skipped].contains(item.planStatus) {
            let candidates = available.enumerated().compactMap { index, run -> (Int, Int, Int)? in
                guard let day = run.day, days.contains(day), let gap = dayGap(day, item.date), abs(gap) <= 2 else { return nil }
                let kind = inferredKind(run)
                if let kind, kind != item.kind { return nil }
                return (index, kind == nil ? 1 : 0, abs(gap))
            }
            if let best = candidates.min(by: { ($0.1, $0.2) < ($1.1, $1.2) }) {
                matched[item.cacheId] = available.remove(at: best.0)
            }
        }
        return visible.map { row($0, run: matched[$0.cacheId], today: today) }
    }

    private static func dayGap(_ a: String, _ b: String) -> Int? {
        guard let da = AppClock.date(fromDay: a), let db = AppClock.date(fromDay: b) else { return nil }
        return AppClock.calendar.dateComponents([.day], from: db, to: da).day
    }

    /// Run type from its title ("Intervals 5×1000", "Long run", "Easy run"); nil when the title says nothing.
    public static func inferredKind(_ run: RunSummaryDTO) -> String? {
        let t = (run.title ?? "").lowercased()
        if t.contains("interval") || t.contains("×") || t.contains("repeat") { return "intervals" }
        if t.contains("long") { return "long_run" }
        if t.contains("easy") || t.contains("recovery") { return "easy_run" }
        return nil
    }

    private static func order(_ kind: String) -> Int { ["easy_run": 0, "intervals": 1, "long_run": 2][kind] ?? 3 }

    private static func row(_ item: PlanItemDTO, run: RunSummaryDTO?, today: String) -> WeekRunRow {
        let state: WeekRunRow.State
        if item.planStatus == .done || item.planStatus == .modified || run != nil { state = .done }
        else if item.planStatus == .skipped { state = .skipped }
        else if item.date == today { state = .today }
        else if item.date < today { state = .missed }
        else { state = .upcoming }

        let session = item.session
        let actualKm = run?.distanceM.map { $0 / 1000 }
        let plannedKm = session?.distanceKm ?? session?.totalKm
        var distance = (actualKm ?? plannedKm).map { "\(Fmt.km($0)) km" }
        var structure: String?
        if item.kind == "intervals", let work = session?.structure?.work, let reps = work.reps, let dist = work.distanceM {
            structure = "\(reps)×\(Fmt.number(dist, maxFraction: 0)) m"
        }
        let asterisk = item.adaptive == true && (state == .today || state == .upcoming)
        if asterisk {
            if let s = structure { structure = s + "*" } else if let d = distance { distance = d + "*" }
        }
        // easy / long run strides follow the distance: "8 km* · 6 × 20 s strides"
        if item.kind != "intervals", let strides = PlanFormat.strides(session?.structure?.strides) {
            structure = strides
        }

        let hr = session?.hr ?? session?.structure?.work?.hr
        let pace = PlanFormat.pace(session?.pace) ?? session?.structure?.work?.paceLabel
        return WeekRunRow(id: item.cacheId, item: item, date: item.date, kind: item.kind,
                          title: PlanFormat.runTitle(item.kind), state: state, movedFrom: item.movedFrom,
                          actualKm: actualKm, ranOn: run?.day.flatMap { $0 == item.date ? nil : $0 }, distanceText: distance, structureText: structure,
                          hrLabel: HRZoneChip.label(for: hr), paceText: pace, asterisk: asterisk)
    }

    // MARK: Lifting

    private static func liftDays(_ all: [PlanItemDTO], finished: [FinishedDayRef], today: String) -> [WeekLiftDay] {
        let doneIds = Set(finished.compactMap(\.plannedId))
        let finishedDays = Set(finished.map(\.day))
        return all.filter { Domain.forKind($0.kind) == .lifting && $0.planStatus != .skipped }
            .sorted { $0.date < $1.date }
            .map { item in
                let done = item.planStatus == .done || doneIds.contains(item.cacheId)
                    || (item.date <= today && finishedDays.contains(item.date))
                return WeekLiftDay(id: item.cacheId, date: item.date, kind: item.kind, done: done)
            }
    }
}

/// Rules of the Today screen that are not about layout.
public enum TodayLogic: Sendable {
    /// The only workout control a lifting card on Today can show. Starting a workout lives in the Workout tab.
    public enum LiftCardAction: Equatable, Sendable { case done, resume, none }

    public static func liftCardAction(completed: Bool, hasActiveWorkout: Bool) -> LiftCardAction {
        if completed { return .done }
        return hasActiveWorkout ? .resume : .none
    }

    /// A stand-alone "Resume workout" card: only for a running workout that no lifting card on Today already resumes.
    public static func showsResumeCard(hasActiveWorkout: Bool, hasLiftingToday: Bool) -> Bool {
        hasActiveWorkout && !hasLiftingToday
    }
}

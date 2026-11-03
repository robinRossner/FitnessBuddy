import Foundation

/// One row of the "Start workout" chooser.
public struct WorkoutChoice: Identifiable, Equatable, Sendable {
    public enum Source: Equatable, Sendable {
        /// A cached planned lifting session (starting it links `planned_session_id`).
        case planned(String)
        /// A finished session used as a template (no lifting session is planned at all).
        case template(UUID)
        case empty
    }

    public var id: String
    public var source: Source
    public var title: String
    /// "planned Mon", "last done Thu", ...
    public var detail: String
    public var exerciseCount: Int?

    /// "Planned Mon · 5 exercises"
    public var subtitle: String {
        let first: String? = detail.isEmpty ? nil : String(detail.prefix(1)).uppercased() + String(detail.dropFirst())
        let count: String? = exerciseCount.map { "\($0) exercises" }
        return [first, count].compactMap { $0 }.joined(separator: " · ")
    }

    /// "Upper (planned Mon)"
    public var label: String { detail.isEmpty ? title : "\(title) (\(detail))" }
}

/// A cached planned lifting session, reduced to what the chooser needs.
public struct PlannedLiftRef: Equatable, Sendable {
    public var id: String
    public var date: String
    public var kind: String
    public var title: String
    public var done: Bool
    public var exerciseCount: Int

    public init(id: String, date: String, kind: String, title: String, done: Bool, exerciseCount: Int) {
        self.id = id
        self.date = date
        self.kind = kind
        self.title = title
        self.done = done
        self.exerciseCount = exerciseCount
    }
}

/// A finished session that can serve as a template.
public struct FinishedLiftRef: Equatable, Sendable {
    public var id: UUID
    public var kind: String
    public var name: String
    public var startedAt: Date

    public init(id: UUID, kind: String, name: String, startedAt: Date) {
        self.id = id
        self.kind = kind
        self.name = name
        self.startedAt = startedAt
    }
}

public enum WorkoutChooserLogic: Sendable {
    public static let liftingKinds: Set<String> = ["upper", "lower", "full"]
    public static let maxPlanned = 4

    /// Order: not-yet-done planned lifting sessions from today on (soonest first), then missed ones from earlier in the
    /// cached week (most recent first), then "Empty workout". Only when nothing planned is left, the most recent finished
    /// session of each kind (the one done longest ago first, so the rotation continues) are offered as templates.
    /// The first element is the preselected one.
    public static func choices(planned: [PlannedLiftRef], finished: [FinishedLiftRef], today: String,
                        locale: Locale = AppLocale.current, calendar: Calendar = AppClock.calendar) -> [WorkoutChoice] {
        let open = planned.filter { !$0.done && liftingKinds.contains($0.kind) }
        let upcoming = open.filter { $0.date >= today }.sorted { ($0.date, $0.title) < ($1.date, $1.title) }
        let missed = open.filter { $0.date < today }.sorted { ($0.date, $0.title) > ($1.date, $1.title) }
        var out: [WorkoutChoice] = (upcoming + missed).prefix(maxPlanned).map { p in
            WorkoutChoice(id: "planned-\(p.id)", source: .planned(p.id), title: p.title,
                          detail: "planned \(dayWord(p.date, today: today, locale: locale, calendar: calendar))",
                          exerciseCount: p.exerciseCount)
        }
        if out.isEmpty {
            out = templates(finished, locale: locale)
        }
        out.append(WorkoutChoice(id: "empty", source: .empty, title: "Empty workout", detail: "", exerciseCount: nil))
        return out
    }

    private static func templates(_ finished: [FinishedLiftRef], locale: Locale) -> [WorkoutChoice] {
        let newestFirst = finished.sorted { $0.startedAt > $1.startedAt }
        var byKind: [FinishedLiftRef] = []
        for kind in ["upper", "lower", "full"] {
            if let latest = newestFirst.first(where: { $0.kind == kind }) { byKind.append(latest) }
        }
        if byKind.isEmpty, let any = newestFirst.first { byKind = [any] }
        return byKind.sorted { $0.startedAt < $1.startedAt }.map { f in
            let name = f.name.isEmpty ? f.kind.capitalized : f.name
            return WorkoutChoice(id: "template-\(f.id.uuidString)", source: .template(f.id), title: name,
                                 detail: "repeat of \(f.startedAt.formatted(.dateTime.weekday(.abbreviated).locale(locale)))",
                                 exerciseCount: nil)
        }
    }

    public static func dayWord(_ day: String, today: String, locale: Locale, calendar: Calendar) -> String {
        if day == today { return "today" }
        guard let d = calendar.date(from: dateComponents(day)) else { return day }
        return d.formatted(.dateTime.weekday(.abbreviated).locale(locale))
    }

    private static func dateComponents(_ day: String) -> DateComponents {
        let p = day.split(separator: "-").compactMap { Int($0) }
        return p.count == 3 ? DateComponents(year: p[0], month: p[1], day: p[2], hour: 12) : DateComponents()
    }
}

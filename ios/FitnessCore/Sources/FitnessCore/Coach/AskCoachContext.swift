import Foundation

/// What an "Ask coach" question is about.
public struct AskCoachContext: Identifiable, Equatable, Sendable {
    public var scope: CoachScope
    public var refId: String?
    /// "Incline Dumbbell Press", "Easy run, Oct 24", "This week".
    public var subject: String
    /// The Coach tab lets the user pick general / week; entry points on a lift or run do not.
    public var allowsScopeSwitch = false

    public var id: String { scope.rawValue + "|" + (refId ?? "") }

    public static let general = AskCoachContext(scope: .general, refId: nil, subject: "Training in general", allowsScopeSwitch: true)
    /// The backend accepts any day of the week as `ref_id`, so today identifies this week.
    public static var thisWeek: AskCoachContext { .init(scope: .week, refId: AppClock.todayString, subject: "This week", allowsScopeSwitch: true) }
    public static func lift(id: Int, name: String) -> AskCoachContext { .init(scope: .lift, refId: String(id), subject: name) }
    public static func run(id: String, name: String) -> AskCoachContext { .init(scope: .run, refId: id, subject: name) }

    public var symbol: String {
        switch scope {
        case .lift: "dumbbell.fill"
        case .run: "figure.run"
        case .week: "calendar"
        case .general: "figure.mixed.cardio"
        }
    }
}

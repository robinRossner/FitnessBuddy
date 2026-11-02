import Foundation

/// "Oct 24" for a `YYYY-MM-DD` string.
public enum DayLabel: Sendable {
    /// "Fri"
    public static func weekday(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.weekday(.abbreviated))
    }
    public static func short(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).day())
    }
    public static func medium(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).day().year())
    }
    public static func monthYear(_ day: String?) -> String {
        guard let day, let d = DayKey.date(day) else { return "–" }
        return d.formatted(.dateTime.month(.abbreviated).year())
    }
}

import Foundation

/// Date helpers for plan days ("YYYY-MM-DD", Monday-based weeks). DEBUG builds can pin "today"
/// (used by the demo data so the Today screen shows a lifting day regardless of the real weekday), and freeze the clock
/// (`-uiFixedNow`, set by `UITestSupport`) so UI-test screenshots do not change between runs.
public enum AppClock: Sendable {
    public static let overrideKey = "debug.todayOverride"

    #if DEBUG
    /// Frozen instant for UI tests; set once at launch, before anything reads the clock.
    nonisolated(unsafe) public static var fixedNow: Date?
    #endif

    public static var calendar: Calendar {
        var c = Calendar(identifier: .iso8601)
        c.timeZone = .current
        return c
    }

    public static func dayString(_ date: Date) -> String {
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }

    public static func date(fromDay s: String) -> Date? {
        let parts = s.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2], hour: 12))
    }

    /// The current instant as the UI sees it: the frozen time under `-uiFixedNow`, else the real time. Ignores the
    /// day pin of `overrideKey`, so freshness labels and cache timestamps keep real elapsed time in demo mode.
    public static var instant: Date {
        #if DEBUG
        if let fixedNow { return fixedNow }
        #endif
        return Date()
    }

    public static var now: Date {
        #if DEBUG
        if let fixedNow { return fixedNow }
        if let s = UserDefaults.standard.string(forKey: overrideKey), let d = date(fromDay: s) { return d }
        #endif
        return Date()
    }

    public static var todayString: String { dayString(now) }

    public static func weekStart(of date: Date) -> Date {
        calendar.dateInterval(of: .weekOfYear, for: date)?.start ?? date
    }

    /// Monday ... Sunday day strings of the week containing `date`.
    public static func weekDays(containing date: Date) -> [String] {
        let start = weekStart(of: date)
        return (0..<7).compactMap { calendar.date(byAdding: .day, value: $0, to: start) }.map(dayString)
    }
}

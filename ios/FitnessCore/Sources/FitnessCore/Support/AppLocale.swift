import Foundation

/// The one fixed display style of the app, whatever region the phone is set to: English, 24-hour clock,
/// day before month ("Wed 4 Nov", "09:41"), decimal dot ("78.4"). Every formatter that does not read the SwiftUI
/// environment (`.formatted(...)`, `RelativeDateTimeFormatter`, ...) gets `AppLocale.current` explicitly; the app root
/// also sets it as `\.locale` so `Text(value, format:)`, pickers and chart axes follow.
public enum AppLocale: Sendable {
    public static let current = Locale(identifier: "en_GB")

    /// `.dateTime` bound to the app locale and calendar: `AppLocale.dateTime.day().month(.abbreviated)`.
    public static var dateTime: Date.FormatStyle {
        // Gregorian, not AppClock's ISO 8601 calendar: that one has no localised date patterns ("Nov 4" instead of "4 Nov").
        var calendar = Calendar(identifier: .gregorian)
        calendar.locale = current
        calendar.timeZone = .current
        return Date.FormatStyle(locale: current, calendar: calendar, timeZone: calendar.timeZone)
    }

    /// "Wednesday, 4 Nov" (or "Wednesday, 4 November" with `.wide`).
    public static func weekdayDate(_ date: Date, month: Date.FormatStyle.Symbol.Month = .abbreviated) -> String {
        date.formatted(dateTime.weekday(.wide)) + ", " + date.formatted(dateTime.day().month(month))
    }

    /// "4 Nov, 09:41" (with `seconds`: "4 Nov, 09:41:07").
    public static func dayTime(_ date: Date, seconds: Bool = false) -> String {
        date.formatted(dateTime.day().month(.abbreviated)) + ", "
            + date.formatted(seconds ? dateTime.hour().minute().second() : dateTime.hour().minute())
    }

    /// "09:41"
    public static func time(_ date: Date) -> String { date.formatted(dateTime.hour().minute()) }
}

import Foundation
import Testing
@testable import FitnessCore

/// Numbers, dates and times use one fixed English style (en_GB, 24-hour, dot) whatever the device region is.
@Suite(.tags(.stats, .fast))
struct AppLocaleTests {
    /// Wednesday 4 Nov 2026, 09:41 in the current time zone.
    private let date = AppClock.calendar.date(from: DateComponents(year: 2026, month: 11, day: 4, hour: 9, minute: 41, second: 7))!

    @Test func numbersUseADot() {
        #expect(Fmt.number(78.4) == "78.4")
        #expect(Fmt.number(2.25) == "2.25")
        #expect(Fmt.hours(24480) == "6.8")
        #expect(Fmt.volume(19_893) == "19.9 t")
    }

    @Test func datesAreDayBeforeMonthInEnglish() {
        #expect(AppLocale.weekdayDate(date) == "Wednesday, 4 Nov")
        #expect(AppLocale.weekdayDate(date, month: .wide) == "Wednesday, 4 November")
        #expect(date.formatted(AppLocale.dateTime.weekday(.abbreviated).day().month(.abbreviated)) == "Wed 4 Nov")
        #expect(date.formatted(AppLocale.dateTime.month(.abbreviated).day().year()) == "4 Nov 2026")
        #expect(DayLabel.short("2026-11-04") == "4 Nov")
    }

    @Test func timesAreTwentyFourHour() {
        #expect(AppLocale.time(date) == "09:41")
        #expect(AppLocale.dayTime(date, seconds: true) == "4 Nov, 09:41:07")
    }
}

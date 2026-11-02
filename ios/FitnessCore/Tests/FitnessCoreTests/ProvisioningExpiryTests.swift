import Foundation
import Testing
@testable import FitnessCore

@Suite(.tags(.fast))
struct ProvisioningExpiryTests {
    private let calendar: Calendar = {
        var c = Calendar(identifier: .iso8601)
        c.timeZone = TimeZone(identifier: "Europe/Berlin")!
        return c
    }()

    private func date(_ day: Int, hour: Int) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour))!
    }

    /// CMS envelope around the plist: binary bytes before and after, as in a real profile.
    private func profile(_ body: String) -> Data {
        var data = Data([0x30, 0x82, 0x0F, 0x00, 0xFF])
        data.append(Data("""
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>\(body)</dict></plist>
        """.utf8))
        data.append(Data([0xA0, 0x82, 0x00, 0xFF]))
        return data
    }

    @Test func readsExpirationDateFromSignedProfile() {
        let data = profile("<key>Name</key><string>x</string><key>ExpirationDate</key><date>2026-11-09T10:30:00Z</date>")
        #expect(ProvisioningExpiry.expirationDate(fromProfile: data) == ISO8601DateFormatter().date(from: "2026-11-09T10:30:00Z"))
    }

    @Test func unreadableProfilesGiveNil() {
        #expect(ProvisioningExpiry.expirationDate(fromProfile: Data()) == nil)
        #expect(ProvisioningExpiry.expirationDate(fromProfile: Data([0x30, 0x82])) == nil)
        #expect(ProvisioningExpiry.expirationDate(fromProfile: profile("<key>Name</key><string>x</string>")) == nil)
    }

    @Test func countsCalendarDays() {
        let now = date(2, hour: 23)
        #expect(ProvisioningExpiry.daysLeft(until: date(9, hour: 1), from: now, calendar: calendar) == 7)
        #expect(ProvisioningExpiry.daysLeft(until: date(3, hour: 1), from: now, calendar: calendar) == 1)
        #expect(ProvisioningExpiry.daysLeft(until: date(2, hour: 23), from: now, calendar: calendar) == 0)
        #expect(ProvisioningExpiry.daysLeft(until: date(1, hour: 12), from: now, calendar: calendar) == 0)
    }

    @Test func labels() {
        #expect(ProvisioningExpiry.label(daysLeft: 0) == "today")
        #expect(ProvisioningExpiry.label(daysLeft: 1) == "tomorrow")
        #expect(ProvisioningExpiry.label(daysLeft: 5) == "in 5 days")
    }
}

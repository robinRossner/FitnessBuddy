import Foundation
import Testing
@testable import FitnessCore

@Suite(.tags(.stats, .fast))
struct StatsFreshnessTests {
    let now = date("2026-10-25T12:00:00.000Z")

    @Test func freshWithinFifteenMinutes() {
        #expect(StatsFreshness.isFresh(fetchedAt: now.addingTimeInterval(-60), now: now))
        #expect(StatsFreshness.isFresh(fetchedAt: now.addingTimeInterval(-14 * 60), now: now))
        #expect(!StatsFreshness.isFresh(fetchedAt: now.addingTimeInterval(-15 * 60), now: now))
        #expect(!StatsFreshness.isFresh(fetchedAt: nil, now: now))
        #expect(!StatsFreshness.isFresh(fetchedAt: now.addingTimeInterval(120), now: now))  // clock went backwards
        #expect(!StatsFreshness.isFresh(fetchedAt: now.addingTimeInterval(-1), now: now, maxAge: 0))
    }

    @Test func relativeLabels() {
        #expect(StatsFreshness.relative(now.addingTimeInterval(-20), now: now) == "just now")
        #expect(StatsFreshness.relative(now.addingTimeInterval(-5 * 60), now: now) == "5 min ago")
        #expect(StatsFreshness.relative(now.addingTimeInterval(-2 * 3600 - 100), now: now) == "2 h ago")
        #expect(StatsFreshness.relative(now.addingTimeInterval(-3 * 86_400), now: now) == "3 d ago")
        #expect(StatsFreshness.label(fetchedAt: now.addingTimeInterval(-2 * 3600), offline: false, now: now) == "Updated 2 h ago")
        #expect(StatsFreshness.label(fetchedAt: nil, offline: false, now: now) == nil)
        #expect(StatsFreshness.label(fetchedAt: nil, offline: true, now: now) == "Offline")
    }

    @Test func offlineLabelNamesTheCachedTime() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let earlier = date("2026-10-24T14:03:00.000Z")
        #expect(StatsFreshness.label(fetchedAt: earlier, offline: true, now: now, calendar: cal) == "Offline, showing data from Oct 24, 14:03")
        let sameDay = date("2026-10-25T08:30:00.000Z")
        #expect(StatsFreshness.label(fetchedAt: sameDay, offline: true, now: now, calendar: cal) == "Offline, showing data from 08:30")
    }
}

import Foundation
import Testing
@testable import FitnessBuddy

/// The Health tab's period picker: every card's chart and average come from the same window of points.
@Suite(.tags(.health, .fast))
struct HealthSignalsTests {
    /// Sunday noon.
    let now = DayKey.date("2026-10-25")!.addingTimeInterval(12 * 3600)

    private func points(_ count: Int) -> [MetricPoint] {
        (0..<count).map { n in MetricPoint(day: DayKey.string(DayKey.daysAgo(n, from: now)), value: Double(n)) }
    }

    @Test func windowKeepsOnlyTheLastNDaysOldestFirst() {
        let pts = points(100)
        for span in HealthSignals.Span.allCases {
            let w = HealthSignals.window(pts, days: span.rawValue, now: now)
            #expect(w.count == span.rawValue)
            #expect(w.first?.day == DayKey.string(DayKey.daysAgo(span.rawValue - 1, from: now)))
            #expect(w.last?.day == "2026-10-25")
        }
    }

    @Test func averageChangesWithThePickedSpan() {
        let pts = points(100)   // value = days ago
        let week = HealthSignals.average(HealthSignals.window(pts, days: 7, now: now))
        let month = HealthSignals.average(HealthSignals.window(pts, days: 30, now: now))
        let quarter = HealthSignals.average(HealthSignals.window(pts, days: 90, now: now))
        #expect(week == 3)
        #expect(month == 14.5)
        #expect(quarter == 44.5)
        #expect(HealthSignals.average([]) == nil)
    }

    @Test func sparseDataStaysInsideTheWindow() {
        let pts = [MetricPoint(day: "2026-10-25", value: 80), MetricPoint(day: "2026-10-01", value: 82),
                   MetricPoint(day: "2026-07-01", value: 90)]
        #expect(HealthSignals.window(pts, days: 7, now: now).count == 1)
        #expect(HealthSignals.window(pts, days: 30, now: now).count == 2)
        #expect(HealthSignals.window(pts, days: 90, now: now).count == 2)
    }

    @Test func deltaAgainstBaseline() {
        #expect(HealthSignals.deltaPct(55, baseline: 50) == 10)
        #expect(HealthSignals.deltaPct(nil, baseline: 50) == nil)
        #expect(HealthSignals.deltaPct(5, baseline: 0) == nil)
    }

    @Test func chartDomainsAndScale() {
        let d = HealthSignals.chartDomain(days: 7, now: now)
        // Seven calendar days (not 7 × 86,400 s: a DST change inside the window adds or drops an hour).
        #expect(Calendar.current.dateComponents([.day], from: d.lowerBound, to: d.upperBound).day == 7)
        let bars = HealthSignals.yDomain(values: [4000, 9000], average: 6500, fromZero: true)
        #expect(bars.lowerBound == 0 && bars.upperBound > 9000)
        let line = HealthSignals.yDomain(values: [50, 60], average: 55, fromZero: false)
        #expect(line.lowerBound < 50 && line.upperBound > 60)
        let flat = HealthSignals.yDomain(values: [70, 70], average: 70, fromZero: false)
        #expect(flat.lowerBound < 70 && flat.upperBound > 70)
    }
}

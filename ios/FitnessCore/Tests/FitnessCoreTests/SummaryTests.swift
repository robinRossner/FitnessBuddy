import Foundation
import Testing
@testable import FitnessCore

/// Metric bucketing for the W / M / 6M / Y detail chart, card sparklines and the Summary highlights.
@Suite(.tags(.health, .fast))
struct MetricSeriesTests {
    /// Sunday; its week starts Monday 2026-10-19.
    let now = DayKey.date("2026-10-25")!.addingTimeInterval(12 * 3600)

    @Test func weekRangeAveragesDailyValuesAndKeepsGaps() {
        let pts = [MetricPoint(day: "2026-10-25", value: 10), MetricPoint(day: "2026-10-24", value: 20),
                   MetricPoint(day: "2026-10-24", value: 30), MetricPoint(day: "2026-10-01", value: 99)]
        let b = MetricSeries.buckets(pts, metric: MetricCatalog.hrv, range: .week, now: now)
        #expect(b.count == 7)
        #expect(b.last?.value == 10)
        #expect(b[5].value == 25)
        #expect(b.first?.value == nil)
        #expect(Set(b.map(\.key)).count == 7)
        #expect(MetricSeries.headline(b, aggregation: .average) == 17.5)
    }

    @Test func weeklyTotalsCutIntoWeeksAndMonths() {
        let km = MetricCatalog.runningDistance
        let pts = [MetricPoint(day: "2026-10-19", value: 12), MetricPoint(day: "2026-10-12", value: 8),
                   MetricPoint(day: "2026-09-28", value: 5)]
        let month = MetricSeries.buckets(pts, metric: km, range: .month, now: now)
        #expect(month.map(\.value) == [5, nil, 8, 12])
        #expect(MetricSeries.headline(month, aggregation: .total) == 25)
        let year = MetricSeries.buckets(pts, metric: km, range: .year, now: now)
        #expect(year.count == 12)
        #expect(year.last?.value == 20)
        #expect(year[10].value == 5)
        #expect(MetricSeries.buckets(pts, metric: km, range: .halfYear, now: now).count == 26)
        #expect(km.ranges == [.month, .halfYear, .year])
        #expect(MetricCatalog.hrv.ranges == MetricRange.allCases)
    }

    @Test func dailyMetricInHalfYearAndYearUsesCalendarWeeksAndMonths() {
        let pts = [MetricPoint(day: "2026-10-19", value: 7), MetricPoint(day: "2026-10-25", value: 9),
                   MetricPoint(day: "2026-10-01", value: 8)]
        let weeks = MetricSeries.buckets(pts, metric: MetricCatalog.sleep, range: .halfYear, now: now)
        #expect(weeks.last?.value == 8)
        let months = MetricSeries.buckets(pts, metric: MetricCatalog.sleep, range: .year, now: now)
        #expect(months.last?.value == 8)
    }

    @Test func sparklineAndTimeLabels() {
        let km = MetricCatalog.runningDistance
        let weekly = MetricSeries.sparkline([MetricPoint(day: "2026-10-12", value: 10)], metric: km, now: now)
        #expect(weekly == [0, 0, 0, 0, 0, 0, 10, 0])
        #expect(MetricSeries.sparkline([], metric: km, now: now).isEmpty)
        let daily = MetricSeries.sparkline([MetricPoint(day: "2026-10-24", value: 60), MetricPoint(day: "2026-10-25", value: 70),
                                            MetricPoint(day: "2026-09-01", value: 1)], metric: MetricCatalog.hrv, now: now)
        #expect(daily == [60, 70])
        #expect(MetricSeries.timeLabel(MetricPoint(day: "2026-10-25", value: 1), metric: MetricCatalog.hrv, now: now) == "Today")
        #expect(MetricSeries.timeLabel(MetricPoint(day: "2026-10-24", value: 1), metric: MetricCatalog.hrv, now: now) == "Yesterday")
        #expect(MetricSeries.timeLabel(MetricPoint(day: "2026-10-19", value: 1), metric: km, now: now) == "This Week")
        #expect(MetricSeries.timeLabel(MetricPoint(day: "2026-10-12", value: 1), metric: km, now: now) == "Last Week")
        #expect(MetricSeries.timeLabel(nil, metric: km, now: now) == nil)
    }

    @Test func catalogReadsRealPayloads() {
        let health = HealthStatsDTO(generatedAt: nil, days: [HealthDayDTO(date: "2026-10-25", hrvMs: 66, sleepS: 27_000)],
                                    bodyweight: [BodyweightPointDTO(date: "2026-10-20", weightKg: 78.4, source: "garmin")],
                                    summary: nil, readinessToday: nil)
        let volume = WeeklyVolumeDTO(generatedAt: nil, weeks: [WeekVolumeDTO(weekStart: "2026-10-19", liftingVolumeKg: 12_500, liftingSessions: 2)])
        let s = MetricCatalog.Sources(health: health, volume: volume)
        #expect(MetricCatalog.points(MetricCatalog.hrv, from: s).map(\.value) == [66])
        #expect(MetricCatalog.points(MetricCatalog.sleep, from: s).map(\.value) == [7.5])
        #expect(MetricCatalog.points(MetricCatalog.restingHR, from: s).isEmpty)
        #expect(MetricCatalog.points(MetricCatalog.bodyweight, from: s).map(\.value) == [78.4])
        #expect(MetricCatalog.points(MetricCatalog.liftingSessions, from: s).map(\.value) == [2])
        #expect(MetricCatalog.points(MetricCatalog.liftingVolume, from: s).map(\.value) == [12.5])
        #expect(MetricCatalog.all.allSatisfy { MetricCatalog.info($0.id) != nil })
        #expect(Set(MetricCatalog.all.map(\.id)).count == MetricCatalog.all.count)
    }
}

@Suite(.tags(.health, .fast))
struct SummaryLogicTests {
    let now = DayKey.date("2026-10-25")!.addingTimeInterval(12 * 3600)

    @Test func pinnedIdsDropUnknownAndDuplicates() {
        #expect(SummaryLogic.pinnedIds("hrv,foo,hrv,sleep") == ["hrv", "sleep"])
        #expect(SummaryLogic.pinnedIds("") == [])
        #expect(SummaryLogic.pinnedIds(SummaryLogic.defaultPinnedRaw) == MetricCatalog.defaultPinned)
    }

    @Test func runningHighlightComparesLastWeekWithEightWeekAverage() throws {
        // last full week = 2026-10-12; the 8 weeks before it run 10 km each
        var weeks = [WeekVolumeDTO(weekStart: "2026-10-19", runningKm: 3), WeekVolumeDTO(weekStart: "2026-10-12", runningKm: 20)]
        for w in 2...9 {
            weeks.append(WeekVolumeDTO(weekStart: DayKey.weekStart(DayKey.daysAgo(7 * w, from: now)), runningKm: 10))
        }
        let h = try #require(SummaryLogic.running(weeks, now: now))
        #expect(h.sentence.contains("more"))
        #expect(h.bars.map(\.fraction) == [1, 0.5])
        #expect(h.target == .metric("km"))
        #expect(SummaryLogic.running(Array(weeks.prefix(4)), now: now) == nil)   // too little history
    }

    @Test func sleepHighlightNeedsBothAverages() throws {
        #expect(SummaryLogic.sleep(nil) == nil)
        #expect(SummaryLogic.sleep(HealthSummaryDTO(sleep7dS: 27_000)) == nil)
        let same = try #require(SummaryLogic.sleep(HealthSummaryDTO(sleep7dS: 27_000, sleep28dS: 27_200)))
        #expect(same.sentence.contains("about"))
        let less = try #require(SummaryLogic.sleep(HealthSummaryDTO(sleep7dS: 21_600, sleep28dS: 27_000)))
        #expect(less.sentence.contains("less"))
    }

    @Test func liftingHighlightPicksTheBiggestNonStalledGain() throws {
        let lifts = [
            LiftSummaryDTO(variantId: 1, name: "Bench Press @ Gym 3", latestE1rm: 100, trend4wPct: 4, stalled: false, inProgram: true),
            LiftSummaryDTO(variantId: 2, name: "Squat", latestE1rm: 140, trend4wPct: 9, stalled: true, inProgram: true),
            LiftSummaryDTO(variantId: 3, name: "Row", latestE1rm: 80, trend4wPct: 12, stalled: false, inProgram: false),
        ]
        let h = try #require(SummaryLogic.lifting(lifts, now: now))
        #expect(h.sentence.hasPrefix("Your Bench Press is up"))
        #expect(h.bars.first?.value.hasPrefix("100") == true)
        #expect(SummaryLogic.lifting([], now: now) == nil)
    }

    @Test func nextLiftSkipsDoneSessions() throws {
        let refs = [PlannedLiftRef(id: "a", date: "2026-10-25", kind: "upper", title: "Upper", done: true, exerciseCount: 5),
                    PlannedLiftRef(id: "b", date: "2026-10-27", kind: "lower", title: "Lower", done: false, exerciseCount: 6)]
        let next = try #require(SummaryLogic.nextLift(planned: refs, today: "2026-10-25", locale: Locale(identifier: "en_US")))
        #expect(next.id == "b")
        #expect(next.when == "Tue")
        #expect(next.subtitle == "6 exercises")
        #expect(SummaryLogic.nextLift(planned: [], today: "2026-10-25") == nil)
    }

    @Test func strongestInsightUsesLargestAbsoluteR() {
        let pts = (0..<5).map { CorrelationPointDTO(date: "2026-10-0\($0 + 1)", x: Double($0), y: Double($0)) }
        let items = [CorrelationDTO(id: "a", title: "A", points: pts, r: 0.3), CorrelationDTO(id: "b", title: "B", points: pts, r: -0.7),
                     CorrelationDTO(id: "c", title: "C", points: [], r: 0.9),
                     CorrelationDTO(id: "sleep_score_vs_readiness", title: "D", points: pts, r: 0.95)]
        #expect(InsightTransforms.strongest(items)?.id == "b")
        #expect(InsightTransforms.strongest([CorrelationDTO(id: "x", title: "X", points: pts, r: 0.1)]) == nil)
        let tail = CorrelationDTO(id: "t", title: "T", r: 0.5, insight: "More sleep goes with better days (r=0.5, n=11)")
        #expect(tail.headline == "More sleep goes with better days")
        #expect(tail.strengthLabel == "Moderate positive")
    }
}

import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

// MARK: - Decoding of the new optional fields

struct NewFieldDecodingTests {
    @Test func liftsFixtureCarriesRegionAndProgram() throws {
        let r = try Fixture.decode(LiftsStatsDTO.self, "stats_lifts")
        let first = try #require(r.lifts.first)
        #expect(first.bodyRegion == "Arms" && first.inProgram == true && first.trend4wPct == 0)
        #expect(r.lifts.filter { $0.inProgram == true }.count > 5)
    }

    @Test func volumeFixtureCarriesPlanAndPRs() throws {
        let r = try Fixture.decode(WeeklyVolumeDTO.self, "stats_volume_weekly")
        let last = try #require(r.weeks.last)
        #expect(last.plannedLiftingSessions == 3 && last.prs == 0 && last.prLifts?.value == 0 && last.liftingSessions == 2)
    }

    @Test func runningFixtureCarriesKpisAndRunDetail() throws {
        let r = try Fixture.decode(RunningStatsDTO.self, "stats_running")
        let k = try #require(r.kpis)
        #expect(k.vdotCurrent == 41.0 && k.vdot4wAgo == 41.0 && k.vdotChange == 0)
        #expect(k.efficiency4wAgo == 1.2982 && k.efficiencyChangePct == -2.51 && k.easyHrDriftFlags4w?.value == 0)
        #expect(k.runsThisWeek == 1 && k.runsPlannedThisWeek == 3 && k.kmThisWeek == 8.13)
        let detail = try #require(r.weekly.last?.runsDetail)
        #expect(detail.count == 1 && detail[0].type == .long && detail[0].km == 8.13)
        #expect(r.weekly.last?.runsByType?.value(.long) == 1)
    }

    @Test func healthFixtureCarriesReadinessTodayAndSteps() throws {
        let r = try Fixture.decode(HealthStatsDTO.self, "stats_health")
        let rt = try #require(r.readinessToday)
        #expect(rt.headline == "Ready" && rt.level == "green" && rt.metrics.map(\.key) == ["hrv", "rhr", "sleep"])
        #expect(rt.metrics[0].status == .ok && rt.metrics[0].deltaPct == -3.9 && rt.details.count == 4)
        #expect(r.days.last?.steps7dAvg == 10871)
        #expect(r.summary?.steps7dAvg == 10871 && r.summary?.days10kLast7 == 4)
    }

    @Test func planTodayFixtureCarriesReadinessToday() throws {
        let r = try Fixture.decode(PlanTodayDTO.self, "plan_today")
        #expect(r.readinessToday != nil)
    }

    @Test func digitKeysUseExplicitCodingKeys() throws {
        let k = try decode(RunKpisDTO.self, #"{"vdot_current":50,"vdot_4w_ago":49,"efficiency_4w_ago":1.2,"easy_hr_drift_flags_4w":[{"a":1},{"b":2}],"runs_this_week":2}"#)
        #expect(k.vdot4wAgo == 49 && k.efficiency4wAgo == 1.2 && k.easyHrDriftFlags4w?.value == 2 && k.runsThisWeek == 2)
        let s = try decode(HealthSummaryDTO.self, #"{"steps_7d_avg":9000.5,"days_10k_last_7":3}"#)
        #expect(s.steps7dAvg == 9000.5 && s.days10kLast7 == 3)
        let d = try decode(HealthDayDTO.self, #"{"date":"2026-10-25","steps_7d_avg":8000}"#)
        #expect(d.steps7dAvg == 8000)
    }

    @Test func oldPayloadsWithoutTheNewFieldsStillDecode() throws {
        let lift = try decode(LiftSummaryDTO.self, #"{"variant_id":1,"name":"Squat","trend_4w_pct":2.5}"#)
        #expect(lift.bodyRegion == nil && lift.inProgram == nil && lift.trend4wPct == 2.5)
        let week = try decode(WeekVolumeDTO.self, #"{"week_start":"2026-10-19","lifting_sessions":2}"#)
        #expect(week.prs == nil && week.plannedLiftingSessions == nil && week.prLifts == nil)
        let run = try decode(RunningStatsDTO.self, #"{"weekly":[{"week_start":"2026-10-19","km":5,"runs":1}]}"#)
        #expect(run.kpis == nil && run.weekly[0].runsDetail == nil && run.weekly[0].runsByType == nil)
        let health = try decode(HealthStatsDTO.self, #"{"days":[{"date":"2026-10-25"}]}"#)
        #expect(health.readinessToday == nil && health.days[0].steps7dAvg == nil && health.summary == nil)
        let corr = try decode(CorrelationDTO.self, #"{"id":"a","title":"A"}"#)
        #expect(corr.category == nil && corr.insight == nil)
        let today = try decode(PlanTodayDTO.self, #"{"date":"2026-10-25","sessions":[]}"#)
        #expect(today.readinessToday == nil)
    }

    @Test func malformedAdditiveBlocksDoNotBlankTheScreen() throws {
        let run = try decode(RunningStatsDTO.self, #"{"weekly":[],"kpis":"oops"}"#)
        #expect(run.kpis == nil)
        let today = try decode(PlanTodayDTO.self, #"{"date":"2026-10-25","sessions":[],"readiness_today":{"metrics":[{"label":"x"}]}}"#)
        #expect(today.readinessToday == nil)
        let type = try decode(RunDetailDTO.self, #"{"run_id":"r","type":"tempo","km":3}"#)
        #expect(type.type == .other)
    }

    @Test func nullKpisAndEmptyReadinessDecode() throws {
        let k = try decode(RunKpisDTO.self, #"{"vdot_current":null,"vdot_4w_ago":null,"efficiency_current":null}"#)
        #expect(k.vdotCurrent == nil && k.efficiencyCurrent == nil)
        let r = try decode(ReadinessTodayDTO.self, #"{"level":"unknown","headline":"No data","metrics":[],"details":["No data"]}"#)
        #expect(r.metrics.isEmpty && r.headline == "No data")
    }

    @Test func demoStatsCarryTheNewFields() throws {
        let running = try APICoding.makeDecoder().decode(RunningStatsDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/running")))
        #expect(running.kpis?.vdot4wAgo == 49.8 && running.weekly.last?.runsDetail?.isEmpty == false)
        let health = try APICoding.makeDecoder().decode(HealthStatsDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/health")))
        #expect(health.readinessToday?.headline == "Take it easier")
    }
}

// MARK: - Readiness

struct ReadinessSummaryTests {
    @Test func headlineMetricsAndDetailsFromReadinessToday() throws {
        let dto = try Fixture.decode(HealthStatsDTO.self, "stats_health").readinessToday
        let s = try #require(ReadinessSummary.make(today: dto, legacy: nil, day: nil))
        #expect(s.level == .green && s.headline == "Ready" && s.metrics.count == 3 && s.details.count == 4)
        let chips = s.metrics.map { "\($0.chipLabel) \($0.chipValue)" }
        #expect(chips == ["HRV 66 ms", "RHR 53", "Sleep \(Fmt.number(7.4, maxFraction: 1)) h"])
        #expect(s.metrics[0].chipDelta == MetricDelta(up: false, text: "4%"))
        #expect(s.metrics[1].chipDelta == MetricDelta(up: true, text: "1"))     // bpm: absolute beats
        #expect(s.metrics[2].chipDelta == nil)                                  // sleep: no delta
    }

    @Test func fallsBackToLegacyColourAndSignals() throws {
        let legacy = ReadinessDTO(date: "2026-10-26", color: "yellow", known: true, reason: "HRV 14% below baseline, sleep 5.9 h",
                                  signals: [SignalDTO(name: "hrv", level: "yellow", detail: "HRV 62 ms (-14% vs 72 baseline)", value: 62, baseline: 72),
                                            SignalDTO(name: "sleep", level: "yellow", detail: "sleep 5.9 h", value: 5.9, baseline: 7.6)])
        let s = try #require(ReadinessSummary.make(today: nil, legacy: legacy, day: nil))
        #expect(s.level == .yellow && s.headline == "Take it easier")
        #expect(s.metrics.map(\.key) == ["hrv", "sleep"] && s.metrics[0].resolvedStatus == .warn)
        #expect(s.details.first == "HRV 14% below baseline, sleep 5.9 h")
    }

    @Test func headlineInfersTheLevelAndHealthDayFillsMetrics() throws {
        let s = try #require(ReadinessSummary.make(today: ReadinessTodayDTO(level: nil, headline: "Recover"), legacy: nil, day: nil))
        #expect(s.level == .red)
        let health = try Fixture.decode(HealthStatsDTO.self, "stats_health")
        let day = HealthTransforms.latestReadinessDay(health.days)
        let fromDay = try #require(ReadinessSummary.make(today: nil, legacy: nil, day: day))
        #expect(fromDay.level == .green && fromDay.headline == "Ready" && fromDay.metrics.map(\.key) == ["hrv", "rhr", "sleep"])
        #expect(ReadinessSummary.make(today: nil, legacy: nil, day: nil) == nil)
    }

    @Test func whyListNamesEveryFactorEvenFromAnOlderBackend() throws {
        let metrics = [ReadinessMetricDTO(key: "hrv", label: "HRV", value: 51, unit: "ms", baseline: 79, deltaPct: -36, status: .bad),
                       ReadinessMetricDTO(key: "sleep", label: "Sleep", value: 5.1, unit: "h", baseline: 7.9, deltaPct: nil, status: .bad)]
        // an old backend lists only the flagged factors; resting HR has no metric, so it becomes a "no data" line
        let old = ["HRV 51 ms (-36% vs 79 baseline)", "Sleep 5.1 h, score 41"]
        let dto = ReadinessTodayDTO(level: "red", headline: "Recover", metrics: metrics, details: old)
        let s = try #require(ReadinessSummary.make(today: dto, legacy: nil, day: nil))
        #expect(s.details == old + ["Resting HR: no data"])
        // a complete list (current backend) is left untouched, as are the empty / no-data answers
        let full = old + ["Resting HR 54 (+2 vs 52) - neutral"]
        #expect(ReadinessSummary.completeDetails(full, metrics: metrics) == full)
        #expect(ReadinessSummary.completeDetails(["No data"], metrics: []) == ["No data"])
        #expect(ReadinessSummary.completeDetails([], metrics: metrics).isEmpty)
        // a missing line for a factor that has a metric is rebuilt from it
        let rhr = ReadinessMetricDTO(key: "rhr", label: "Resting HR", value: 54, unit: "bpm", baseline: 52, deltaPct: 3.8, status: .ok)
        #expect(ReadinessSummary.completeDetails(old, metrics: metrics + [rhr]).last == "Resting HR 54 bpm (baseline 52 bpm) - neutral")
    }

    @Test func noDataFromTheBackendStaysEmpty() throws {
        let dto = ReadinessTodayDTO(level: "unknown", headline: "No data", metrics: [], details: ["No data"])
        let s = try #require(ReadinessSummary.make(today: dto, legacy: nil, day: nil))
        #expect(s.level == .unknown && s.headline == "No data" && s.metrics.isEmpty)
    }
}

// MARK: - Zone chip

struct ZoneChipTests {
    @Test func planNamedZoneWins() {
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: "Z1-Z2", low: 127, high: 156, basis: "lthr")) == "Z1–Z2")
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: "Z4-Z5a", low: 166, high: 179, basis: nil)) == "Z4–Z5a")
        #expect(HRZoneChip.highestZone(in: "Z4–Z5a") == 5 && HRZoneChip.highestZone(in: "Z1–Z2") == 2)
        #expect(HRZoneChip.highestZone(in: "127–156 bpm") == nil)
    }

    @Test func derivedFromBpmWithLthrBounds() {
        // LTHR-based easy ceiling 152 = 89 % of ~171: 124 bpm is Z1, 152 bpm tops Z2.
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: nil, low: 127, high: 156, basis: "lthr")) == "Z1–Z2")
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: nil, low: 150, high: 160, basis: nil), lthr: 171) == "Z2–Z3")
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: nil, low: 140, high: 145, basis: nil), lthr: 171) == "Z1")
        #expect(HRZoneChip.zone(ofBpm: 190, lthr: 171) == 5)
    }

    @Test func fallsBackToTheBpmRange() {
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: nil, low: 127, high: 156, basis: nil)) == "127–156 bpm")
        #expect(HRZoneChip.label(for: HRRangeDTO(zone: nil, low: nil, high: nil, basis: nil)) == nil)
        #expect(HRZoneChip.label(for: nil) == nil)
    }
}

// MARK: - Lifting dashboard

struct LiftingDashboardTests {
    let now = DayKey.date("2026-10-25")!   // Sunday of the week starting 2026-10-19

    private func week(_ start: String, sessions: Int, planned: Int?, prs: Int? = nil) -> WeekVolumeDTO {
        WeekVolumeDTO(weekStart: start, liftingSessions: sessions, prs: prs, plannedLiftingSessions: planned)
    }

    @Test func rangesCoverTheRightWeeksAndFillGaps() {
        let weeks = [week("2026-10-19", sessions: 2, planned: 3, prs: 1), week("2026-10-05", sessions: 3, planned: 3)]
        let eight = LiftingDashboard.series(weeks, range: .eightWeeks, now: now)
        #expect(eight.count == 8 && eight.first?.weekStart == "2026-08-31" && eight.last?.weekStart == "2026-10-19")
        #expect(eight.first(where: { $0.weekStart == "2026-10-12" })?.done == 0)       // gap week becomes a zero week
        #expect(eight.last?.done == 2 && eight.last?.planned == 3 && eight.last?.prs == 1)
        let six = LiftingDashboard.series(weeks, range: .sixMonths, now: now)
        #expect(six.first?.weekStart == "2026-04-20" && six.count == 27)
        let year = LiftingDashboard.series(weeks, range: .year, now: now)
        #expect(year.first?.weekStart == "2025-10-20" && year.count == 53)
        #expect(zip(year, year.dropFirst()).allSatisfy { $0.weekStart < $1.weekStart })
    }

    @Test func prSeriesFallsBackToLatestPRDatesWhenTheBackendHasNone() {
        let lifts = [LiftSummaryDTO(variantId: 1, name: "Bench", lastPrDate: "2026-10-21"), LiftSummaryDTO(variantId: 2, name: "Squat", lastPrDate: "2026-10-22"),
                     LiftSummaryDTO(variantId: 3, name: "Row", lastPrDate: "2026-10-08")]
        let s = LiftingDashboard.series([week("2026-10-19", sessions: 2, planned: nil)], lifts: lifts, range: .eightWeeks, now: now)
        #expect(s.first(where: { $0.weekStart == "2026-10-19" })?.prs == 2)
        #expect(s.first(where: { $0.weekStart == "2026-10-05" })?.prs == 1)
        // Backend numbers win as soon as any week carries them.
        let backend = LiftingDashboard.series([week("2026-10-19", sessions: 2, planned: 3, prs: 0)], lifts: lifts, range: .eightWeeks, now: now)
        #expect(backend.allSatisfy { $0.prs == 0 })
    }

    @Test func kpisCombinePlanPRsProgressAndConsistency() {
        var weeks: [WeekVolumeDTO] = []
        // 8 completed weeks: 6 planned/done 3, then 2 weeks done 1 of 3 -> (6*3 + 2*1) / (8*3) = 20/24
        for (i, start) in ["2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"].enumerated() {
            weeks.append(week(start, sessions: i < 6 ? 3 : 1, planned: 3))
        }
        weeks.append(week("2026-10-19", sessions: 2, planned: 3))
        let lifts = [LiftSummaryDTO(variantId: 1, name: "A", trend4wPct: 3, lastPrDate: "2026-10-10", stalled: false, inProgram: true),
                     LiftSummaryDTO(variantId: 2, name: "B", trend4wPct: 0, stalled: true, inProgram: true),
                     LiftSummaryDTO(variantId: 3, name: "C", trend4wPct: -2, stalled: false, inProgram: true),
                     LiftSummaryDTO(variantId: 4, name: "D", trend4wPct: 9, stalled: false, inProgram: false)]
        let k = LiftingDashboard.kpis(weeks: weeks, lifts: lifts, now: now)
        #expect(k.sessionsDone == 2 && k.sessionsPlanned == 3)
        #expect(k.prsLast30Days == 1)
        #expect(k.progressing == 1 && k.stalled == 1 && k.tracked == 3)   // D is not in the program
        #expect(abs((k.consistencyPct ?? 0) - 20.0 / 24.0 * 100) < 0.001)
        #expect(LiftingDashboard.kpis(weeks: [], lifts: [], plannedThisWeekFallback: 3, now: now).sessionsPlanned == 3)
        #expect(LiftingDashboard.kpis(weeks: [week("2026-10-12", sessions: 2, planned: nil)], lifts: [], now: now).consistencyPct == nil)
    }

    @Test func programListAndRegionGroupsDoNotOverlap() {
        let lifts = [LiftSummaryDTO(variantId: 1, name: "Face Pull", muscleGroup: "rear_delts"),
                     LiftSummaryDTO(variantId: 2, name: "Lateral Raise", muscleGroup: "shoulders"),
                     LiftSummaryDTO(variantId: 3, name: "Lat Pulldown", muscleGroup: "lats"),
                     LiftSummaryDTO(variantId: 4, name: "Curl", muscleGroup: "biceps"),
                     LiftSummaryDTO(variantId: 5, name: "Squat", muscleGroup: "quads", bodyRegion: "Legs"),
                     LiftSummaryDTO(variantId: 6, name: "Crunch", muscleGroup: "abs"),
                     LiftSummaryDTO(variantId: 7, name: "Mystery", muscleGroup: nil)]
        let groups = LiftProgram.grouped(lifts)
        #expect(groups.map(\.region) == [.back, .shoulders, .arms, .legs, .core, .other])
        #expect(groups.first(where: { $0.region == .shoulders })?.lifts.map(\.name) == ["Face Pull", "Lateral Raise"])   // rear delts are Shoulders
        #expect(groups.flatMap(\.lifts).count == lifts.count)
        #expect(BodyRegion(muscleGroup: "triceps") == .arms && BodyRegion(raw: "chest") == .chest && BodyRegion(raw: "??") == .other)
    }

    @Test func inProgramUsesTheFlagOrFallsBackToRecentActiveLifts() {
        let flagged = [LiftSummaryDTO(variantId: 1, name: "A", inProgram: true), LiftSummaryDTO(variantId: 2, name: "B", inProgram: false)]
        #expect(LiftProgram.inProgram(flagged, now: now).map(\.variantId) == [1])
        let old = [LiftSummaryDTO(variantId: 1, name: "A", lastDate: "2026-10-20"), LiftSummaryDTO(variantId: 2, name: "B", lastDate: "2025-01-01"),
                   LiftSummaryDTO(variantId: 3, name: "C", active: false, lastDate: "2026-10-20")]
        #expect(LiftProgram.inProgram(old, now: now).map(\.variantId) == [1])
    }
}

// MARK: - Running dashboard

struct RunningDashboardTests {
    let now = DayKey.date("2026-10-25")!

    @Test func stackedSegmentsAreChronologicalAndCumulative() {
        let week = RunWeekDTO(weekStart: "2026-10-19", km: 25, timeS: 9000, runs: 3, runsDetail: [
            RunDetailDTO(runId: "c", date: "2026-10-24", type: .long, km: 12),
            RunDetailDTO(runId: "a", date: "2026-10-20", type: .easy, km: 6),
            RunDetailDTO(runId: "b", date: "2026-10-22", type: .intervals, km: 7)])
        let stack = RunStackTransforms.stacks([week])[0]
        #expect(stack.segments.map(\.runId) == ["a", "b", "c"])
        #expect(stack.segments.map(\.type) == [.easy, .intervals, .long])
        #expect(stack.segments.map(\.kmStart) == [0, 6, 13] && stack.segments.map(\.kmEnd) == [6, 13, 25])
        #expect(stack.totalKm == 25 && stack.runCount == 3)
        #expect(ChartPalette.runType(.easy) != ChartPalette.runType(.long))
    }

    @Test func weeksWithoutDetailGetOneGraySegmentAndRunCountComesFromTheWeek() {
        let weeks = [RunWeekDTO(weekStart: "2026-10-05", km: 0, timeS: 0, runs: 0),
                     RunWeekDTO(weekStart: "2026-10-12", km: 14.5, timeS: 4000, runs: 2),
                     RunWeekDTO(weekStart: "2026-10-19", km: 8, timeS: 2600, runs: 1,
                                runsDetail: [RunDetailDTO(runId: "x", date: "2026-10-22", type: nil, km: 8)])]
        let stacks = RunStackTransforms.stacks(weeks)
        #expect(stacks.map(\.weekStart) == ["2026-10-12", "2026-10-19"])   // leading empty week trimmed
        #expect(stacks[0].segments.count == 1 && stacks[0].segments[0].type == .other && stacks[0].segments[0].isRun == false)
        #expect(stacks[0].runCount == 2 && stacks[0].totalKm == 14.5)
        #expect(stacks[1].segments[0].type == .other && stacks[1].segments[0].isRun)
        #expect(RunStackTransforms.stacks(weeks, last: 1).map(\.weekStart) == ["2026-10-19"])
    }

    @Test func kpisPreferTheBackendAndKeepNullsWhenTheBlockIsPresent() {
        let weekly = [RunWeekDTO(weekStart: "2026-10-19", km: 8.13, timeS: 2600, runs: 1)]
        let withKpis = RunningStatsDTO(weekly: weekly, vdot: [VdotPointDTO(date: "2026-10-01", value: 41.0)],
                                       kpis: RunKpisDTO(vdotCurrent: nil, runsThisWeek: 1, runsPlannedThisWeek: 3, kmThisWeek: 8.13))
        let k = RunStackTransforms.kpis(withKpis, now: now)
        #expect(k.vdotCurrent == nil && k.vdotChange == nil)          // "needs a hard effort", not the seed value from the series
        #expect(k.runsThisWeek == 1 && k.runsPlanned == 3 && k.kmThisWeek == 8.13)
    }

    @Test func kpisFallBackToTheSeriesWithoutTheBlock() {
        let stats = RunningStatsDTO(
            weekly: [RunWeekDTO(weekStart: "2026-10-19", km: 18.4, timeS: 6000, runs: 2)],
            efficiency: [EfficiencyPointDTO(date: "2026-09-20", runId: "a", efficiency: 1.20, decouplingPct: 2),
                         EfficiencyPointDTO(date: "2026-10-10", runId: "b", efficiency: 1.30, decouplingPct: 7),
                         EfficiencyPointDTO(date: "2026-10-20", runId: "c", efficiency: 1.32, decouplingPct: 9)],
            vdot: [VdotPointDTO(date: "2026-08-30", value: 49.0), VdotPointDTO(date: "2026-10-12", value: 50.2)])
        let k = RunStackTransforms.kpis(stats, plannedRunsThisWeek: 3, now: now)
        #expect(k.vdotCurrent == 50.2 && k.vdotPrevious == 49.0 && abs((k.vdotChange ?? 0) - 1.2) < 1e-9)
        #expect(k.efficiencyCurrent == 1.32 && abs((k.efficiencyChangePct ?? 0) - 10) < 1e-9)
        #expect(k.driftFlags == 2 && k.runsThisWeek == 2 && k.runsPlanned == 3 && k.kmThisWeek == 18.4)
        #expect(RunStackTransforms.driftRuns(stats.efficiency, now: now).map(\.runId) == ["c", "b"])
    }
}

// MARK: - Health steps

struct StepsTests {
    let now = DayKey.date("2026-10-25")!

    private func days(_ steps: [Double?], avg: Double? = nil) -> [HealthDayDTO] {
        steps.enumerated().map { i, s in
            HealthDayDTO(date: DayKey.string(DayKey.daysAgo(steps.count - 1 - i, from: now)), steps: s, steps7dAvg: avg)
        }
    }

    @Test func averageIsComputedWhenTheFieldIsMissing() {
        let series = HealthTransforms.stepSeries(days([1000, 2000, 3000, nil, 5000, 6000, 7000, 8000]))
        #expect(series.count == 8)
        #expect(series[0].avg7 == 1000)                       // only itself
        #expect(series[2].avg7 == 2000)                       // (1000+2000+3000)/3
        #expect(series[3].avg7 == 2000 && series[3].steps == nil)   // missing day is skipped, not counted as 0
        #expect(series[7].avg7 == (2000 + 3000 + 5000 + 6000 + 7000 + 8000) / 6.0)   // window = last 7 calendar days
    }

    @Test func theBackendAverageWinsWhenPresent() {
        let series = HealthTransforms.stepSeries(days([1000, 2000], avg: 9999))
        #expect(series.map(\.avg7) == [9999, 9999])
    }

    @Test func daysAtGoalFromSummaryOrComputed() {
        let d = days([12_000, 9_000, 10_000, 4_000, 15_000, 11_000, 3_000, 10_500])   // last 7: 9k,10k,4k,15k,11k,3k,10.5k
        #expect(HealthTransforms.daysAtGoal(d, summary: nil, now: now) == 4)
        #expect(HealthTransforms.daysAtGoal(d, summary: HealthSummaryDTO(days10kLast7: 6), now: now) == 6)
        let series = HealthTransforms.stepSeries(d)
        #expect(HealthTransforms.latestStepsAvg(series, summary: HealthSummaryDTO(steps7dAvg: 8123)) == 8123)
        #expect(HealthTransforms.latestStepsAvg(series, summary: nil) == series.last?.avg7)
    }
}

// MARK: - Insights

struct InsightsGroupingTests {
    @Test func groupsByCategoryDropsReadinessAndCollapsesThinItems() throws {
        let r = try Fixture.decode(CorrelationsDTO.self, "stats_correlations")
        let groups = InsightTransforms.grouped(r.items)
        #expect(groups.map(\.category) == [.recovery, .hybrid])
        #expect(groups[0].items.count == 4 && groups[1].items.map(\.id) == ["run_load_vs_lift_performance"])
        let items = [CorrelationDTO(id: "sleep_score_vs_readiness", title: "x"),
                     CorrelationDTO(id: "thin", title: "Thin", points: [CorrelationPointDTO(date: "d", x: 1, y: 1)], r: nil, n: 1),
                     CorrelationDTO(id: "full", title: "Full", points: (0..<5).map { CorrelationPointDTO(date: "d\($0)", x: Double($0), y: Double($0)) }, r: 0.9, n: 5),
                     CorrelationDTO(id: "run_vs_lift_x", title: "Hybrid by id")]
        let g = InsightTransforms.grouped(items)
        #expect(g.map(\.category) == [.recovery, .hybrid])
        #expect(g[0].items.map(\.id) == ["full", "thin"])      // enough data first, collapsed ones last, dropped id gone
    }
}

// MARK: - Workout chooser

@MainActor
struct WorkoutChooserTests {
    let locale = Locale(identifier: "en_US")
    let today = "2026-10-25"   // Sunday: the long-run day

    private func planned(_ id: String, _ date: String, _ kind: String, done: Bool = false) -> PlannedLiftRef {
        PlannedLiftRef(id: id, date: date, kind: kind, title: kind.capitalized, done: done, exerciseCount: 5)
    }

    @Test func upcomingSessionsComeFirstThenMissedAndEmptyIsLast() {
        let list = [planned("tue", "2026-10-27", "lower"), planned("mon", "2026-10-26", "upper"),
                    planned("thu", "2026-10-22", "upper"), planned("wed", "2026-10-21", "lower", done: true)]
        let c = WorkoutChooserLogic.choices(planned: list, finished: [], today: today, locale: locale)
        #expect(c.map(\.source) == [.planned("mon"), .planned("tue"), .planned("thu"), .empty])
        #expect(c.first?.label == "Upper (planned Mon)")        // preselected: first element
        #expect(c[1].label == "Lower (planned Tue)")
        #expect(c.last?.title == "Empty workout")
    }

    @Test func todayCountsAsUpcomingAndNonLiftingRowsAreIgnored() {
        let list = [planned("run", "2026-10-25", "long_run"), planned("up", "2026-10-25", "upper"), planned("rest", "2026-10-26", "rest")]
        let c = WorkoutChooserLogic.choices(planned: list, finished: [], today: today, locale: locale)
        #expect(c.map(\.source) == [.planned("up"), .empty])
        #expect(c[0].detail == "planned today")
    }

    @Test func fallsBackToTheLastFinishedSessionOfEachKindWhenNothingIsPlanned() {
        let u1 = UUID(), u2 = UUID(), l1 = UUID(), other = UUID()
        let finished = [FinishedLiftRef(id: u1, kind: "upper", name: "Upper", startedAt: date("2026-10-15T16:00:00Z")),
                        FinishedLiftRef(id: u2, kind: "upper", name: "Upper", startedAt: date("2026-10-22T16:00:00Z")),
                        FinishedLiftRef(id: l1, kind: "lower", name: "Lower", startedAt: date("2026-10-20T16:00:00Z")),
                        FinishedLiftRef(id: other, kind: "other", name: "Workout", startedAt: date("2026-10-24T16:00:00Z"))]
        let c = WorkoutChooserLogic.choices(planned: [planned("done", "2026-10-26", "upper", done: true)], finished: finished, today: today, locale: locale)
        // most recent per kind; the one done longest ago (Lower) first so the rotation continues; "other" is not a template
        #expect(c.map(\.source) == [.template(l1), .template(u2), .empty])
        // With no upper/lower/full history at all, the latest session of any kind is offered.
        let any = WorkoutChooserLogic.choices(planned: [], finished: [finished[3]], today: today, locale: locale)
        #expect(any.map(\.source) == [.template(other), .empty])
        // Nothing at all: only the empty workout (callers then start it directly).
        #expect(WorkoutChooserLogic.choices(planned: [], finished: [], today: today, locale: locale).map(\.source) == [.empty])
    }

    @Test func choicesReadTheCachedWeekAndSkipSessionsDoneEarly() throws {
        let ctx = try makeContext()
        try SyncMerge.apply(try Fixture.decode(PullResponse.self, "sync_pull"), in: ctx)
        let rows = try ctx.fetch(FetchDescriptor<CachedPlannedSession>()).filter { WorkoutChooserLogic.liftingKinds.contains($0.kind) }
        let first = try #require(rows.min { $0.date < $1.date })
        let before = WorkoutChooserLogic.choices(in: ctx, today: "1999-01-01")
        #expect(before.count == rows.count + 1 || before.count == WorkoutChooserLogic.maxPlanned + 1)
        #expect(before.first?.source == .planned(first.id))
        // Finishing a workout linked to that planned session removes it from the chooser.
        let s = LocalSession(plannedSessionId: first.id, kind: first.kind, name: first.title, endedAt: .now)
        ctx.insert(s)
        let after = WorkoutChooserLogic.choices(in: ctx, today: "1999-01-01")
        #expect(!after.contains { $0.source == .planned(first.id) })
    }

    @Test func templateStartCopiesExercisesLastWeightsAndRepsUncompleted() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let old = LocalSession(kind: "upper", name: "Upper", startedAt: date("2026-10-22T16:00:00Z"), endedAt: date("2026-10-22T17:00:00Z"))
        ctx.insert(old)
        for (i, w) in [60.0, 62.5, 62.5].enumerated() {
            let s = LocalSet(sessionId: old.id, variantId: 7, variantName: "Bench", exerciseOrder: 1, setOrder: i + 1, weightKg: w, reps: 8 - i,
                             completedAt: date("2026-10-22T16:10:00Z"))
            ctx.insert(s)
        }
        ctx.insert(LocalSet(sessionId: old.id, variantId: 9, variantName: "Row", exerciseOrder: 2, setOrder: 1, weightKg: 50, reps: 10,
                            completedAt: date("2026-10-22T16:30:00Z")))
        let started = try #require(svc.start(fromTemplate: old, gymId: 4))
        let sets = svc.sets(of: started)
        #expect(started.id != old.id && started.kind == "upper" && started.plannedSessionId == nil && started.gymId == 4)
        #expect(sets.map(\.variantId) == [7, 7, 7, 9])
        #expect(sets.map { $0.weightKg ?? 0 } == [60, 62.5, 62.5, 50] && sets.map { $0.reps ?? 0 } == [8, 7, 6, 10])
        #expect(sets.allSatisfy { !$0.isCompleted && $0.prevWeightKg == $0.weightKg })
        #expect(svc.start(fromTemplate: old, gymId: 4)?.id == started.id)   // one active workout at a time
    }
}

// MARK: - Week strip

struct WeekStripModelTests {
    let days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]
    let today = "2026-10-25"

    @Test func mergesFinishedPlannedAndTemplateDays() {
        let planned = [PlannedDayRef(id: "sun", date: "2026-10-25", kind: "long_run", status: "proposed"),
                       PlannedDayRef(id: "thu", date: "2026-10-22", kind: "upper", status: "proposed")]
        let finished = [FinishedDayRef(plannedId: nil, day: "2026-10-19", kind: "upper"),
                        FinishedDayRef(plannedId: nil, day: "2026-10-20", kind: "lower")]
        let cells = WeekStripModel.cells(days: days, planned: planned, finished: finished, runDays: ["2026-10-21"], today: today)
        #expect(cells.map(\.state) == [.done, .done, .done, .missed, .expected, .rest, .planned])
        #expect(cells.map(\.kind) == ["upper", "lower", "easy_run", "upper", "intervals", "rest", "long_run"])
        #expect(WeekStripModel.cells(days: days, planned: [], finished: [], runDays: [], today: today).map(\.state)
                == [.expected, .expected, .expected, .expected, .expected, .rest, .expected])
    }

    @Test func futureDaysUsePlanOrTemplateAndRestOnlyWhenSaid() {
        let planned = [PlannedDayRef(id: "w", date: "2026-10-28", kind: "rest", status: "proposed"),
                       PlannedDayRef(id: "t", date: "2026-10-27", kind: "lower", status: "proposed")]
        let week = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
        let cells = WeekStripModel.cells(days: week, planned: planned, finished: [], runDays: [], today: today)
        #expect(cells.map(\.state) == [.expected, .planned, .rest, .expected, .expected, .rest, .expected])
        #expect(cells[0].kind == "upper" && cells[1].kind == "lower" && cells[3].kind == "upper")
    }

    @Test func doneRowsAndEarlyFinishesCountForTheirPlannedDay() {
        let planned = [PlannedDayRef(id: "a", date: "2026-10-22", kind: "upper", status: "done"),
                       PlannedDayRef(id: "b", date: "2026-10-23", kind: "lower", status: "proposed")]
        // "b" was done early, on Wednesday; "other" kind sessions fall back to the row / a plain lift.
        let finished = [FinishedDayRef(plannedId: "b", day: "2026-10-21", kind: "other")]
        let cells = WeekStripModel.cells(days: days, planned: planned, finished: finished, runDays: [], today: today)
        #expect(cells[3].state == .done && cells[4].state == .done && cells[4].kind == "lower")
        #expect(cells[2].state == .done && cells[2].kind == "full")
    }
}

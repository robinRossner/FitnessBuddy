import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

private func pt(_ date: String, _ e1rm: Double? = 100) -> LiftPointDTO {
    LiftPointDTO(date: date, sessionId: "s-\(date)", bestE1rm: e1rm, topSet: nil, volumeKg: 1000, sets: 3)
}

struct ChartTransformTests {
    let now = DayKey.date("2026-10-25")!

    @Test func dayKeyRoundTrip() {
        #expect(DayKey.string(DayKey.date("2026-03-08")!) == "2026-03-08")
        #expect(DayKey.date("garbage") == nil)
        #expect(DayKey.weekStart(DayKey.date("2026-10-25")!) == "2026-10-19")   // Sunday -> its Monday
        #expect(DayKey.weekStart(DayKey.date("2026-10-19")!) == "2026-10-19")
        #expect(DayKey.string(DayKey.daysAgo(30, from: now)) == "2026-09-25")
    }

    @Test func rangeFilteringKeepsOnlyRecentPointsAndAllKeepsEverything() {
        let pts = [pt("2023-02-01"), pt("2025-09-01"), pt("2025-11-01"), pt("2026-05-01"), pt("2026-08-01"), pt("2026-10-20")]
        #expect(LiftRange.threeMonths.filter(pts, now: now).map(\.date) == ["2026-08-01", "2026-10-20"])
        #expect(LiftRange.sixMonths.filter(pts, now: now).map(\.date) == ["2026-05-01", "2026-08-01", "2026-10-20"])
        #expect(LiftRange.year.filter(pts, now: now).map(\.date) == ["2025-11-01", "2026-05-01", "2026-08-01", "2026-10-20"])
        #expect(LiftRange.all.filter(pts, now: now).count == 6)
        #expect(LiftRange.all.filter(pts.reversed(), now: now).first?.date == "2023-02-01")  // sorted ascending
        #expect(LiftRange.threeMonths.filter([], now: now).isEmpty)
    }

    @Test func cutoffDateIsInclusive() {
        let pts = [pt("2026-07-25"), pt("2026-07-24")]
        #expect(LiftRange.threeMonths.filter(pts, now: now).map(\.date) == ["2026-07-25"])
    }

    @Test func zonesStackFromZ1ToZ5AndMissingZonesAreZero() {
        let weeks = [
            RunWeekDTO(weekStart: "2026-10-12", km: 10, timeS: 3000, runs: 2, timeInZonesS: ["z5": 60, "z1": 120, "z3": 600]),
            RunWeekDTO(weekStart: "2026-10-05", km: 5, timeS: 1500, runs: 1, timeInZonesS: ["z2": 300]),
            RunWeekDTO(weekStart: "2026-09-28", km: 0, timeS: 0, runs: 0, timeInZonesS: ["z1": 0, "z2": 0]),
        ]
        let segs = RunTransforms.zoneSegments(weeks)
        #expect(segs.count == 10)                                   // the empty week is skipped
        #expect(segs.map(\.weekStart).prefix(5).allSatisfy { $0 == "2026-10-05" })   // sorted by week
        let w2 = segs.filter { $0.weekStart == "2026-10-12" }
        #expect(w2.map(\.zone) == [1, 2, 3, 4, 5])
        #expect(w2.map(\.minutes) == [2, 0, 10, 0, 1])
        #expect(w2.map(\.name) == RunTransforms.zoneNames)
        #expect(ChartPalette.zones.count == 5)
    }

    @Test func leadingEmptyWeeksAreTrimmed() {
        let weeks = [RunWeekDTO(weekStart: "2026-08-17", km: 0, timeS: 0, runs: 0), RunWeekDTO(weekStart: "2026-08-24", km: 12, timeS: 4000, runs: 2),
                     RunWeekDTO(weekStart: "2026-08-31", km: 0, timeS: 0, runs: 0)]
        #expect(RunTransforms.weeksWithRuns(weeks).map(\.weekStart) == ["2026-08-24", "2026-08-31"])
        #expect(RunTransforms.weeksWithRuns([RunWeekDTO(weekStart: "2026-08-17", km: 0, timeS: 0, runs: 0)]).isEmpty)
    }

    @Test func sleepSecondsBecomeHours() {
        #expect(HealthTransforms.sleepHours(27_000) == 7.5)
        #expect(HealthTransforms.sleepHours(nil) == nil)
        #expect(HealthTransforms.sleepHours(0) == 0)
        #expect(Fmt.hours(26_640) == Fmt.number(7.4, maxFraction: 1))
        #expect(Fmt.sleep(25_500) == "7 h 05 min")
    }

    @Test func healthWindowAndReadinessStrip() {
        let days = (0..<100).map { off -> HealthDayDTO in
            HealthDayDTO(date: DayKey.string(DayKey.daysAgo(off, from: now)),
                         readiness: DayReadinessDTO(level: off == 0 ? "green" : (off == 2 ? "red" : "yellow"), reason: nil))
        }
        #expect(HealthTransforms.window(days, range: .thirty, now: now).count == 30)
        #expect(HealthTransforms.window(days, range: .ninety, now: now).count == 90)
        #expect(HealthTransforms.window(days, range: .thirty, now: now).first?.date == "2026-09-26")
        let strip = HealthTransforms.readinessStrip(Array(days.prefix(3)), count: 5, now: now)
        #expect(strip.count == 5)
        #expect(strip.map(\.level) == [.unknown, .unknown, .red, .yellow, .green])   // oldest first, gaps unknown
        #expect(strip.last?.date == "2026-10-25")
        #expect(HealthTransforms.deltaPct(short: 57, long: 60) == -5)
        #expect(HealthTransforms.deltaPct(short: nil, long: 60) == nil)
        #expect(HealthTransforms.deltaPct(short: 5, long: 0) == nil)
    }

    @Test func weekSummaryAndPRCount() {
        let weeks = [WeekVolumeDTO(weekStart: "2026-10-12", liftingVolumeKg: 1, liftingSets: 1),
                     WeekVolumeDTO(weekStart: "2026-10-19", liftingVolumeKg: 19_893, liftingSets: 39)]
        #expect(VolumeTransforms.thisWeek(weeks, now: now) == WeekSummary(sets: 39, volumeKg: 19_893))
        #expect(VolumeTransforms.thisWeek(Array(weeks.prefix(1)), now: now) == WeekSummary(sets: 0, volumeKg: 0))
        let lifts = [LiftSummaryDTO(variantId: 1, name: "A", lastPrDate: "2026-10-01"), LiftSummaryDTO(variantId: 2, name: "B", lastPrDate: "2026-09-01"),
                     LiftSummaryDTO(variantId: 3, name: "C", lastPrDate: nil)]
        #expect(LiftListing.recentPRCount(lifts, now: now) == 1)
        let by = VolumeTransforms.setsByMuscle([WeekVolumeDTO(weekStart: "2026-10-19", setsByMuscleGroup: ["chest": 6, "back": 9])])
        #expect(by.map(\.group) == ["back", "chest"])
    }

    @Test func liftListSortsActiveFirstSearchesAndFilters() {
        let lifts = [LiftSummaryDTO(variantId: 1, name: "Zercher Squat", muscleGroup: "quads", gymName: "Gym 3", active: false),
                     LiftSummaryDTO(variantId: 2, name: "Bench Press", muscleGroup: "chest", gymName: "Gym 3", active: true),
                     LiftSummaryDTO(variantId: 3, name: "Back Squat", muscleGroup: "quads", gymName: "Gym 2", active: true)]
        #expect(LiftListing.filtered(lifts, query: "", muscle: nil).map(\.variantId) == [3, 2, 1])
        #expect(LiftListing.filtered(lifts, query: "squat", muscle: nil).map(\.variantId) == [3, 1])
        #expect(LiftListing.filtered(lifts, query: "gym 2", muscle: nil).map(\.variantId) == [3])
        #expect(LiftListing.filtered(lifts, query: "", muscle: "chest").map(\.variantId) == [2])
        #expect(LiftListing.muscleGroups(lifts) == ["chest", "quads"])
    }

    @Test func trendDirection() {
        #expect(Trend(pct: 3.2) == .up && Trend(pct: -4) == .down && Trend(pct: 0.4) == .flat && Trend(pct: nil) == .unknown)
    }

    @Test func correlationHelpers() {
        #expect(CorrelationText.strength(0.1) == "No clear link")
        #expect(CorrelationText.strength(-0.3) == "Weak negative link")
        #expect(CorrelationText.strength(0.5) == "Moderate positive link")
        #expect(CorrelationText.strength(0.9) == "Strong positive link")
        let pts = (0..<5).map { CorrelationPointDTO(date: "2026-10-0\($0 + 1)", x: Double($0), y: 2 * Double($0) + 1) }
        let line = CorrelationText.trendLine(pts)
        #expect(line?.y0 == 1 && line?.y1 == 9)
        #expect(CorrelationText.trendLine(Array(pts.prefix(2))) == nil)
    }
}

struct FormattingTests {
    @Test func paceAndDurations() {
        #expect(Fmt.paceKm(250) == "4:10/km")
        #expect(Fmt.paceKm(304.6) == "5:05/km")
        #expect(Fmt.paceKm(nil) == "–" && Fmt.paceKm(0) == "–")
        #expect(Fmt.duration(2902.6) == "48:23")
        #expect(Fmt.duration(3725) == "1:02:05")
        #expect(Fmt.longDuration(3900) == "1 h 05 min")
        // Decimal separator follows the device locale, so build the expectation with the same formatter.
        let d = { (v: Double) in Fmt.number(v, maxFraction: 1) }
        #expect(Fmt.percent(2.34) == "+\(d(2.3)) %")
        #expect(Fmt.percent(-1.26) == "-\(d(1.3)) %")
        #expect(Fmt.percent(0) == "0 %" && Fmt.percent(nil) == "–")
        #expect(Fmt.meters(8130) == "\(d(8.1)) km" && Fmt.meters(400) == "400 m")
        #expect(Fmt.volume(19_893) == "\(d(19.9)) t" && Fmt.volume(850) == "850 kg")
        #expect(MuscleGroup.display("upper_back") == "Upper back" && MuscleGroup.display(nil) == "Other")
    }
}

// MARK: - Bodyweight

struct BodyweightMergeTests {
    private func local(_ day: String, _ kg: Double, hour: Int = 8, deleted: Bool = false, pending: Bool = true) -> LocalBodyweight {
        let d = Calendar.current.date(bySettingHour: hour, minute: 0, second: 0, of: DayKey.date(day)!)!
        return LocalBodyweight(id: UUID(), measuredAt: d, weightKg: kg, deleted: deleted, pending: pending)
    }
    private let server = [BodyweightPointDTO(date: "2026-10-01", weightKg: 82, source: "garmin"),
                          BodyweightPointDTO(date: "2026-10-24", weightKg: 82.2, source: "manual")]

    @Test func pendingLocalEntryAppearsImmediately() {
        let merged = BodyweightMerge.merge(server: server, local: [local("2026-10-25", 81.8)])
        #expect(merged.map(\.day) == ["2026-10-01", "2026-10-24", "2026-10-25"])
        #expect(merged.last?.weightKg == 81.8 && merged.last?.isPending == true)
        #expect(merged.first?.isPending == false)
    }

    @Test func localEntryWinsOverTheServerValueOfTheSameDay() {
        let merged = BodyweightMerge.merge(server: server, local: [local("2026-10-24", 83.0, pending: false)])
        #expect(merged.count == 2)
        #expect(merged.last?.weightKg == 83.0 && merged.last?.isPending == false)
    }

    @Test func syncedEntryIsNotDuplicatedOnceTheStatsCatchUp() {
        let l = local("2026-10-24", 82.2, pending: false)
        #expect(BodyweightMerge.merge(server: server, local: [l]).count == 2)
    }

    @Test func laterEntryOfTheSameDayWins() {
        let merged = BodyweightMerge.merge(server: [], local: [local("2026-10-25", 82, hour: 7), local("2026-10-25", 81.6, hour: 19)])
        #expect(merged.count == 1 && merged[0].weightKg == 81.6)
    }

    @Test func deletedEntryHidesTheManualServerValueButNotTheWatchScale() {
        let merged = BodyweightMerge.merge(server: server, local: [local("2026-10-24", 82.2, deleted: true), local("2026-10-01", 82, deleted: true)])
        #expect(merged.map(\.day) == ["2026-10-01"])           // manual hidden, garmin stays
        #expect(BodyweightMerge.merge(server: [], local: [local("2026-10-24", 75, deleted: true)]).isEmpty)
    }

    @Test func fourWeekChange() {
        let entries = BodyweightMerge.merge(server: [BodyweightPointDTO(date: "2026-09-27", weightKg: 80, source: nil),
                                                     BodyweightPointDTO(date: "2026-10-25", weightKg: 78.4, source: nil)], local: [])
        #expect(abs((BodyweightMerge.change4wPct(entries) ?? 0) + 2.0) < 1e-9)
        #expect(BodyweightMerge.change4wPct(Array(entries.suffix(1))) == nil)
        let far = BodyweightMerge.merge(server: [BodyweightPointDTO(date: "2026-08-01", weightKg: 80, source: nil),
                                                 BodyweightPointDTO(date: "2026-10-25", weightKg: 78.4, source: nil)], local: [])
        #expect(BodyweightMerge.change4wPct(far) == nil)        // reference too far away
    }

    @Test func windowing() {
        let entries = BodyweightMerge.merge(server: server, local: [])
        let now = DayKey.date("2026-10-25")!
        #expect(BodyweightMerge.within(entries, days: 20, now: now).map(\.day) == ["2026-10-24"])
        #expect(BodyweightMerge.within(entries, days: 90, now: now).count == 2)
    }
}

@MainActor
struct BodyweightLogTests {
    @Test func parsingAndValidation() {
        #expect(BodyweightLog.parse("78,4") == 78.4)
        #expect(BodyweightLog.parse(" 78.4 kg ") == 78.4)
        #expect(BodyweightLog.parse("abc") == nil)
        #expect(BodyweightLog.isValid(78) && !BodyweightLog.isValid(5) && !BodyweightLog.isValid(900))
    }

    @Test func addCreatesADirtyRecordTheOutboxPicksUp() throws {
        let ctx = try makeContext()
        let m = BodyweightLog.add(weightKg: 78.44, day: Date(), in: ctx)
        #expect(m.weightKg == 78.4 && m.dirty && !m.isDemo && m.deletedAt == nil)
        let batch = try Outbox.collect(in: ctx)
        #expect(batch.bodyMeasurements.count == 1 && batch.bodyMeasurements[0].id == m.id.uuidString.lowercased())
        #expect(batch.bodyMeasurements[0].deletedAt == nil)
    }

    @Test func pastDayIsLoggedAtNoonLocalTime() {
        let day = DayKey.date("2026-10-20")!
        let at = BodyweightLog.measuredAt(for: day, now: DayKey.date("2026-10-25")!)
        #expect(Calendar.current.component(.hour, from: at) == 12)
        #expect(DayKey.string(at) == "2026-10-20")
    }

    @Test func deleteIsATombstoneThatIsPushed() throws {
        let ctx = try makeContext()
        let m = BodyweightLog.add(weightKg: 80, day: Date(), in: ctx)
        _ = try Outbox.apply(results: [PushResultDTO(entity: "body_measurement", id: m.id.uuidString.lowercased(), status: "applied", reason: nil)],
                             batch: try Outbox.collect(in: ctx), in: ctx)
        #expect(!m.dirty && Outbox.pendingCount(in: ctx) == 0)
        BodyweightLog.delete(m, in: ctx)
        #expect(m.deletedAt != nil && m.dirty)
        let batch = try Outbox.collect(in: ctx)
        #expect(batch.bodyMeasurements.count == 1 && batch.bodyMeasurements[0].deletedAt != nil)
        let snap = BodyweightLog.snapshot(m)
        #expect(snap.deleted && snap.pending)
    }
}

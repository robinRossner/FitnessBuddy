@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

@MainActor
@Suite(.tags(.progress))
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

/// Chart transforms that use the app's chart colours.
@Suite(.tags(.stats, .fast))
struct ChartPaletteTransformTests {
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
}

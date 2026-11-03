@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

// MARK: - Workout chooser

@MainActor
@Suite(.tags(.workout))
struct WorkoutChooserTests {
    let locale = AppLocale.current
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

/// Dashboard pieces that need the app (demo data, chart colours).
@Suite(.tags(.stats, .fast))
struct DashboardAppTests {
    @Test func demoStatsCarryTheNewFields() throws {
        let running = try APICoding.makeDecoder().decode(RunningStatsDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/running")))
        #expect(running.kpis?.vdot4wAgo == 49.8 && running.weekly.dropLast().last?.runsDetail?.isEmpty == false)
        let health = try APICoding.makeDecoder().decode(HealthStatsDTO.self, from: try #require(DemoStats.json(for: "/api/v1/stats/health")))
        #expect(health.readinessToday?.headline == "Take it easier")
    }

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
}

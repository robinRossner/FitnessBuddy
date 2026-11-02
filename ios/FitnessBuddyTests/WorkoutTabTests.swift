import Foundation
import Testing
@testable import FitnessBuddy

@Suite(.tags(.workout, .fast))
struct WorkoutTabTests {
    private let days = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]

    private func plan(_ id: String, _ date: String, title: String = "Upper", done: Bool = false) -> PlannedLiftRef {
        PlannedLiftRef(id: id, date: date, kind: "upper", title: title, done: done, exerciseCount: 5)
    }

    @Test func weekMarksDoneMissedAndPlanned() {
        let linked = UUID()
        let rows = WorkoutHomeLogic.week(
            days: days,
            planned: [plan("a", "2026-10-26", done: true), plan("b", "2026-10-27"), plan("c", "2026-10-30", title: "Lower")],
            finished: [FinishedWeekRef(id: linked, plannedId: "a", day: "2026-10-26", name: "Upper")],
            today: "2026-10-28")
        #expect(rows.map(\.state) == [.done, .missed, .planned])
        #expect(rows[0].sessionId == linked)
        #expect(rows[2].plannedId == "c")
    }

    @Test func unlinkedSessionOnPlannedDayCountsForThatRow() {
        let id = UUID()
        let rows = WorkoutHomeLogic.week(
            days: days, planned: [plan("a", "2026-10-27")],
            finished: [FinishedWeekRef(id: id, plannedId: nil, day: "2026-10-27", name: "Workout")],
            today: "2026-10-28")
        #expect(rows.count == 1)
        #expect(rows[0].state == .done)
        #expect(rows[0].sessionId == id)
    }

    @Test func extraSessionsAndOtherWeeksAreHandled() {
        let extra = UUID()
        let rows = WorkoutHomeLogic.week(
            days: days, planned: [plan("old", "2026-10-19")],
            finished: [FinishedWeekRef(id: extra, plannedId: nil, day: "2026-10-29", name: ""),
                       FinishedWeekRef(id: UUID(), plannedId: nil, day: "2026-10-20", name: "Last week")],
            today: "2026-10-30")
        #expect(rows.count == 1)
        #expect(rows[0].title == "Workout")
        #expect(rows[0].state == .done)
        #expect(rows[0].plannedId == nil)
    }

    private func set(_ order: Int, _ n: Int, _ kg: Double?, _ reps: Int?, type: String = "working", variant: Int = 1) -> LoggedSet {
        LoggedSet(variantId: variant, variantName: "Bench Press @ Gym 3", exerciseOrder: order, setOrder: n, setType: type,
                  weightKg: kg, reps: reps, durationS: nil)
    }

    @Test func e1rmIsEpley() {
        #expect(WorkoutDetailLogic.e1rm(weightKg: 60, reps: 30) == 120)
        #expect(WorkoutDetailLogic.e1rm(weightKg: nil, reps: 8) == nil)
        #expect(WorkoutDetailLogic.e1rm(weightKg: 0, reps: 8) == nil)
    }

    @Test func personalRecordNeedsHistoryAndBeatsIt() {
        let sets = [set(1, 1, 40, 10, type: "warmup"), set(1, 2, 60, 8), set(1, 3, 62.5, 8), set(2, 1, 20, 12, variant: 2)]
        let withHistory = WorkoutDetailLogic.blocks(sets: sets, priorBest: [1: 70, 2: 100])
        #expect(withHistory.count == 2)
        #expect(withHistory[0].name == "Bench Press")
        #expect(withHistory[0].hasPR)
        #expect(withHistory[0].sets.map(\.isPR) == [false, false, true])
        #expect(withHistory[0].sets.map(\.label) == ["W", "1", "2"])
        #expect(!withHistory[1].hasPR)
        // no earlier history -> nothing is called a record
        #expect(!WorkoutDetailLogic.blocks(sets: sets, priorBest: [:]).contains { $0.hasPR })
        // equal or lower than before -> no record
        #expect(!WorkoutDetailLogic.blocks(sets: sets, priorBest: [1: 200]).contains { $0.hasPR })
    }

    @Test func volumeSkipsWarmups() {
        let sets = [set(1, 1, 40, 10, type: "warmup"), set(1, 2, 60, 8), set(1, 3, 50, 10)]
        #expect(WorkoutDetailLogic.volume(sets) == 980)
        #expect(WorkoutDetailLogic.blocks(sets: sets, priorBest: [:])[0].volumeKg == 980)
    }

    @Test func loggedRules() {
        let now = Date()
        #expect(WorkoutDetailLogic.isLogged(completedAt: now, deletedAt: nil, reps: 8, durationS: nil, startedHere: true))
        #expect(!WorkoutDetailLogic.isLogged(completedAt: nil, deletedAt: nil, reps: 8, durationS: nil, startedHere: true))
        // pulled history may lack completed_at
        #expect(WorkoutDetailLogic.isLogged(completedAt: nil, deletedAt: nil, reps: 8, durationS: nil, startedHere: false))
        #expect(!WorkoutDetailLogic.isLogged(completedAt: now, deletedAt: now, reps: 8, durationS: nil, startedHere: true))
        #expect(!WorkoutDetailLogic.isLogged(completedAt: now, deletedAt: nil, reps: nil, durationS: nil, startedHere: true))
    }
}

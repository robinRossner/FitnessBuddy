import Foundation
import Testing
@testable import FitnessCore

/// Pure helpers behind the Progress tab (ProgressModel.swift): next lift targets, running overview, this week's runs.
@Suite(.tags(.progress, .fast))
struct ProgressTabTests {
    /// Wednesday; its week starts Monday 2026-10-26.
    let now = DayKey.date("2026-10-28")!.addingTimeInterval(12 * 3600)

    private func lifting(_ id: String, _ date: String, kind: String = "upper", status: String? = "proposed",
                         _ exercises: [PlannedExerciseDTO]) -> PlanItemDTO {
        PlanItemDTO(id: id, date: date, kind: kind, status: status, title: kind.capitalized,
                    session: PrescriptionDTO(type: kind, exercises: exercises))
    }

    private func exercise(_ variantId: Int, kg: Double = 80, reps: Int = 8, note: String? = nil) -> PlannedExerciseDTO {
        PlannedExerciseDTO(order: 1, variantId: variantId, name: "Lift \(variantId)",
                           sets: [PlannedSetDTO(index: 0, setType: "warmup", targetWeight: 40, targetReps: 8)]
                               + (1...3).map { PlannedSetDTO(index: $0, targetWeight: kg, targetReps: reps) },
                           progressionNote: note)
    }

    private func runPlan(_ date: String, _ kind: String, km: Double?, status: String? = "proposed") -> PlanItemDTO {
        PlanItemDTO(id: "p-\(date)-\(kind)", date: date, kind: kind, status: status, title: PlanFormat.runTitle(kind),
                    session: PrescriptionDTO(type: kind, distanceKm: km))
    }

    @Test func nextTargetIsTheFirstOpenLiftingSessionWithTheLift() {
        let items = [
            lifting("a", "2026-10-27", [exercise(7)]),                          // in the past
            lifting("b", "2026-10-28", status: "done", [exercise(7)]),          // marked done
            lifting("c", "2026-10-29", [exercise(7)]),                          // finished on this device
            lifting("d", "2026-10-31", kind: "lower", [exercise(9)]),           // other lift
            runPlan("2026-10-30", "easy_run", km: 8),
            lifting("e", "2026-11-02", [exercise(7, kg: 82.5, reps: 6, note: "+2.5 kg after 3 × 8")]),
        ]
        let t = ProgressModel.nextTarget(variantId: 7, items: items, doneIds: ["c"], today: "2026-10-28")
        #expect(t == NextLiftTarget(date: "2026-11-02", text: "3 × 6 @ \(Fmt.weight(82.5)) kg", note: "+2.5 kg after 3 × 8"))
        #expect(ProgressModel.nextTarget(variantId: 9, items: items, today: "2026-10-28")?.date == "2026-10-31")
        #expect(ProgressModel.nextTarget(variantId: 42, items: items, today: "2026-10-28") == nil)
    }

    @Test func e1rmSparklineAndChangeUseSessionsInDateOrder() {
        let pts = [LiftPointDTO(date: "2026-10-20", bestE1rm: 110), LiftPointDTO(date: "2026-10-01", bestE1rm: 100),
                   LiftPointDTO(date: "2026-10-10", bestE1rm: nil), LiftPointDTO(date: "2026-10-27", bestE1rm: 105)]
        #expect(ProgressModel.e1rmSparkline(pts) == [100, 110, 105])
        #expect(ProgressModel.e1rmSparkline(pts, count: 2) == [110, 105])
        #expect(ProgressModel.e1rmChangePct(pts) == 5)
        #expect(ProgressModel.e1rmChangePct(Array(pts.prefix(1))) == nil)
    }

    @Test func runningOverviewZeroFillsWeeksAndAveragesTheFullOnes() {
        let stats = RunningStatsDTO(
            weekly: [RunWeekDTO(weekStart: "2026-10-19", km: 20), RunWeekDTO(weekStart: "2026-10-26", km: 5),
                     RunWeekDTO(weekStart: "2026-10-12", km: 15)],
            efficiency: [
                EfficiencyPointDTO(date: "2026-10-20", runId: "1", type: "easy", distanceM: 10_000, avgPaceSPerKm: 330),
                EfficiencyPointDTO(date: "2026-10-25", runId: "2", type: "easy", distanceM: 5_000, avgPaceSPerKm: 300),
                EfficiencyPointDTO(date: "2026-10-26", runId: "3", type: "long", distanceM: 16_000, avgPaceSPerKm: 340),
                EfficiencyPointDTO(date: "2026-09-01", runId: "4", type: "easy", distanceM: 8_000, avgPaceSPerKm: 400),
            ])
        let kpis = RunKpiValues(driftFlags: 0, runsThisWeek: 1, kmThisWeek: 5)
        let plan = [runPlan("2026-10-29", "easy_run", km: 8), runPlan("2026-11-01", "long_run", km: 12),
                    runPlan("2026-10-30", "intervals", km: 6, status: "skipped"), runPlan("2026-11-03", "easy_run", km: 9)]
        let o = ProgressModel.runningOverview(stats, kpis: kpis, plan: plan, now: now)
        #expect(o.weeks.count == 8)
        #expect(o.weeks.first?.key == "2026-09-07")
        #expect(o.weeks.last == ProgressWeekBar(weekStart: "2026-10-26", value: 5, isCurrent: true))
        #expect(o.weeks.filter(\.isCurrent).count == 1)
        #expect(o.weeks.map(\.value) == [0, 0, 0, 0, 0, 15, 20, 5])
        #expect(o.averageKm == 5)  // (15 + 20) / 7 full weeks
        #expect(o.kmLastWeek == 20)
        #expect(o.kmPlannedThisWeek == 20)  // skipped and next week's runs left out

        var withStrides = plan
        withStrides[0].session?.structure = IntervalStructureDTO(strides: StridesDTO(reps: 6, durationS: 20, recoveryS: 60))
        let s = ProgressModel.runningOverview(stats, kpis: kpis, plan: withStrides, now: now)
        #expect(s.kmPlannedThisWeek == 20)  // strides are part of the run's distance, not on top
        #expect(o.easyRuns == 2)
        #expect(o.easyPaceSPerKm == 320)  // distance-weighted
        #expect(o.kmThisWeek == 5)

        let empty = ProgressModel.runningOverview(RunningStatsDTO(), kpis: kpis, plan: [], now: now)
        #expect(empty.averageKm == nil && empty.kmPlannedThisWeek == nil && empty.easyPaceSPerKm == nil && empty.kmLastWeek == nil)
    }

    @Test func weekRunsMergeDoneRunsWithOpenPlannedOnes() {
        let days = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]
        let runs = [
            RunSummaryDTO(id: "r1", title: "Morning Run", startedAtLocal: "2026-10-28T07:00:00", distanceM: 8_400, avgPaceSPerKm: 330),
            // A long run on Monday: not taken as a make-up for Tuesday's easy run, so that one stays missed.
            RunSummaryDTO(id: "r2", title: "Long run", startedAtLocal: "2026-10-26T18:00:00", distanceM: 5_000),
            RunSummaryDTO(id: "old", title: "Last week", startedAtLocal: "2026-10-22T07:00:00", distanceM: 10_000),
        ]
        let plan = [runPlan("2026-10-27", "easy_run", km: 6), runPlan("2026-10-28", "easy_run", km: 8),
                    runPlan("2026-10-30", "intervals", km: nil), runPlan("2026-11-01", "long_run", km: 12)]
        let rows = WeekRunsModel.build(days: days, items: plan, runs: runs, finished: [], today: "2026-10-28").rows
        let list = ProgressModel.weekRuns(runs: runs, planned: rows, days: days)
        #expect(list.map(\.date) == ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-30", "2026-11-01"])
        #expect(list[0].target == .run(id: "r2") && list[0].title == "Long run" && list[0].detail == "5 km")
        #expect(list[1].isDone == false && list[1].badge == "Missed")
        #expect(list[2].target == .run(id: "r1") && list[2].detail == "\(Fmt.km(8.4)) km · \(Fmt.pace(330)) /km")
        #expect(list[3].badge == nil && !list[3].isDone)
        #expect(!list.contains { $0.title == "Last week" })
    }
}

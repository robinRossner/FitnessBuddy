import Foundation
import Testing
@testable import FitnessCore

@Suite(.tags(.stats, .decoding, .fast))
struct StatsDecodingTests {
    // MARK: Real backend examples (docs/api/examples/stats_*.json, read from the test bundle)

    @Test func liftsFixture() throws {
        let r = try Fixture.decode(LiftsStatsDTO.self, "stats_lifts")
        #expect(r.generatedAt != nil)
        #expect(r.lifts.count == 21)
        let first = try #require(r.lifts.first)
        #expect(first.variantId == 16 && first.muscleGroup == "triceps" && first.gymName == "Gym 2")
        #expect(first.trend4wPct == 2.45)
        #expect(first.bestE1rm == 38.5 && first.latestE1rm == 34.83)
        #expect(first.isStalled)
        #expect(r.lifts.contains { $0.gymName == nil })           // generic variants
        #expect(r.lifts.contains { $0.lastPrDate != nil })
    }

    @Test func liftDetailFixture() throws {
        let r = try Fixture.decode(LiftDetailDTO.self, "stats_lift_detail")
        #expect(r.lift.variantId == 3 && r.lift.sessions == 48)
        #expect(r.points.count == 48)
        #expect(r.points[0].date == "2026-04-27")
        #expect(r.points[0].topSet == TopSetDTO(weightKg: 18, reps: 8) && r.points[0].bestE1rm == 22.8)
        #expect(r.lift.bestE1rm == 31.53 && r.lift.trend4wPct == -2.32 && r.lift.lastPrDate == "2026-09-24")
        #expect(r.prs.count == 14)
        #expect(Set(r.prs.map(\.type)) == [.e1rm, .weight, .reps])
    }

    @Test func weeklyVolumeFixture() throws {
        let r = try Fixture.decode(WeeklyVolumeDTO.self, "stats_volume_weekly")
        #expect(r.weeks.count == 26)
        let last = try #require(r.weeks.last)
        #expect(last.weekStart == "2026-10-19" && last.liftingSets == 56)
        #expect(last.setsByMuscleGroup?["chest"] == 6)
        #expect(last.runningKm == 16.4)
    }

    @Test func runningFixture() throws {
        let r = try Fixture.decode(RunningStatsDTO.self, "stats_running")
        #expect(r.weekly.count == 26 && r.efficiency.count == 46 && r.vdot.count == 6 && r.vdotFit.count == 70 && r.intervals.count == 24)
        let step = try #require(r.vdot.last)
        #expect(step.value == 44.25 && step.source == "interval_reps" && step.delta == 0.29 && step.reason?.contains("measured from interval reps") == true)
        #expect(r.vdot.first?.source == "initial" && r.vdotFit[0].source == nil && r.vdotFit[0].value == 41.0)
        #expect(r.weekly.last?.timeInZonesS?["z1"] == 2763)
        #expect(r.efficiency[0].type == "long" && r.efficiency[0].efficiency == 1.1528)
        #expect(r.intervals[0].passed == true && r.intervals[0].hrInRange == true && r.intervals[0].targetHrLow == 166)
    }

    @Test func runAnalysisFixtures() throws {
        let iv = try Fixture.decode(RunAnalysisDTO.self, "stats_run_analysis_intervals")
        #expect(iv.laps.count == 14)
        #expect(iv.laps.filter { $0.kind == .work }.count == 6)
        #expect(iv.laps[1].paceOk != nil && iv.laps[1].hrOk == true && iv.laps[1].targetHrLow == 179 && iv.laps[1].targetPaceSPerKm != nil)
        #expect(iv.verdict?.passed == true && (iv.verdict?.reasons.count ?? 0) >= 1)
        #expect(iv.planned == nil)
        let easy = try Fixture.decode(RunAnalysisDTO.self, "stats_run_analysis_easy")
        #expect(easy.run.type == "easy" && easy.verdict?.passed == true)
        #expect(easy.laps.allSatisfy { $0.kind == .other })
    }

    @Test func healthFixture() throws {
        let r = try Fixture.decode(HealthStatsDTO.self, "stats_health")
        #expect(r.days.count == 90 && r.bodyweight.count == 15)
        #expect(r.days[0].hrvMs == 70.6 && r.days[0].readiness?.readinessLevel == .green)
        let today = try #require(r.days.last)
        #expect(today.hrvMs == 56.7 && today.hrvBaselineMs == 60.5 && today.sleepS == 24920 && today.bodyBatteryHigh == 79)
        #expect(today.readiness?.readinessLevel == .green)
        #expect(r.summary?.hrv7d == 59.3)
        #expect(r.summary?.sleep7dS == 25269)
        #expect(r.summary?.bodyweightChange4wPct == -0.36)
        #expect(r.bodyweight.last?.source == "manual")
    }

    @Test func correlationsFixture() throws {
        let r = try Fixture.decode(CorrelationsDTO.self, "stats_correlations")
        #expect(r.items.map(\.id) == ["sleep_vs_lifting", "hrv_vs_lift_performance", "hrv_vs_easy_efficiency",
                                      "rhr_vs_easy_efficiency", "run_load_vs_lift_performance"])
        let first = r.items[0]
        #expect(first.r == -0.078 && first.n == 46 && first.points.count == 46 && first.note?.hasPrefix("n=46") == true)
        #expect(first.category == .recovery && first.insight?.hasPrefix("No clear link") == true)
        #expect(r.items.filter { $0.category == .hybrid }.map(\.id) == ["run_load_vs_lift_performance"])
    }

    // MARK: Tolerance

    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    @Test func unknownKeysNullsAndMissingArraysAreFine() throws {
        let lifts = try decode(LiftsStatsDTO.self, #"{"generated_at":"2026-10-25T11:24:35.995Z","extra":1,"lifts":[{"variant_id":1,"name":"Squat","future_field":{"a":1},"best_e1rm":null,"stalled":null}]}"#)
        #expect(lifts.lifts[0].bestE1rm == nil && lifts.lifts[0].isStalled == false && lifts.lifts[0].isActive)
        let empty = try decode(HealthStatsDTO.self, #"{"generated_at":"2026-10-25T11:24:35Z"}"#)
        #expect(empty.days.isEmpty && empty.bodyweight.isEmpty && empty.summary == nil)
        let corr = try decode(CorrelationsDTO.self, #"{"items":[{"id":"a","title":"A","points":[],"r":null,"n":2,"note":null}]}"#)
        #expect(corr.items[0].r == nil && corr.items[0].n == 2)
    }

    @Test func unknownEnumsFallBack() throws {
        let pr = try decode(LiftPRDTO.self, #"{"date":"2026-01-01","type":"tonnage","value":3}"#)
        #expect(pr.type == .unknown)
        let lap = try decode(LapDTO.self, #"{"index":1,"kind":"strides"}"#)
        #expect(lap.kind == .other)
        let day = try decode(DayReadinessDTO.self, #"{"level":"purple","reason":null}"#)
        #expect(day.readinessLevel == .unknown)
        let lap2 = try decode(LapDTO.self, #"{"index":2,"kind":"WORK"}"#)
        #expect(lap2.kind == .work)
    }

    @Test func plannedShapeChangeDoesNotHideTheAnalysis() throws {
        let json = #"{"run":{"id":"r1","date":"2026-10-01"},"planned":{"unexpected":true},"laps":[],"verdict":{"passed":null,"summary":null}}"#
        let a = try decode(RunAnalysisDTO.self, json)
        #expect(a.planned == nil && a.run.id == "r1" && a.verdict?.passed == nil && a.verdict?.reasons == [])
    }

    @Test func recentRunsListAndDay() throws {
        let runs = try decode([RunSummaryDTO].self, #"[{"id":"x","source":"garmin","title":"Run","started_at_utc":"2026-10-24T05:10:00Z","started_at_local":"2026-10-24T07:10:00","distance_m":8000.5,"avg_pace_s_per_km":330.2,"avg_hr":null,"time_in_zones_s":{"z1":3}}]"#)
        #expect(runs[0].day == "2026-10-24" && runs[0].distanceM == 8000.5 && runs[0].avgHr == nil)
    }

    @Test func dictionaryKeysSurviveSnakeCaseDecoding() throws {
        let w = try decode(WeekVolumeDTO.self, #"{"week_start":"2026-10-19","sets_by_muscle_group":{"upper_back":3,"chest":2}}"#)
        // The decoder may camel-case dictionary keys; MuscleGroup.display copes with both.
        let names = Set((w.setsByMuscleGroup ?? [:]).keys.map { MuscleGroup.display($0) })
        #expect(names == ["Upper back", "Chest"])
    }

}

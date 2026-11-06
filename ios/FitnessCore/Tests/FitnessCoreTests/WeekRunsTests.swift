import Foundation
import Testing
@testable import FitnessCore

private let week = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]

private func run(_ date: String, km: Double, id: String = "r", title: String? = nil) -> RunSummaryDTO {
    RunSummaryDTO(id: id + date, activityType: "running", title: title, startedAtLocal: date + "T07:00:00", startedAtUtc: nil,
                  durationS: 2400, distanceM: km * 1000, avgHr: nil, maxHr: nil, avgPaceSPerKm: nil, trainingEffectAerobic: nil)
}

private func plan(_ date: String, _ kind: String, status: String? = "proposed", km: Double? = nil, adaptive: Bool? = nil,

                  movedFrom: String? = nil, hr: HRRangeDTO? = HRRangeDTO(zone: "Z1-Z2", low: nil, high: nil, basis: nil),

                  reps: Int? = nil, strides: Int? = nil) -> PlanItemDTO {
    var structure: IntervalStructureDTO?
    if let reps { structure = IntervalStructureDTO(warmup: nil, work: IntervalWorkDTO(reps: reps, distanceM: 1000), recovery: nil, cooldown: nil) }
    if let strides {
        structure = IntervalStructureDTO(warmup: nil, work: nil, recovery: nil, cooldown: nil,
                                         strides: StridesDTO(reps: strides, durationS: 20, recoveryS: 60))
    }
    let session = PrescriptionDTO(type: kind, distanceKm: km, hr: hr, structure: structure, totalKm: nil)
    return PlanItemDTO(id: "id-\(date)-\(kind)", date: date, kind: kind, status: status, title: PlanFormat.runTitle(kind),
                       session: session, adaptive: adaptive, movedFrom: movedFrom)
}

private func model(_ items: [PlanItemDTO], runs: [RunSummaryDTO] = [], finished: [FinishedDayRef] = [], today: String = "2026-10-29") -> WeekRunsModel {
    WeekRunsModel.build(days: week, items: items, runs: runs, finished: finished, today: today)
}

@Suite(.tags(.workout, .fast))
struct WeekRunsModelTests {
    @Test func mergesPlanRunsAndStatuses() {
        let m = model([
            plan("2026-10-26", "upper"),
            plan("2026-10-28", "easy_run", km: 8, adaptive: true),
            plan("2026-10-30", "intervals", adaptive: true, reps: 5),
            plan("2026-11-01", "long_run", km: 11, adaptive: true),
        ], runs: [run("2026-10-28", km: 8.43)])
        #expect(m.rows.map(\.kind) == ["easy_run", "intervals", "long_run"])  // lifting is not a run row
        #expect(m.rows.map(\.state) == [.done, .upcoming, .upcoming])
        #expect(m.rows[0].actualKm == 8.43 && m.rows[0].distanceText == "\(Fmt.km(8.43)) km")  // actual, not planned
        #expect(m.rows[1].structureText == "5×1000 m*" && m.rows[1].distanceText == nil)
        #expect(m.rows[2].distanceText == "11 km*" && m.rows[2].hrLabel == "Z1–Z2")
    }

    @Test func todayMissedSkippedAndPlanDone() {
        let m = model([
            plan("2026-10-27", "easy_run", km: 6),                       // past, no run: missed
            plan("2026-10-28", "easy_run", status: "done", km: 7),       // plan says done, no run matched
            plan("2026-10-29", "intervals", reps: 4),                    // today
            plan("2026-10-31", "easy_run", status: "skipped", km: 5),
        ])
        #expect(m.rows.map(\.state) == [.missed, .done, .today, .skipped])
        #expect(m.rows[1].actualKm == nil && m.rows[1].distanceText == "7 km")
    }

    @Test func runsAfterTodayOrOfOtherKindsDoNotComplete() {
        var walk = run("2026-10-28", km: 3); walk.activityType = "walking"
        let m = model([plan("2026-10-28", "easy_run", km: 8), plan("2026-10-30", "intervals", reps: 5)],
                      runs: [walk, run("2026-10-30", km: 9)])
        #expect(m.rows.map(\.state) == [.missed, .upcoming])
    }

    @Test func runOnAnotherDayCountsWhenTypeMatchesWithinTwoDays() {
        // Intervals planned Fri, done Sat (today Sun).
        let m = model([plan("2026-10-30", "intervals", reps: 5)], runs: [run("2026-10-31", km: 9, title: "Intervals 5×1000")], today: "2026-11-01")
        #expect(m.rows[0].state == .done && m.rows[0].ranOn == "2026-10-31" && m.rows[0].actualKm == 9)
        // a run with an unknown title may stand in too
        let untitled = model([plan("2026-10-30", "intervals", reps: 5)], runs: [run("2026-10-31", km: 9)], today: "2026-11-01")
        #expect(untitled.rows[0].state == .done)
        // ... a run of another type or more than two days away may not
        let wrongType = model([plan("2026-10-30", "intervals", reps: 5)], runs: [run("2026-10-31", km: 9, title: "Easy run")], today: "2026-11-01")
        #expect(wrongType.rows[0].state == .missed && wrongType.rows[0].ranOn == nil)
        let tooFar = model([plan("2026-10-27", "easy_run", km: 6)], runs: [run("2026-10-30", km: 6, title: "Easy run")], today: "2026-11-01")
        #expect(tooFar.rows[0].state == .missed)
    }

    @Test func aRunIsNeverUsedTwice() {
        // Easy run on its planned day claims the run; the missed intervals do not get it as well.
        let m = model([plan("2026-10-29", "easy_run", km: 6), plan("2026-10-30", "intervals", reps: 5)],
                      runs: [run("2026-10-29", km: 6)], today: "2026-11-01")
        #expect(m.rows.map(\.state) == [.done, .missed])
    }

    @Test func modifiedPlanStatusCountsAsDone() {
        let m = model([plan("2026-10-30", "intervals", status: "modified", reps: 5)], today: "2026-11-01")
        #expect(m.rows[0].state == .done && m.rows[0].actualKm == nil)
    }

    @Test func movedAndMakeUpSessionsAppear() {
        let m = model([
            plan("2026-10-28", "easy_run", status: "moved", km: 8),                       // moved away: superseded
            plan("2026-10-30", "easy_run", status: "moved", km: 8, movedFrom: "2026-10-28"),
            plan("2026-10-31", "easy_run", km: 5),                                         // make-up
            plan("2026-11-01", "long_run", km: 11),
        ])
        #expect(m.rows.map(\.date) == ["2026-10-30", "2026-10-31", "2026-11-01"])
        #expect(m.rows[0].movedFrom == "2026-10-28" && m.rows[1].movedFrom == nil)
    }

    @Test func movedRowStaysWhenNothingSupersedesIt() {
        let m = model([plan("2026-10-30", "easy_run", status: "moved", km: 8)])
        #expect(m.rows.count == 1)
    }

    @Test func twoRunsOnOneDayMatchTwoSessions() {
        let m = model([plan("2026-10-27", "easy_run", km: 5), plan("2026-10-27", "intervals", reps: 4)],
                      runs: [run("2026-10-27", km: 5.1, id: "a")])
        #expect(m.rows.map(\.state).sorted { "\($0)" < "\($1)" } == [.done, .missed].sorted { "\($0)" < "\($1)" })
    }

    @Test func asteriskOnlyOnFutureAdaptiveItems() {
        let m = model([
            plan("2026-10-27", "easy_run", km: 6, adaptive: true),                    // missed
            plan("2026-10-28", "easy_run", km: 8, adaptive: true),                    // done
            plan("2026-10-29", "easy_run", km: 5, adaptive: true),                    // today, not run yet
            plan("2026-10-30", "intervals", adaptive: false, reps: 5),                // upcoming, fixed
            plan("2026-10-31", "easy_run", km: 4),                                    // upcoming, flag missing
            plan("2026-11-01", "long_run", km: 11, adaptive: true),                   // upcoming
        ], runs: [run("2026-10-28", km: 8)])
        #expect(m.rows.map(\.asterisk) == [false, false, true, false, false, true])
        #expect(m.rows.compactMap(\.distanceText).filter { $0.hasSuffix("*") } == ["5 km*", "11 km*"])
        #expect(m.showsFootnote)
        #expect(!model([plan("2026-10-30", "easy_run", km: 5)]).showsFootnote)
        #expect(WeekRunsModel.footnote == "* may change based on your last run, readiness and weekly volume")
    }

    @Test func asteriskFallsBackToTitleWithoutDistance() {
        let m = model([plan("2026-10-30", "easy_run", adaptive: true)])
        #expect(m.rows[0].asterisk && m.rows[0].asteriskOnTitle)
    }

    @Test func paceWhenNoHeartRate() {
        var item = plan("2026-10-30", "easy_run", km: 5, hr: nil)
        item.session?.pace = PaceRangeDTO(minSPerKm: 320, maxSPerKm: 340, label: nil, note: nil)
        let row = model([item]).rows[0]
        #expect(row.hrLabel == nil && row.paceText == "5:20–5:40")
    }

    @Test func liftLineMarksDoneDays() {
        let m = model([plan("2026-10-26", "upper"), plan("2026-10-27", "lower"), plan("2026-10-29", "upper"),
                       plan("2026-10-30", "intervals", reps: 5)],
                      finished: [FinishedDayRef(plannedId: "id-2026-10-26-upper", day: "2026-10-26", kind: "upper"),
                                 FinishedDayRef(plannedId: nil, day: "2026-10-27", kind: "lower")])
        #expect(m.lifts.map(\.done) == [true, true, false])
        let mon = DayLabel.weekday("2026-10-26"), tue = DayLabel.weekday("2026-10-27"), thu = DayLabel.weekday("2026-10-29")
        #expect(m.liftLine == "Upper \(mon) ✓ · Lower \(tue) ✓ · Upper \(thu)")
        #expect(model([plan("2026-10-30", "intervals", reps: 5)]).liftLine == nil)
    }

    @Test func startWorkoutIsNotOnToday() {
        // A planned lifting card never offers "start": only done / resume / nothing.
        #expect(TodayLogic.liftCardAction(completed: false, hasActiveWorkout: false) == .none)
        #expect(TodayLogic.liftCardAction(completed: false, hasActiveWorkout: true) == .resume)
        #expect(TodayLogic.liftCardAction(completed: true, hasActiveWorkout: true) == .done)
        // The stand-alone card exists only to resume.
        #expect(!TodayLogic.showsResumeCard(hasActiveWorkout: false, hasLiftingToday: false))
        #expect(TodayLogic.showsResumeCard(hasActiveWorkout: true, hasLiftingToday: false))
        #expect(!TodayLogic.showsResumeCard(hasActiveWorkout: true, hasLiftingToday: true))
    }
}

@Suite(.tags(.workout, .decoding, .fast))
struct PlanFieldsDecodingTests {
    private func decode(_ json: String) throws -> PlanItemDTO {
        try APICoding.makeDecoder().decode(PlanItemDTO.self, from: Data(json.utf8))
    }

    @Test func newFieldsDecode() throws {
        let item = try decode("""
        {"id":"p1","date":"2026-10-30","kind":"intervals","status":"moved","title":"Intervals","adaptive":true,
         "adapts_on":["last_run","readiness"],"moved_from":"2026-10-28","change_reason":"Moved: legs heavy"}
        """)
        #expect(item.adaptive == true && item.adaptsOn == ["last_run", "readiness"])
        #expect(item.movedFrom == "2026-10-28" && item.changeReason == "Moved: legs heavy" && item.planStatus == .moved)
        // survives the cache round trip (re-encoded payload)
        let back = try APICoding.makeDecoder().decode(PlanItemDTO.self, from: APICoding.makeEncoder().encode(item))
        #expect(back == item)
    }

    @Test func missingFieldsStayNil() throws {
        let item = try decode(#"{"date":"2026-10-30","kind":"easy_run","status":"proposed"}"#)
        #expect(item.adaptive == nil && item.adaptsOn == nil && item.movedFrom == nil && item.changeReason == nil)
        #expect(item.planStatus == .proposed)
    }

    @Test func unknownStatusAndWrongShapesAreTolerated() throws {
        let item = try decode(#"{"date":"2026-10-30","kind":"easy_run","status":"rescheduled","adaptive":"yes","adapts_on":"last_run","moved_from":5}"#)
        #expect(item.planStatus == .unknown && item.adaptive == nil && item.adaptsOn == nil && item.movedFrom == nil)
        #expect(try decode(#"{"date":"2026-10-30","kind":"easy_run"}"#).planStatus == .unknown)
        for s in ["proposed", "approved", "done", "skipped", "modified", "moved"] {
            #expect(PlanStatus(rawValue: s) != nil)
        }
    }

    @Test func existingFixturesStillDecode() throws {
        let week = try Fixture.decode(PlanWeekDTO.self, "plan_week")
        #expect(!week.days.isEmpty && week.days.flatMap(\.sessions).allSatisfy { $0.movedFrom == nil || !$0.movedFrom!.isEmpty })
    }

    @Test func easyRunStridesFollowTheDistance() {
        let m = model([
            plan("2026-10-28", "easy_run", km: 8, adaptive: true, strides: 6),
            plan("2026-11-01", "long_run", km: 11, adaptive: true, strides: 0),
        ], today: "2026-10-27")
        #expect(m.rows[0].distanceText == "\(Fmt.km(8)) km*" && m.rows[0].structureText == "6 × 20 s strides")
        #expect(m.rows[1].structureText == nil)  // 0 reps = no strides
        #expect(PlanFormat.strides(StridesDTO(reps: 6, durationS: 20)) == "6 × 20 s strides")
        #expect(PlanFormat.stridesDetail(StridesDTO(reps: 6, durationS: 20, recoveryS: 60))?.hasSuffix("60 s easy jog between") == true)
    }

    @Test func fixtureEasyRunDecodesStrides() throws {
        let week = try Fixture.decode(PlanWeekDTO.self, "plan_week")
        let strides = week.days.flatMap(\.sessions).compactMap { $0.session?.structure?.strides }
        #expect(strides.contains { ($0.reps ?? 0) > 0 && $0.durationS != nil })
    }

    @Test func adaptsOnLabels() {
        #expect(PlanFormat.adaptsOnLabel("last_run") == "Your last run")
        #expect(PlanFormat.adaptsOnLabel("weekly_volume") == "Your weekly volume")
        #expect(PlanFormat.adaptsOnLabel("heat_index") == "Heat index")
    }
}

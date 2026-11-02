import Foundation
import Testing
@testable import FitnessBuddy

/// Pure helpers behind the restyled Coach tab: "old → new" lines, check-in gauge, settings screen values.
@Suite(.tags(.coach, .fast))
struct CoachTabTests {
    // MARK: Old → new

    @Test func unitIsWrittenOnceAfterTheNewValue() {
        let kg = CoachChangeFormat.parts(from: .number(82.5), to: .number(85), path: "exercise_overrides.bench.target_weight_kg")
        #expect(kg.old == Fmt.number(82.5) && kg.new == "85 kg")
        let pct = CoachChangeFormat.parts(from: .number(10), to: .number(12), path: "running.weekly_volume_cap_pct")
        #expect(pct.old == "10" && pct.new == "12 %")
        let range = CoachChangeFormat.parts(from: .array([.number(7), .number(12)]), to: .array([.number(8), .number(12)]), path: "x.rep_range")
        #expect(range.old == "7–12" && range.new == "8–12")
    }

    @Test func mixedOrMissingUnitsKeepBothTexts() {
        let toNull = CoachChangeFormat.parts(from: .number(4), to: .null, path: "running.long_km")
        #expect(toNull.old == "4 km" && toNull.new == "none")
        let plain = CoachChangeFormat.parts(from: .number(6), to: .number(7), path: "running.intervals.reps_max")
        #expect(plain.old == "6" && plain.new == "7")
    }

    @Test func titleFallsBackToAReadablePath() {
        #expect(CoachChangeFormat.title(label: "Interval reps (maximum)", param: "running.intervals.reps_max") == "Interval reps (maximum)")
        #expect(CoachChangeFormat.title(label: "exercise_overrides.triceps_extension.target_weight",
                                        param: "exercise_overrides.triceps_extension.target_weight") == "Triceps extension target weight")
        #expect(CoachChangeFormat.title(label: "", param: "deload_week") == "Deload week")
    }

    @Test func cardLinesPreferOpenChangesThenSchedule() {
        let p = CoachProposalDTO(
            id: "1", createdAt: nil, status: .partiallyApplied, trigger: CoachTriggerDTO(type: .liftStall, title: "Bench stalled"),
            changes: [
                CoachChangeDTO(id: "a", param: "x.target_weight_kg", label: "Bench Press", from: .number(82.5), to: .number(85)),
                CoachChangeDTO(id: "b", param: "x.reps", label: "Done", status: .applied),
                CoachChangeDTO(id: "c", param: "x.bad", label: "Bad", valid: false, status: .invalid),
            ],
            scheduleChanges: [CoachScheduleChangeDTO(id: "s", action: .move, label: "Move intervals Fri → Sat")])
        let lines = CoachChangeFormat.lines(for: p)
        #expect(lines.map(\.id) == ["a", "s"])
        #expect(lines[0].kind == .value(old: Fmt.number(82.5), new: "85 kg") && lines[0].title == "Bench Press")
        #expect(lines[1].kind == .schedule(symbol: ScheduleAction.move.symbol))
    }

    @Test func cardLinesFallBackToEveryChangeWhenNoneIsOpen() {
        let p = CoachProposalDTO(
            id: "2", createdAt: nil, status: .pending, trigger: CoachTriggerDTO(type: .ask, title: "Q"),
            changes: [CoachChangeDTO(id: "x", param: "p.x", label: "X", valid: false, status: .invalid)])
        #expect(CoachChangeFormat.lines(for: p).map(\.id) == ["x"])
    }

    // MARK: Check-ins

    @Test func gaugeFillsOneSegmentPerCall() {
        #expect(CoachCheckIns.segments(used: 1, limit: 4) == [true, false, false, false])
        #expect(CoachCheckIns.segments(used: 6, limit: 4) == [true, true, true, true])
        #expect(CoachCheckIns.segments(used: -1, limit: 2) == [false, false])
        #expect(CoachCheckIns.segments(used: 0, limit: 0) == nil)
        #expect(CoachCheckIns.segments(used: 0, limit: CoachCheckIns.maxSegments + 1) == nil)
    }

    @Test func remainingText() {
        #expect(CoachCheckIns.remainingText(used: 1, limit: 4) == "3 left this week")
        #expect(CoachCheckIns.remainingText(used: 4, limit: 4) == "All used this week")
        #expect(CoachCheckIns.remainingText(used: 0, limit: 0) == "No automatic check-ins")
    }

    // MARK: Navigation

    @Test func appRoutesMapToCoachScreens() {
        #expect(CoachScreen(.proposal("7")) == .proposal("7"))
        #expect(CoachScreen(.history) == .configHistory)
    }

    // MARK: Settings

    @Test func providerKeysDecodeAndDriveConnection() throws {
        let json = #"{"provider":"claude_code","credential_configured":true,"billing":"subscription","model":"claude-opus-5-5","approval_mode":"manual","api_key_configured":false,"dry_run":false,"calls_this_week":1,"max_calls_per_week":4}"#
        let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: Data(json.utf8))
        #expect(s.isConnected && !s.apiKeyConfigured)
        #expect(s.model == "claude-opus-5-5" && s.billing == "subscription")
        #expect(CoachSettingsView.runsOn(s) == "Claude subscription")
    }

    @Test func olderServersFallBackToTheApiKeyFlag() throws {
        // what an older server sends: no provider keys
        let old = #"{"approval_mode":"manual","api_key_configured":true,"dry_run":false,"calls_this_week":0,"max_calls_per_week":4}"#
        let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: Data(old.utf8))
        #expect(s.credentialConfigured == nil && s.isConnected)
        #expect(CoachSettingsView.runsOn(s) == nil)
        #expect(!CoachSettingsDTO(apiKeyConfigured: false).isConnected)
        #expect(CoachSettingsView.runsOn(CoachSettingsDTO(provider: "api")) == "Claude API")
    }
}

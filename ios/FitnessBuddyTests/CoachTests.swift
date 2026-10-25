import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try CoachCoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

private func change(_ id: String, valid: Bool = true, status: CoachChangeStatus = .proposed) -> CoachChangeDTO {
    CoachChangeDTO(id: id, param: "p." + id, valid: valid, status: valid ? status : .invalid)
}

private func proposal(_ id: String, _ status: CoachStatus, changes: [CoachChangeDTO] = [], at: TimeInterval = 0) -> CoachProposalDTO {
    CoachProposalDTO(id: id, createdAt: Date(timeIntervalSince1970: at), status: status,
                     trigger: CoachTriggerDTO(type: .ask, title: "t " + id), changes: changes)
}

// MARK: - Decoding

struct CoachDecodingTests {
    @Test func proposalsFixture() throws {
        let list = try CoachCoding.makeDecoder().decode(CoachProposalListDTO.self, from: Fixture.data("coach_proposals"))
        #expect(list.items.map(\.id) == ["2", "1"])  // string ids, newest first
        let applied = list.items[0]
        #expect(applied.status == .applied && applied.appliedConfigVersion == "v3.3" && applied.confidence == .high)
        #expect(applied.trigger.type == .ask && applied.trigger.ref == CoachTriggerRefDTO(kind: nil, id: nil))
        #expect(applied.changes[0].status == .applied && applied.changes[0].param == "running.intervals.reps_max")
        #expect(applied.sessionOverrides.isEmpty && applied.costUsd == 0.0971)
        let p = list.items[1]
        #expect(p.status == .pending && p.isPending && p.confidence == .medium && p.trigger.type == .ask)
        #expect(p.trigger.ref == CoachTriggerRefDTO(kind: "lift", id: "127"))
        #expect(p.createdAt != nil && p.question?.hasPrefix("My triceps extension") == true && p.notesForUser != nil)
        #expect(p.changes.map(\.id) == ["1", "2", "3"] && p.sessionOverrides.count == 1)
        #expect(p.changes[0].from == .array([.number(7), .number(12)]) && p.changes[0].to == .array([.number(8), .number(12)]))
        #expect(p.changes[0].changeText == "7–12 → 8–12" && p.changes[0].risk == .low)
        #expect(p.changes[1].risk == .high && p.changes[1].changeText == "10 % → 12 %")
        #expect(p.changes[2].valid == false && p.changes[2].status == .invalid && p.changes[2].validationError?.contains("allowlist") == true)
        #expect(p.sessionOverrides[0].session == "Upper" && p.sessionOverrides[0].date == "2026-10-26")
        #expect(CoachLogic.defaultSelection(p.changes) == ["1", "2"])
    }

    @Test func detailFixtureHasInputSummary() throws {
        let p = try CoachCoding.makeDecoder().decode(CoachProposalDTO.self, from: Fixture.data("coach_proposal_detail"))
        #expect(p.id == "1" && p.status == .pending && p.changes.count == 3)
        guard case .object(let summary) = p.inputSummary else { Issue.record("no input_summary"); return }
        // free-form keys survive untouched (no snake -> camel conversion)
        #expect(summary.keys.contains("past_advisor_changes") && summary.keys.contains("readiness_14d"))
        #expect(summary["lifts"] != nil && summary["config"] != nil)
    }

    @Test func askAndApproveResponses() throws {
        let ask = try CoachCoding.makeDecoder().decode(CoachProposalDTO.self, from: Fixture.data("coach_ask_response"))
        #expect(ask.status == .queued && ask.id == "1" && ask.diagnosis == nil && ask.confidence == nil && ask.changes.isEmpty)
        #expect(ask.costUsd == nil && ask.error == nil && ask.trigger.ref?.id == "127" && !ask.isPending)
        let done = try CoachCoding.makeDecoder().decode(CoachProposalDTO.self, from: Fixture.data("coach_approve_response"))
        #expect(done.status == .applied && done.appliedConfigVersion == "v3.3" && !done.isPending)
    }

    @Test func historyFixture() throws {
        let h = try CoachCoding.makeDecoder().decode(CoachHistoryDTO.self, from: Fixture.data("coach_history"))
        #expect(h.versions.map(\.version) == ["v3.3", "v3.2", "v3.1", "v3", "v2", "v1"])
        let v33 = h.versions[0]
        #expect(v33.active && v33.author == .advisor && v33.consultationId == "2" && v33.outcome == nil)
        #expect(v33.diff == [CoachDiffEntryDTO(path: "running.intervals.reps_max", old: .number(6), new: .number(7))])
        let v32 = h.versions[1]
        #expect(!v32.active && v32.author == .user && v32.consultationId == nil)
        // sorted, keys intact (snake_case, not converted), null -> number
        #expect(v32.diff.map(\.path) == ["running.interval_fast_flag_pct", "running.interval_pace_tolerance_pct"])
        #expect(v32.diff[0].old == .null && v32.diff[0].new == .number(5) && v32.diff[0].changeText == "none → 5 %")
        #expect(h.versions[3].author == .seed && h.versions[3].diff.isEmpty && h.versions[3].reason == nil)
        #expect(h.versions.filter(\.active).count == 1)
    }

    @Test func outcomeMetricsAreDictsKeyedByName() throws {
        let v = try decode(CoachVersionDTO.self, """
        {"version": "v3.4", "author": "advisor", "active": false, "diff": {},
         "outcome": {"verdict": "improved", "notes": "Bench e1RM up.",
                     "metric_before": {"bench_press_e1rm_kg": 70.0, "aerobic_efficiency": null},
                     "metric_after": {"bench_press_e1rm_kg": 71.0, "aerobic_efficiency": 1.31}}}
        """)
        let o = try #require(v.outcome)
        #expect(o.verdict == .improved && o.metricBefore?.isNull == false)
        let rows = CoachOutcomeDTO.metricRows(before: o.metricBefore, after: o.metricAfter)
        #expect(rows == [
            .init(name: "aerobic efficiency", change: "none → " + Fmt.number(1.31)),
            .init(name: "bench press e1rm kg", change: "70 kg → 71 kg"),
        ])
        #expect(CoachOutcomeDTO.metricRows(before: nil, after: nil).isEmpty)
        #expect(CoachOutcomeDTO.metricRows(before: .number(1), after: .number(2)).map(\.change) == ["1 → 2"])
    }

    @Test func toleratesUnknownValuesAndNumericIds() throws {
        let list = try decode(CoachProposalListDTO.self, """
        {"items": [
          {"id": 7, "status": "from_the_future", "trigger": {"type": "somethingnew", "title": "Future"}, "changes": [{"id": 3, "param": "a.b", "risk": "scary", "valid": true}]},
          {"id": "8", "status": "awaiting_key", "trigger": {"type": "ask", "title": "Q", "ref": {"kind": "week", "id": "2026-10-19"}}},
          {"id": "9", "status": "failed", "error": "Answer cut off", "trigger": {"type": "data_anomaly", "title": "HRV"}},
          {"id": "10", "status": "dry_run", "trigger": {"type": "weekly_review", "title": "Week"}}
        ]}
        """)
        #expect(list.items[0].id == "7" && list.items[0].status == .unknown && list.items[0].trigger.type == .unknown)
        #expect(list.items[0].changes[0].id == "3" && list.items[0].changes[0].risk == .unknown && list.items[0].changes[0].status == .proposed)
        #expect(list.items[1].status == .awaitingKey && list.items[1].trigger.ref?.kind == "week" && list.items[1].canDismiss)
        #expect(list.items[2].error == "Answer cut off" && list.items[2].canDismiss && list.items[3].canDismiss)
        #expect(!list.items[0].canDismiss)
    }

    @Test func partiallyAppliedWithRemainingChangesCountsAsPending() throws {
        func make(_ statuses: [CoachChangeStatus]) -> CoachProposalDTO {
            var p = proposal("p", .partiallyApplied)
            p.changes = statuses.enumerated().map { CoachChangeDTO(id: "c\($0.offset)", param: "x", valid: $0.element != .invalid, status: $0.element) }
            return p
        }
        #expect(make([.applied, .proposed]).isPending)
        #expect(!make([.applied, .rejected]).isPending)
        #expect(!make([.applied, .invalid]).isPending)
        #expect(!proposal("a", .applied).isPending && proposal("b", .pending).isPending)
        let g = CoachLogic.group([make([.applied, .proposed]), make([.applied, .rejected])])
        #expect(g.pending.count == 1 && g.recent.count == 1)
    }

    @Test func settingsFixtureAndFallbacks() throws {
        let s = try CoachCoding.makeDecoder().decode(CoachSettingsDTO.self, from: Fixture.data("coach_settings"))
        #expect(s == CoachSettingsDTO(approvalMode: .manual, apiKeyConfigured: true, dryRun: false, callsThisWeek: 0, maxCallsPerWeek: 4))
        let odd = try decode(CoachSettingsDTO.self, #"{"approval_mode":"yolo","api_key_configured":true,"calls_this_week":2.0}"#)
        #expect(odd.approvalMode == .unknown && odd.apiKeyConfigured && odd.callsThisWeek == 2 && odd.maxCallsPerWeek == 0)
    }

    @Test func jsonValueCoversEveryType() throws {
        let v = try decode(JSONValue.self, #"{"n":1.5,"i":3,"s":"x","b":false,"z":null,"a":[1,"two",[true]],"o":{"k_k":{"deep":0}}}"#)
        guard case .object(let o) = v else { Issue.record("not an object"); return }
        #expect(o["n"] == .number(1.5) && o["i"] == .number(3) && o["s"] == .string("x") && o["b"] == .bool(false) && o["z"] == .null)
        #expect(o["a"] == .array([.number(1), .string("two"), .array([.bool(true)])]))
        #expect(o["o"] == .object(["k_k": .object(["deep": .number(0)])]))
        let again = try decode(JSONValue.self, String(decoding: try JSONEncoder().encode(v), as: UTF8.self))
        #expect(again == v)
    }

    @Test func requestsEncodeSnakeCase() throws {
        let ask = try JSONEncoder().encode(CoachAskRequest(scope: .lift, refId: "127", question: "Why?"))
        #expect(String(decoding: ask, as: UTF8.self).contains(#""ref_id":"127""#))
        let approve = try JSONEncoder().encode(CoachApproveRequest(changeIds: ["a", "b"]))
        #expect(String(decoding: approve, as: UTF8.self) == #"{"change_ids":["a","b"]}"#)
        let mode = try JSONEncoder().encode(CoachSettingsUpdate(approvalMode: .autoLowRisk))
        #expect(String(decoding: mode, as: UTF8.self) == #"{"approval_mode":"auto-low-risk"}"#)
    }

    @Test func planTodayCoachBlock() throws {
        let old = try APICoding.makeDecoder().decode(PlanTodayDTO.self, from: Fixture.data("plan_today"))
        #expect(old.coach == nil)  // older backend: no block
        let day = try APICoding.makeDecoder().decode(PlanTodayDTO.self, from: Fixture.data("coach_plan_today"))
        #expect(day.coach == CoachSummaryDTO(pendingCount: 1, latestPendingId: "1"))
        #expect(!day.sessions.isEmpty)
        var obj = try #require(JSONSerialization.jsonObject(with: Fixture.data("plan_today")) as? [String: Any])
        obj["coach"] = ["pending_count": 0, "latest_pending_id": NSNull()]
        let none = try APICoding.makeDecoder().decode(PlanTodayDTO.self, from: JSONSerialization.data(withJSONObject: obj))
        #expect(none.coach == CoachSummaryDTO(pendingCount: 0, latestPendingId: nil))
        obj["coach"] = "garbage"
        #expect(try APICoding.makeDecoder().decode(PlanTodayDTO.self, from: JSONSerialization.data(withJSONObject: obj)).coach == nil)
    }
}

// MARK: - Formatting

struct CoachFormatTests {
    @Test func repRange() {
        #expect(CoachValueFormat.text(.array([.number(7), .number(12)]), path: "strength.rep_range") == "7–12")
        #expect(CoachValueFormat.change(from: .array([.number(7), .number(12)]), to: .array([.number(8), .number(12)]), path: "strength.rep_range") == "7–12 → 8–12")
    }

    @Test func unitsFollowThePath() {
        #expect(CoachValueFormat.text(.number(10), path: "strength.deload.load_pct") == "10 %")
        #expect(CoachValueFormat.text(.number(4), path: "running.easy_run_km") == "4 km")
        #expect(CoachValueFormat.text(.number(2.5), path: "increments.barbell_kg") == Fmt.number(2.5) + " kg")
        #expect(CoachValueFormat.text(.number(5), path: "strength.timed_step_s") == "5 s")
        #expect(CoachValueFormat.change(from: .number(200), to: .number(250), path: "running.intervals.recovery_distance_m") == "200 m → 250 m")
        #expect(CoachValueFormat.text(.number(400), path: "running.intervals.rep_distance_m") == "400 m")
        #expect(CoachValueFormat.text(.number(150), path: "rest_min") == "150 min")
        #expect(CoachValueFormat.text(.number(145), path: "running.hr_ceiling_bpm") == "145 bpm")
        #expect(CoachValueFormat.text(.number(0.8), path: "pct_hrmax") == Fmt.number(0.8))  // not a *_pct suffix: a fraction
        #expect(CoachValueFormat.text(.array([.number(60), .number(75)]), path: "easy.pct") == "60–75 %")
        #expect(CoachValueFormat.change(from: .number(3), to: .number(4), path: "running.intervals.pace_tolerance_pct") == "3 % → 4 %")
    }

    @Test func numbersBoolsNullAndStrings() {
        #expect(CoachValueFormat.text(.number(2)) == "2")
        #expect(CoachValueFormat.text(.number(2.346)) == Fmt.number(2.35))
        #expect(CoachValueFormat.text(.number(-1.5)) == "-" + Fmt.number(1.5))
        #expect(CoachValueFormat.text(.bool(true)) == "on" && CoachValueFormat.text(.bool(false)) == "off")
        #expect(CoachValueFormat.text(.null) == "none" && CoachValueFormat.text(.string("same_effort")) == "same_effort")
        #expect(CoachValueFormat.text(.string("")) == "empty")
    }

    @Test func listsAndNestedValues() {
        #expect(CoachValueFormat.text(.array([.number(1), .number(2), .number(3)])) == "1, 2, 3")
        #expect(CoachValueFormat.text(.array((1...8).map { .number(Double($0)) })) == "1, 2, 3, 4, 5, 6 +2")
        #expect(CoachValueFormat.text(.array([])) == "empty")
        let nested = JSONValue.object(["easy_low": .number(130), "easy_high_bpm": .number(145)])
        #expect(CoachValueFormat.text(nested, path: "running.hr") == "easy high bpm: 145 bpm, easy low: 130")
        let big = JSONValue.object(Dictionary(uniqueKeysWithValues: (1...6).map { ("k\($0)", JSONValue.number(Double($0))) }))
        #expect(CoachValueFormat.text(big).hasSuffix("+2"))
        #expect(CoachValueFormat.text(.array([.array([.number(1), .number(2)]), .array([.number(3), .number(4)])])) == "1–2, 3–4")
    }

    @Test func newParamPathsFormat() {
        let slug = "exercise_overrides.triceps_extension"
        #expect(CoachValueFormat.change(from: .array([.number(7), .number(12)]), to: .array([.number(8), .number(12)]), path: slug + ".rep_range") == "7–12 → 8–12")
        #expect(CoachValueFormat.change(from: .number(3), to: .number(4), path: slug + ".sets") == "3 → 4")
        #expect(CoachValueFormat.change(from: .string("cable_triceps_extension"), to: .string("rope_pushdown"), path: slug + ".variant") == "cable_triceps_extension → rope_pushdown")
        #expect(CoachValueFormat.change(from: .number(127), to: .number(131), path: slug + ".variant") == "127 → 131")
        #expect(CoachValueFormat.change(from: .string("manual"), to: .string("auto-low-risk"), path: "advisor.approval_mode") == "manual → auto-low-risk")
        #expect(CoachValueFormat.text(.number(2.5), path: "increments.dumbbell_increment_kg") == Fmt.number(2.5) + " kg")
        #expect(CoachValueFormat.text(.number(1), path: "running.long_run.step_km") == "1 km")
    }

    @Test func changeTextOnTheDTO() {
        let c = CoachChangeDTO(id: "x", param: "strength.rep_range", from: .array([.number(7), .number(12)]), to: .array([.number(8), .number(12)]))
        #expect(c.changeText == "7–12 → 8–12")
        #expect(CoachChangeDTO(id: "y", param: "w.pct", from: .null, to: .number(18)).changeText == "none → 18 %")
    }
}

// MARK: - Rules

struct CoachLogicTests {
    @Test func onlyValidChangesAreSelectableAndDefaultOn() {
        let changes = [change("a"), change("b", valid: false), change("c"), change("d", status: .applied)]
        #expect(CoachLogic.isSelectable(changes[0]) && !CoachLogic.isSelectable(changes[1]) && !CoachLogic.isSelectable(changes[3]))
        #expect(CoachLogic.defaultSelection(changes) == ["a", "c"])
    }

    @Test func togglingIgnoresInvalidAndUnknown() {
        let changes = [change("a"), change("b", valid: false), change("c")]
        var sel = CoachLogic.defaultSelection(changes)
        sel = CoachLogic.toggled("a", in: sel, changes: changes)
        #expect(sel == ["c"])
        #expect(CoachLogic.toggled("b", in: sel, changes: changes) == sel)
        #expect(CoachLogic.toggled("zzz", in: sel, changes: changes) == sel)
        #expect(CoachLogic.toggled("a", in: sel, changes: changes) == ["a", "c"])
    }

    @Test func approvalIdsKeepOrderAndDropInvalid() {
        let changes = [change("a"), change("b", valid: false), change("c")]
        #expect(CoachLogic.approvalIds(selection: ["c", "b", "a"], changes: changes) == ["a", "c"])
        #expect(CoachLogic.approvalIds(selection: [], changes: changes).isEmpty)
    }

    @Test func onlyQueuedAndRunningArePolled() {
        for s in [CoachStatus.queued, .running] { #expect(CoachLogic.shouldPoll(s)) }
        for s in [CoachStatus.pending, .applied, .partiallyApplied, .rejected, .failed, .dryRun, .awaitingKey, .unknown] {
            #expect(!CoachLogic.shouldPoll(s))
        }
    }

    @Test func bannerVisibility() {
        #expect(!CoachLogic.bannerVisible(pendingCount: 0) && !CoachLogic.bannerVisible(pendingCount: -1))
        #expect(CoachLogic.bannerVisible(pendingCount: 1))
        #expect(CoachLogic.bannerTitle(pendingCount: 1) == "Coach has 1 proposal to review")
        #expect(CoachLogic.bannerTitle(pendingCount: 3) == "Coach has 3 proposals to review")
    }

    @Test func suggestionsPerScope() {
        #expect(CoachLogic.suggestions(for: .lift).contains("Why am I stalling?"))
        #expect(CoachLogic.suggestions(for: .lift).contains("Should I change my rep range?"))
        #expect(CoachLogic.suggestions(for: .run).contains("Is my easy pace right?"))
        for scope in CoachScope.allCases {
            let s = CoachLogic.suggestions(for: scope)
            #expect((2...3).contains(s.count) && Set(s).count == s.count)
        }
        #expect(Set(CoachScope.allCases.map { CoachLogic.suggestions(for: $0) }).count == 4)
    }

    @Test func versionLabels() {
        #expect(CoachLogic.nextVersion(after: "v3.3") == "v3.4")
        #expect(CoachLogic.nextVersion(after: "v3") == "v3.1")
        #expect(CoachLogic.nextVersion(after: "3.9") == "3.10")
        #expect(CoachLogic.nextVersion(after: "main") == nil && CoachLogic.nextVersion(after: nil) == nil)
        #expect(CoachLogic.versionLabel("3.2") == "v3.2" && CoachLogic.versionLabel("v3.2") == "v3.2")
    }

    @Test func groupingOrdersNewestFirst() {
        let g = CoachLogic.group([
            proposal("old", .pending, at: 1), proposal("new", .pending, at: 5), proposal("q", .queued, at: 3),
            proposal("k", .awaitingKey, at: 2), proposal("done", .applied, at: 9), proposal("no", .rejected, at: 8),
            proposal("dry", .dryRun, at: 7), proposal("bad", .failed, at: 6),
        ])
        #expect(g.pending.map(\.id) == ["new", "old"])
        #expect(g.inFlight.map(\.id) == ["q", "k"])
        #expect(g.recent.map(\.id) == ["done", "no", "dry", "bad"])
    }
}

// MARK: - Polling

@MainActor
struct CoachPollerTests {
    private final class Script: @unchecked Sendable {
        var statuses: [CoachStatus]
        var fetches = 0
        var sleeps: [TimeInterval] = []
        init(_ statuses: [CoachStatus]) { self.statuses = statuses }
    }

    private func run(_ script: Script, timeout: TimeInterval = 120, failing: Bool = false) async -> (CoachPoller.Outcome, [CoachStatus]) {
        var seen: [CoachStatus] = []
        let outcome = await CoachPoller.run(interval: 3, timeout: timeout, sleep: { script.sleeps.append($0) }, fetch: {
            script.fetches += 1
            if failing { throw CoachError.needsConnection }
            let status = script.statuses.isEmpty ? CoachStatus.running : script.statuses.removeFirst()
            return proposal("p", status)
        }, onUpdate: { seen.append($0.status) })
        return (outcome, seen)
    }

    @Test func stopsAsSoonAsTheStatusIsTerminal() async {
        let script = Script([.queued, .running, .pending, .running])
        let (outcome, seen) = await run(script)
        #expect(outcome == .finished(.pending))
        #expect(script.fetches == 3 && seen == [.queued, .running, .pending])
        #expect(script.sleeps == [3, 3, 3])
    }

    @Test func everyNonPollingStatusEndsThePoll() async {
        for status in [CoachStatus.failed, .awaitingKey, .dryRun, .applied, .rejected] {
            let (outcome, _) = await run(Script([status]))
            #expect(outcome == .finished(status))
        }
    }

    @Test func givesUpAfterTwoMinutes() async {
        let script = Script([])  // running forever
        let (outcome, _) = await run(script)
        #expect(outcome == .timedOut)
        #expect(script.fetches == 40)  // 120 s / 3 s
    }

    @Test func failedPollsCountAsTicksAndNeverCrash() async {
        let script = Script([])
        let (outcome, seen) = await run(script, timeout: 30, failing: true)
        #expect(outcome == .timedOut && script.fetches == 10 && seen.isEmpty)
    }

    @Test func cancellationStopsThePoll() async {
        let task = Task { @MainActor in
            await CoachPoller.run(interval: 3, timeout: 120, sleep: { _ in withUnsafeCurrentTask { $0?.cancel() } },
                                  fetch: { proposal("p", .running) })
        }
        #expect(await task.value == .cancelled)
    }
}

// MARK: - Repository

@MainActor
struct CoachRepositoryTests {
    private final class Server: @unchecked Sendable {
        var calls: [String] = []
        var bodies: [String: String] = [:]
        var failure: APIError?
        var queries: [String: String] = [:]

        func respond(_ method: String, _ path: String, _ query: [URLQueryItem] = [], _ body: Data?) throws -> Data {
            calls.append(method + " " + path)
            queries[method + " " + path] = query.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
            if let body { bodies[method + " " + path] = String(decoding: body, as: UTF8.self) }
            if let failure, method != "GET" { throw failure }
            switch (method, path) {
            case ("GET", "/api/v1/coach/proposals"):
                return try Fixture.data("coach_proposals")
            case ("GET", "/api/v1/coach/history"): return try Fixture.data("coach_history")
            case ("GET", "/api/v1/coach/settings"): return try Fixture.data("coach_settings")
            case ("PUT", "/api/v1/coach/settings"):
                return Data(#"{"approval_mode":"auto-low-risk","api_key_configured":true,"dry_run":false,"calls_this_week":0,"max_calls_per_week":4}"#.utf8)
            case ("POST", let p) where p.hasSuffix("/approve"):
                return try Fixture.data("coach_approve_response")
            case ("POST", "/api/v1/coach/ask"):
                return try Fixture.data("coach_ask_response")
            case ("POST", _): return Data("{}".utf8)
            default: throw APIError.http(404, nil)
            }
        }
    }

    private func makeRepo(_ server: Server, configured: Bool = true) -> CoachRepository {
        let settings = SettingsStore(defaults: UserDefaults(suiteName: "coach-tests-\(UUID().uuidString)")!, loadToken: false)
        if configured { settings.backendURL = "http://localhost:8000"; settings.apiToken = "test-token" }
        let repo = CoachRepository(settings: settings, network: NetworkMonitor(), store: MemoryStatsCache())
        repo.transportOverride = { method, path, query, body in try server.respond(method, path, query, body) }
        return repo
    }

    @Test func loadFetchesCachesAndReportsPending() async {
        let server = Server()
        let repo = makeRepo(server)
        var pending: (Int, String?)?
        repo.onPendingChanged = { pending = ($0, $1) }
        await repo.load(force: true)
        #expect(repo.proposals.count == 2 && repo.history.count == 6 && repo.settings?.approvalMode == .manual)
        #expect(pending?.0 == 1 && pending?.1 == "1")
        #expect(repo.activeVersion?.version == "v3.3" && repo.groups.pending.count == 1)
        #expect(server.queries["GET /api/v1/coach/proposals"]?.contains("limit=100") == true)
        #expect(repo.proposalsFetchedAt != nil && !repo.isOffline && repo.failure == nil)
    }

    @Test func offlineKeepsTheCachedCopy() async {
        let server = Server()
        let repo = makeRepo(server)
        await repo.load(force: true)
        repo.transportOverride = { _, _, _, _ in throw APIError.transport(URLError(.notConnectedToInternet)) }
        await repo.refreshAll()
        #expect(repo.isOffline && repo.proposals.count == 2 && repo.history.count == 6)
        #expect(repo.failure != nil)
    }

    @Test func freshCacheIsNotRefetched() async {
        let server = Server()
        let repo = makeRepo(server)
        await repo.load(generation: 1)
        let calls = server.calls.count
        await repo.load(generation: 1)
        #expect(server.calls.count == calls)
        await repo.load(generation: 2)  // a sync happened since
        #expect(server.calls.count > calls)
    }

    @Test func approveSendsOnlyTheChosenIdsThenReplans() async throws {
        let server = Server()
        let repo = makeRepo(server)
        var replanned = 0
        repo.afterPlanChange = { replanned += 1 }
        let result = try await repo.approve("abc", changeIds: ["ch1", "ch3"])
        #expect(result.status == .applied && result.appliedConfigVersion == "v3.3" && result.id == "2")
        #expect(server.bodies["POST /api/v1/coach/proposals/abc/approve"] == #"{"change_ids":["ch1","ch3"]}"#)
        #expect(replanned == 1)
        #expect(server.calls.contains("GET /api/v1/coach/history"))  // refreshed afterwards
    }

    @Test func askMapsRateLimitAndOffline() async throws {
        let server = Server()
        let repo = makeRepo(server)
        let queued = try await repo.ask(scope: .lift, refId: "127", question: "  Why am I stalling?  ")
        #expect(queued.status == .queued && queued.id == "1" && repo.detail("1") != nil)
        #expect(server.bodies["POST /api/v1/coach/ask"] == #"{"question":"Why am I stalling?","ref_id":"127","scope":"lift"}"#)

        server.failure = .http(429, #"{"detail":"Weekly limit reached (10/10)"}"#)
        await #expect(throws: CoachError.rateLimited("Weekly limit reached (10/10)")) {
            _ = try await repo.ask(scope: .general, refId: nil, question: "again")
        }
        server.failure = .transport(URLError(.notConnectedToInternet))
        await #expect(throws: CoachError.needsConnection) { _ = try await repo.ask(scope: .general, refId: nil, question: "q") }
    }

    @Test func writesNeedAConfiguredBackendAndNeverQueue() async {
        let server = Server()
        let repo = makeRepo(server, configured: false)
        #expect(!repo.canWrite && repo.writeBlockedHint == "Connect your backend in Settings")
        await #expect(throws: CoachError.notConfigured) { try await repo.rollback(to: "v3.1") }
        await #expect(throws: CoachError.notConfigured) { try await repo.setApprovalMode(.auto) }
        #expect(server.calls.isEmpty)
    }

    @Test func rollbackAndModeChangeHitTheirEndpoints() async throws {
        let server = Server()
        let repo = makeRepo(server)
        var replanned = 0
        repo.afterPlanChange = { replanned += 1 }
        try await repo.rollback(to: "v3.1")
        #expect(server.calls.contains("POST /api/v1/config/rollback/v3.1") && replanned == 1)
        try await repo.setApprovalMode(.autoLowRisk)
        #expect(server.bodies["PUT /api/v1/coach/settings"] == #"{"approval_mode":"auto-low-risk"}"#)
        #expect(repo.settings?.approvalMode == .autoLowRisk)
    }

    @Test func errorMessagesComeFromTheServerDetail() {
        #expect(CoachError.map(APIError.http(409, #"{"detail":"Already decided"}"#)) == .failed("Already decided"))
        #expect(CoachError.map(APIError.http(500, nil)) == .failed("Server error 500"))
        #expect(CoachError.map(APIError.http(429, "nope")) == .rateLimited("The coach is rate limited right now. Try again later."))
        #expect(CoachError.map(APIError.transport(URLError(.timedOut))) == .needsConnection)
        #expect(CoachError.detail(#"{"detail":[{"msg":"field required"}]}"#) == "field required")
    }

    @Test func switchingBackendDropsTheCache() async {
        let server = Server()
        let repo = makeRepo(server)
        await repo.load(force: true)
        repo.reset()
        #expect(repo.proposals.isEmpty && repo.history.isEmpty && repo.settings == nil)
    }
}

// MARK: - Banner cache

@MainActor
struct CoachBannerCacheTests {
    @Test func planCacheStoresAndBackendResetClearsTheBanner() throws {
        let ctx = try makeContext()
        var today = try Fixture.decode(PlanTodayDTO.self, "plan_today")
        today.coach = CoachSummaryDTO(pendingCount: 2, latestPendingId: "p9")
        PlanCache.store(today: today, in: ctx)
        let state = SyncState.current(in: ctx)
        #expect(state.coachPendingCount == 2 && state.coachLatestPendingId == "p9")
        #expect(CoachLogic.bannerVisible(pendingCount: state.coachPendingCount))

        today.coach = nil  // proposal decided / older backend
        PlanCache.store(today: today, in: ctx)
        #expect(state.coachPendingCount == 0 && !CoachLogic.bannerVisible(pendingCount: state.coachPendingCount))

        state.coachPendingCount = 3
        state.coachLatestPendingId = "x"
        BackendReset.reset(in: ctx, fingerprint: "other")
        #expect(state.coachPendingCount == 0 && state.coachLatestPendingId == nil)
    }
}

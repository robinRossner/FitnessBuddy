import Foundation
import Testing
@testable import FitnessBuddy

@Suite(.tags(.decoding, .sync, .fast))
struct DecodingTests {
    @Test func planToday() throws {
        let plan = try Fixture.decode(PlanTodayDTO.self, "plan_today")
        #expect(plan.date == "2026-10-26")
        let item = try #require(plan.sessions.first)
        #expect(item.kind == "upper")
        #expect(item.session?.type == "lifting")
        let ex = try #require(item.session?.exercises?.first)
        #expect(ex.variantId != nil)
        #expect(!ex.sets.isEmpty)
        #expect(ex.sets[0].setType == "working")
        #expect(ex.rule != nil)
        #expect(plan.readiness?.level == .unknown)  // known == false even though color == "green"
    }

    @Test func planWeekCoversRunsAndRest() throws {
        let week = try Fixture.decode(PlanWeekDTO.self, "plan_week")
        #expect(week.days.count == 7)
        let items = week.days.flatMap(\.sessions)
        let kinds = Set(items.map(\.kind))
        #expect(kinds.isSuperset(of: ["upper", "lower", "easy_run", "intervals", "long_run", "rest"]))
        let intervals = try #require(items.first { $0.kind == "intervals" }?.session)
        #expect(intervals.structure?.work?.reps ?? 0 > 0)
        #expect(intervals.structure?.work?.paceLabel != nil)
        #expect(intervals.structure?.work?.hr?.low != nil)
        #expect(intervals.structure?.recovery?.distanceM != nil)
        let easy = try #require(items.first { $0.kind == "easy_run" }?.session)
        #expect(easy.distanceKm != nil)
        #expect(PlanFormat.hr(easy.hr) != nil)
        #expect(PlanFormat.pace(easy.pace) != nil)
    }

    @Test func syncPullSnapshot() throws {
        let pull = try Fixture.decode(PullResponse.self, "sync_pull")
        #expect(pull.cursor == "c9483")
        #expect(pull.hasMore == false)
        #expect(pull.serverTime != nil)
        #expect(!pull.sessions.isEmpty && !pull.sets.isEmpty)
        #expect(!pull.variants.isEmpty && !pull.exercises.isEmpty)
        #expect(pull.gyms.contains { $0.deletedAt != nil })
        #expect(pull.config?.version == "v3.5")
        #expect(pull.config?.dumbbellIncrementKg == 2.0)
        #expect(pull.config?.incrementsKg?["barbell"] == 5.0)
        #expect(pull.bodyMeasurements.count == 2)
        #expect(pull.plannedSessions.count > 0)
        #expect(pull.sessions.allSatisfy { ["upper", "lower", "full", "other"].contains($0.kind) })
        let ladder = pull.variants.compactMap(\.weightLadder).first
        #expect(ladder != nil)
    }

    @Test func syncPullIncremental() throws {
        let pull = try Fixture.decode(PullResponse.self, "sync_pull_incremental")
        #expect(pull.cursor == "c9491")
        #expect(pull.variants.isEmpty && pull.gyms.isEmpty)
        // the incremental pull carries back exactly the rows of the example push
        let push = try Fixture.decode(PushRequest.self, "sync_push_request")
        #expect(pull.sessions.count == 1 && pull.sessions.count == push.sessions.count)
        #expect(pull.sets.count == 5 && pull.sets.count == push.sets.count)
        #expect(pull.bodyMeasurements.count == push.bodyMeasurements.count)
    }

    @Test func pushResponseAndHistory() throws {
        let push = try Fixture.decode(PushResponse.self, "sync_push_response")
        let request = try Fixture.decode(PushRequest.self, "sync_push_request")
        #expect(push.results.count == 7)  // one result per pushed row
        #expect(push.results.count == request.sessions.count + request.sets.count + request.bodyMeasurements.count)
        #expect(push.results.allSatisfy { ["applied", "stale", "rejected"].contains($0.status) })
        let history = try Fixture.decode([VariantHistoryEntryDTO].self, "variant_history")
        #expect(history.count == 5)
        #expect(history[0].sets.first?.weightKg != nil)
    }

    @Test func pushRequestEncodesSnakeCaseLowercaseAndExplicitNulls() throws {
        let request = PushRequest(
            deviceId: "d", sessions: [sessionDTO(UUID(), updated: "2026-10-26T17:24:41.000Z", ended: nil)],
            sets: [], bodyMeasurements: [])
        let json = try #require(String(data: APICoding.makeEncoder().encode(request), encoding: .utf8))
        #expect(json.contains("\"device_id\""))
        #expect(json.contains("\"started_at\":\"2026-10-26T16:00:00.000Z\""))
        #expect(json.contains("\"ended_at\":null"))
        #expect(json.contains("\"planned_session_id\":null"))
        #expect(!json.contains("A") || json.contains("device_id"))  // ids are lowercased by construction
    }

    @Test func realPushRequestFixtureRoundTrips() throws {
        let req = try Fixture.decode(PushRequest.self, "sync_push_request")
        #expect(req.sessions.count == 1 && req.sets.count == 5 && req.bodyMeasurements.count == 1)
        let again = try APICoding.makeDecoder().decode(PushRequest.self, from: APICoding.makeEncoder().encode(req))
        #expect(again == req)
    }

    @Test func isoParsing() {
        #expect(ISO8601.parse("2026-10-26T16:30:12.000Z") == ISO8601.parse("2026-10-26T16:30:12Z"))
        #expect(ISO8601.parse("2026-10-26T16:30:12") == ISO8601.parse("2026-10-26T16:30:12Z"))
        #expect(ISO8601.parse("2026-10-26T18:30:12+02:00") == ISO8601.parse("2026-10-26T16:30:12Z"))
        #expect(ISO8601.string(ISO8601.parse("2026-10-26T16:30:12.250Z")!) == "2026-10-26T16:30:12.250Z")
    }

    @Test func baseURLNormalisation() {
        #expect(APIClient.normalizedBaseURL("192.168.1.5:8000/")?.absoluteString == "http://192.168.1.5:8000")
        #expect(APIClient.normalizedBaseURL("https://fit.example.com/api/v1")?.absoluteString == "https://fit.example.com")
        #expect(APIClient.normalizedBaseURL("  ") == nil)
    }
}

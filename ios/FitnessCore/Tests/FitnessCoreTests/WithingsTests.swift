import Foundation
import Testing
@testable import FitnessCore

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

@Suite(.tags(.health, .decoding, .fast))
struct WithingsDecodingTests {
    @Test func connectedStatus() throws {
        let s = try decode(WithingsStatusDTO.self, """
        {"configured":true,"connected":true,"last_sync_at":"2026-11-04T08:41:00Z","last_error":null,
         "latest_measured_at":"2026-11-04T06:12:00+00:00","latest_weight_kg":82.4}
        """)
        #expect(s.configured && s.connected)
        #expect(s.lastSyncAt == Date(timeIntervalSince1970: 1_793_781_660))
        #expect(s.lastError == nil)
        #expect(s.latestWeightKg == 82.4)
        #expect(s.latestMeasuredAt == Date(timeIntervalSince1970: 1_793_772_720))
    }

    @Test func notConfiguredAndNullsDecode() throws {
        let s = try decode(WithingsStatusDTO.self, """
        {"configured":false,"connected":false,"last_sync_at":null,"last_error":null,"latest_measured_at":null,"latest_weight_kg":null}
        """)
        #expect(s == WithingsStatusDTO(configured: false, connected: false))
        // naive timestamps (no zone) decode too
        let naive = try decode(WithingsStatusDTO.self, #"{"configured":true,"connected":true,"last_sync_at":"2026-11-04T08:41:00"}"#)
        #expect(naive.lastSyncAt != nil)
        #expect(naive.latestWeightKg == nil)
    }

    @Test func connectAnswer() throws {
        #expect(try decode(WithingsConnectDTO.self, #"{"authorize_url":"https://account.withings.com/oauth2_user/authorize2?x=1"}"#)
            .authorizeUrl == "https://account.withings.com/oauth2_user/authorize2?x=1")
    }

    @Test func syncResults() throws {
        let r = try decode(WithingsSyncResultDTO.self,
            #"{"status":"synced","new_measurements":2,"latest_measured_at":"2026-11-04T06:12:00Z","latest_weight_kg":82.4}"#)
        #expect(r.status == .synced)
        #expect(r.newMeasurements == 2)
        #expect(r.latestWeightKg == 82.4)
        #expect(try decode(WithingsSyncResultDTO.self, #"{"status":"busy","new_measurements":0}"#).status == .busy)
        #expect(try decode(WithingsSyncResultDTO.self, #"{"status":"not_connected","new_measurements":0}"#).status == .notConnected)
        #expect(try decode(WithingsSyncResultDTO.self, #"{"status":"maintenance"}"#).status == .unknown("maintenance"))
    }
}

@Suite(.tags(.health, .fast))
struct WithingsCallbackTests {
    private func parse(_ s: String) -> WithingsCallback? { URL(string: s).flatMap(WithingsCallback.parse) }

    @Test func connected() {
        #expect(parse("fitnessbuddy://withings?result=connected") == .connected)
    }

    @Test func errorReasons() {
        #expect(parse("fitnessbuddy://withings?result=error&reason=denied") == .failed(.denied))
        #expect(parse("fitnessbuddy://withings?result=error&reason=invalid_state") == .failed(.invalidState))
        #expect(parse("fitnessbuddy://withings?result=error&reason=exchange_failed") == .failed(.exchangeFailed))
        #expect(parse("fitnessbuddy://withings?result=error&reason=weird") == .failed(.other("weird")))
        #expect(parse("fitnessbuddy://withings?result=error") == .failed(.other(nil)))
        #expect(WithingsCallback.failed(.denied).message == "Withings access was not allowed.")
        #expect(WithingsCallback.connected.message == nil)
    }

    @Test func otherURLsAreNotCallbacks() {
        #expect(parse("fitnessbuddy://other?result=connected") == nil)
        #expect(parse("https://withings?result=connected") == nil)
        #expect(parse("fitnessbuddy://withings") == nil)
        #expect(parse("fitnessbuddy://withings?result=maybe") == nil)
    }
}

@Suite(.tags(.health, .fast))
struct WithingsLogicTests {
    private let cal = Calendar.current
    private func at(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }
    private var now: Date { at(4, 9, 41) }

    @Test func whenLabels() {
        #expect(WithingsLogic.whenLabel(at(4, 7, 12), now: now) == "today 07:12")
        #expect(WithingsLogic.whenLabel(at(3, 18, 5), now: now) == "yesterday 18:05")
        #expect(WithingsLogic.whenLabel(at(2, 6, 0), now: now) == "Mon 2 Nov 06:00")
    }

    @Test func latestWeighIn() {
        #expect(WithingsLogic.latestWeighIn(weightKg: 82.4, at: at(4, 7, 12), now: now) == "82.4 kg, today 07:12")
        #expect(WithingsLogic.latestWeighIn(weightKg: 82.0, at: nil, now: now) == "82.0 kg")
        #expect(WithingsLogic.latestWeighIn(weightKg: nil, at: at(4, 7, 12), now: now) == "today 07:12")
        #expect(WithingsLogic.latestWeighIn(weightKg: nil, at: nil, now: now) == nil)
    }

    @Test func relativeTimes() {
        #expect(WithingsLogic.relative(at(4, 9, 41), now: now) == "just now")
        #expect(WithingsLogic.relative(at(4, 9, 36), now: now) == "5 minutes ago")
        #expect(WithingsLogic.relative(at(4, 8, 41), now: now) == "1 hour ago")
    }

    @Test func statusLines() {
        #expect(WithingsLogic.statusLines(.init(configured: false, connected: false), now: now)
            == .init(headline: "Not set up on server"))
        #expect(WithingsLogic.statusLines(.init(configured: true, connected: false), now: now)
            == .init(headline: "Not connected"))
        let connected = WithingsStatusDTO(configured: true, connected: true, lastSyncAt: at(4, 8, 41),
                                          latestMeasuredAt: at(4, 7, 12), latestWeightKg: 82.4)
        #expect(WithingsLogic.statusLines(connected, now: now)
            == .init(headline: "Connected", lastSync: "1 hour ago", latestWeighIn: "82.4 kg, today 07:12"))
        let fresh = WithingsStatusDTO(configured: true, connected: true)
        #expect(WithingsLogic.statusLines(fresh, now: now) == .init(headline: "Connected", lastSync: "not yet", latestWeighIn: nil))
    }

    @Test func syncFeedback() {
        func fb(_ r: WithingsSyncResultDTO) -> WithingsLogic.Feedback { WithingsLogic.feedback(for: r, now: now) }
        #expect(fb(.init(status: .synced, newMeasurements: 1, latestMeasuredAt: at(4, 7, 12), latestWeightKg: 82.4))
            == .init(tone: .success, text: "1 new weigh-in. Latest: 82.4 kg, today 07:12."))
        #expect(fb(.init(status: .synced, newMeasurements: 3)) == .init(tone: .success, text: "3 new weigh-ins."))
        #expect(fb(.init(status: .synced, newMeasurements: 0)) == .init(tone: .neutral, text: "No new weigh-ins."))
        #expect(fb(.init(status: .busy)).tone == .neutral)
        #expect(fb(.init(status: .notConnected)).tone == .warning)
        #expect(fb(.init(status: .unknown("x"))).tone == .warning)
    }

    @Test func syncButtonNoteOnlyForNewsAndTransientTrouble() {
        #expect(WithingsLogic.syncButtonNote(for: .init(status: .synced, newMeasurements: 3), now: now) == "3 new weigh-ins.")
        #expect(WithingsLogic.syncButtonNote(for: .init(status: .synced, newMeasurements: 0), now: now) == nil)
        #expect(WithingsLogic.syncButtonNote(for: .init(status: .notConnected), now: now) == nil)
        #expect(WithingsLogic.syncButtonNote(for: .init(status: .busy), now: now) == nil)
        #expect(WithingsLogic.syncButtonNote(for: APIError.http(429, nil))?.contains("limiting") == true)
        #expect(WithingsLogic.syncButtonNote(for: APIError.http(503, nil)) == nil)
        #expect(WithingsLogic.syncButtonNote(for: APIError.http(409, nil)) == nil)
    }

    @Test func errorMessages() {
        #expect(WithingsLogic.message(for: APIError.http(429, nil)).contains("limiting"))
        #expect(WithingsLogic.message(for: APIError.http(503, nil)) == "Withings isn't set up on the server.")
        #expect(WithingsLogic.message(for: APIError.http(502, nil)).contains("didn't answer"))
        #expect(WithingsLogic.message(for: APIError.unauthorized) == APIError.message(for: APIError.unauthorized))
    }
}

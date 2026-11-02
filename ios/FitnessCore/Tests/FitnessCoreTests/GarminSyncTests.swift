import Foundation
import Testing
@testable import FitnessCore

private func decode(_ json: String) throws -> GarminSyncResultDTO {
    try APICoding.makeDecoder().decode(GarminSyncResultDTO.self, from: Data(json.utf8))
}

@Suite(.tags(.health, .decoding, .fast))
struct GarminSyncDecodingTests {
    @Test func syncedWithActivity() throws {
        let r = try decode("""
        {"status":"synced","retry_after_s":12,"new_activities":1,
         "latest_activity":{"title":"Morning Run","activity_type":"running","started_at_local":"2026-11-01T07:12:03"},
         "hrv_today":true,"manual_syncs_today":3,"manual_syncs_limit":30}
        """)
        #expect(r.status == .synced)
        #expect(r.retryAfterS == 12)
        #expect(r.newActivities == 1)
        #expect(r.latestActivity == .init(title: "Morning Run", activityType: "running", startedAtLocal: "2026-11-01T07:12:03"))
        #expect(r.hrvToday == true)
        #expect(r.manualSyncsToday == 3)
        #expect(r.manualSyncsLimit == 30)
    }

    @Test func cooldownBusyAndLimitDecode() throws {
        #expect(try decode(#"{"status":"cooldown","retry_after_s":20,"new_activities":0}"#).status == .cooldown)
        #expect(try decode(#"{"status":"busy","retry_after_s":null}"#).status == .busy)
        let limit = try decode(#"{"status":"limit_reached","manual_syncs_today":30,"manual_syncs_limit":30}"#)
        #expect(limit.status == .limitReached)
        #expect(limit.retryAfterS == nil)
    }

    @Test func nullsAndMissingFieldsDecode() throws {
        let r = try decode("""
        {"status":"synced","retry_after_s":null,"new_activities":0,
         "latest_activity":{"title":null,"activity_type":null,"started_at_local":null},
         "hrv_today":null,"manual_syncs_today":null,"manual_syncs_limit":null}
        """)
        #expect(r.retryAfterS == nil)
        #expect(r.latestActivity == .init(title: nil, activityType: nil, startedAtLocal: nil))
        #expect(r.hrvToday == nil)
        let bare = try decode(#"{"status":"synced","latest_activity":null}"#)
        #expect(bare.latestActivity == nil)
        #expect(bare.newActivities == nil)
    }

    @Test func unknownStatusDoesNotBreakDecoding() throws {
        #expect(try decode(#"{"status":"maintenance"}"#).status == .unknown("maintenance"))
    }
}

@Suite(.tags(.health, .fast))
struct GarminSyncFeedbackTests {
    private let today = "2026-11-01"

    private func feedback(_ json: String) throws -> GarminSyncFeedback {
        GarminSyncLogic.feedback(for: try decode(json), today: today)
    }

    @Test func newActivityIsNamed() throws {
        let f = try feedback("""
        {"status":"synced","new_activities":1,"latest_activity":{"title":"Morning Run","started_at_local":"2026-11-01T07:12:03"}}
        """)
        #expect(f.title == "Morning Run synced")
        #expect(f.tone == .success)
        #expect(!f.offersConnect)
    }

    @Test func severalNewActivitiesAreCounted() throws {
        let f = try feedback(#"{"status":"synced","new_activities":2,"latest_activity":{"title":"Evening Run"}}"#)
        #expect(f.title == "2 new activities synced")
        #expect(f.detail == "Latest: Evening Run")
        #expect(try feedback(#"{"status":"synced","new_activities":1,"latest_activity":null}"#).title == "New activity synced")
    }

    @Test func nothingNewOffersConnectAndSaysWhenTheLatestWas() throws {
        let f = try feedback("""
        {"status":"synced","new_activities":0,"latest_activity":{"title":"Morning Run","started_at_local":"2026-11-01T07:12:03"}}
        """)
        #expect(f.title == "No new activity at Garmin yet")
        #expect(f.offersConnect)
        #expect(f.detail?.contains("Latest at Garmin: Morning Run, today at 07:12.") == true)
        #expect(f.detail?.contains("usually makes the watch upload") == true)
        // never claims the watch was synced
        #expect(f.title.lowercased().contains("watch") == false)

        let none = try feedback(#"{"status":"synced","new_activities":0,"latest_activity":null}"#)
        #expect(none.offersConnect)
        #expect(none.detail == "Opening Garmin Connect usually makes the watch upload.")
    }

    @Test func whenLabels() {
        #expect(GarminSyncLogic.whenLabel("2026-11-01T07:12:03", today: today) == "today at 07:12")
        #expect(GarminSyncLogic.whenLabel("2026-10-31T18:05:00", today: today) == "yesterday at 18:05")
        #expect(GarminSyncLogic.whenLabel("2026-10-26T06:00:00", today: today)?.hasSuffix(" at 06:00") == true)
        #expect(GarminSyncLogic.whenLabel(nil, today: today) == nil)
        #expect(GarminSyncLogic.whenLabel("garbage", today: today) == nil)
    }

    @Test func cooldownAndBusyShowTheWait() throws {
        #expect(try feedback(#"{"status":"cooldown","retry_after_s":12}"#).title == "Just synced, try again in 12s")
        #expect(try feedback(#"{"status":"busy","retry_after_s":5}"#).title == "Just synced, try again in 5s")
        #expect(try feedback(#"{"status":"busy","retry_after_s":null}"#).title == "Just synced, try again shortly")
        #expect(try feedback(#"{"status":"cooldown","retry_after_s":0}"#).title == "Just synced, try again in 1s")
    }

    @Test func limitUsesTheServersLimit() throws {
        #expect(try feedback(#"{"status":"limit_reached","manual_syncs_today":30,"manual_syncs_limit":30}"#).title
                == "Daily sync limit reached (30)")
        #expect(try feedback(#"{"status":"limit_reached","manual_syncs_limit":7}"#).title == "Daily sync limit reached (7)")
        #expect(try feedback(#"{"status":"limit_reached"}"#).title == "Daily sync limit reached")
    }

    @Test func errorsAreShortAndPlain() {
        #expect(GarminSyncLogic.message(for: APIError.http(429, nil)).hasPrefix("Garmin is limiting requests"))
        #expect(GarminSyncLogic.message(for: APIError.http(503, "{}")) == "Garmin isn't available on the server.")
        #expect(GarminSyncLogic.message(for: APIError.http(502, nil)) == "Garmin didn't answer. Try again later.")
        #expect(GarminSyncLogic.message(for: APIError.unauthorized) == APIError.unauthorized.errorDescription)
        #expect(GarminSyncLogic.feedback(for: APIError.http(502, nil)).tone == .warning)
    }

}

import Foundation
import Testing
@testable import FitnessBuddy

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

    @Test func connectLinkKeepsSchemeThenAppStore() {
        #expect(GarminConnectLink.urls.map(\.absoluteString) == [
            "gcm-ciq://", "https://apps.apple.com/app/garmin-connect/id583446403",
        ])
    }
}

@MainActor
private final class Fake {
    var responses: [Result<GarminSyncResultDTO, Error>] = []
    var calls: [String] = []
    var now = Date(timeIntervalSince1970: 1_000_000)
    var sleeps: [TimeInterval] = []
    var blockSleep = false
    var connectOpens = true
    var demo = false

    func controller() -> GarminSyncController {
        GarminSyncController(hooks: .init(
            request: { [self] in
                calls.append("request")
                return try responses.isEmpty ? syncedEmpty() : responses.removeFirst().get()
            },
            isDemo: { [self] in demo },
            syncApp: { [self] in calls.append("syncApp") },
            refresh: { [self] _ in calls.append("refresh") },
            openConnect: { [self] in calls.append("openConnect"); return connectOpens },
            sleep: { [self] s in
                sleeps.append(s)
                if blockSleep { try await Task.sleep(for: .seconds(3600)) }
            },
            now: { [self] in now }))
    }

    func syncedEmpty(retryAfter: Int? = nil) -> GarminSyncResultDTO {
        .init(status: .synced, retryAfterS: retryAfter, newActivities: 0, latestActivity: nil, hrvToday: nil,
              manualSyncsToday: nil, manualSyncsLimit: nil)
    }

    func yield() async { for _ in 0..<20 { await Task.yield() } }
}

@MainActor
@Suite(.tags(.health, .fast))
struct GarminSyncControllerTests {
    @Test func tapCallsBackendThenAppSyncThenRefresh() async {
        let fake = Fake()
        let c = fake.controller()
        await c.sync()
        #expect(fake.calls == ["request", "syncApp", "refresh"])
        #expect(c.feedback?.title == "No new activity at Garmin yet")
        #expect(!c.isRunning)
    }

    @Test func appSyncStillRunsOnCooldownBusyLimitAndFailure() async {
        let fake = Fake()
        let c = fake.controller()
        for status in [GarminSyncResultDTO.Status.cooldown, .busy, .limitReached] {
            fake.calls = []
            fake.responses = [.success(.init(status: status, retryAfterS: 9, newActivities: nil, latestActivity: nil,
                                             hrvToday: nil, manualSyncsToday: 30, manualSyncsLimit: 30))]
            await c.sync()
            #expect(fake.calls == ["request", "syncApp", "refresh"])
        }
        fake.calls = []
        fake.responses = [.failure(APIError.http(502, nil))]
        await c.sync()
        #expect(fake.calls == ["request", "syncApp", "refresh"])
        #expect(c.feedback?.title == "Garmin didn't answer. Try again later.")
    }

    @Test func demoAndNotConfiguredSkipTheAppSync() async {
        let fake = Fake()
        let c = fake.controller()
        fake.demo = true
        await c.sync()
        #expect(fake.calls.isEmpty)
        #expect(c.feedback?.title == "Not available with demo data.")
        fake.demo = false
        fake.responses = [.failure(APIError.notConfigured)]
        await c.sync()
        #expect(fake.calls == ["request"])
        #expect(c.feedback?.title == APIError.notConfigured.errorDescription)
    }

    @Test func dismissOnlyClosesTheMatchingBanner() async {
        let fake = Fake()
        let c = fake.controller()
        await c.sync()
        let old = c.feedbackId
        await c.sync()
        c.dismissFeedback(id: old)
        #expect(c.feedback != nil)
        c.dismissFeedback(id: c.feedbackId)
        #expect(c.feedback == nil)
    }

    // MARK: Return from Garmin Connect

    @Test func returningFromConnectSyncsOnceMore() async {
        let fake = Fake()
        let c = fake.controller()
        await c.sync()
        fake.calls = []
        await c.openGarminConnect()
        c.appDidEnterBackground()
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls == ["openConnect", "request", "syncApp", "refresh"])
        // one-shot: coming back again does nothing
        fake.calls = []
        c.appDidEnterBackground()
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls.isEmpty)
    }

    @Test func becomingActiveWithoutHavingLeftDoesNothing() async {
        let fake = Fake()
        let c = fake.controller()
        await c.openGarminConnect()  // e.g. the "Open in Garmin Connect?" alert was dismissed
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls == ["openConnect"])
    }

    @Test func nothingHappensWithoutOpeningConnectOrWhenItDidNotOpen() async {
        let fake = Fake()
        let c = fake.controller()
        c.appDidEnterBackground()
        c.appDidBecomeActive()
        fake.connectOpens = false
        await c.openGarminConnect()
        c.appDidEnterBackground()
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls == ["openConnect"])
    }

    @Test func staleReturnIsIgnored() async {
        let fake = Fake()
        let c = fake.controller()
        await c.openGarminConnect()
        c.appDidEnterBackground()
        fake.now.addTimeInterval(GarminSyncController.returnWindow + 1)
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls == ["openConnect"])
    }

    @Test func waitsOutTheRemainingRetryAfterBeforeResyncing() async {
        let fake = Fake()
        fake.responses = [.success(fake.syncedEmpty(retryAfter: 30))]
        let c = fake.controller()
        await c.sync()
        await c.openGarminConnect()
        c.appDidEnterBackground()
        fake.now.addTimeInterval(10)  // 10 s of the 30 s cooldown are over
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.sleeps == [20])
        #expect(fake.calls.filter { $0 == "request" }.count == 2)
    }

    @Test func noWaitWhenTheCooldownIsAlreadyOver() async {
        let fake = Fake()
        fake.responses = [.success(fake.syncedEmpty(retryAfter: 30))]
        let c = fake.controller()
        await c.sync()
        await c.openGarminConnect()
        c.appDidEnterBackground()
        fake.now.addTimeInterval(45)
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.sleeps.isEmpty)
        #expect(fake.calls.filter { $0 == "request" }.count == 2)
    }

    @Test func manualTapDuringTheWaitCancelsTheAutomaticSync() async {
        let fake = Fake()
        fake.responses = [.success(fake.syncedEmpty(retryAfter: 30))]
        let c = fake.controller()
        await c.sync()
        await c.openGarminConnect()
        c.appDidEnterBackground()
        fake.blockSleep = true
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.sleeps.count == 1)
        #expect(fake.calls.filter { $0 == "request" }.count == 1)  // still waiting
        await c.sync()  // manual tap wins
        await fake.yield()
        #expect(fake.calls.filter { $0 == "request" }.count == 2)
        // and the cancelled automatic sync never fires later
        c.appDidEnterBackground()
        c.appDidBecomeActive()
        await fake.yield()
        #expect(fake.calls.filter { $0 == "request" }.count == 2)
    }
}

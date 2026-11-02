import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy
@testable import FitnessCore

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

/// Garmin Connect deep link (UIKit).
@Suite(.tags(.health, .fast))
struct GarminConnectLinkTests {
    @Test func connectLinkKeepsSchemeThenAppStore() {
        #expect(GarminConnectLink.urls.map(\.absoluteString) == [
            "gcm-ciq://", "https://apps.apple.com/app/garmin-connect/id583446403",
        ])
    }
}

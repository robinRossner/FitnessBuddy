import Foundation
import Testing
@testable import FitnessBuddy
@testable import FitnessCore

@MainActor
private final class Fake {
    var calls: [String] = []
    var status = WithingsStatusDTO(configured: true, connected: false)
    var syncResult: Result<WithingsSyncResultDTO, Error> = .success(.init(status: .synced, newMeasurements: 0))
    var demo = false
    let now = Date(timeIntervalSince1970: 1_800_000_000)

    func controller() -> WithingsController {
        WithingsController(hooks: .init(
            status: { [self] in calls.append("status"); return status },
            connect: { [self] in calls.append("connect"); return URL(string: "https://account.withings.com/authorize")! },
            sync: { [self] in calls.append("sync"); return try syncResult.get() },
            disconnect: { [self] in calls.append("disconnect"); status = .init(configured: true, connected: false) },
            isDemo: { [self] in demo },
            syncApp: { [self] in calls.append("syncApp") },
            refreshStats: { [self] in calls.append("refreshStats") },
            now: { [self] in now }))
    }
}

@MainActor
@Suite(.tags(.health, .fast))
struct WithingsControllerTests {
    @Test func connectedCallbackImportsAndSyncsTheApp() async {
        let fake = Fake()
        let c = fake.controller()
        fake.syncResult = .success(.init(status: .synced, newMeasurements: 2, latestMeasuredAt: fake.now, latestWeightKg: 82.4))
        await c.connect { url in
            #expect(url.host == "account.withings.com")
            fake.status = .init(configured: true, connected: true)
            return URL(string: "fitnessbuddy://withings?result=connected")!
        }
        #expect(fake.calls == ["connect", "status", "sync", "syncApp", "refreshStats", "status"])
        #expect(c.feedback?.tone == .success)
        #expect(c.feedback?.text.hasPrefix("Connected. 2 new weigh-ins") == true)
        #expect(c.activity == nil)
    }

    @Test func errorCallbackShowsTheReasonAndSyncsNothing() async {
        let fake = Fake()
        let c = fake.controller()
        await c.connect { _ in URL(string: "fitnessbuddy://withings?result=error&reason=denied")! }
        #expect(fake.calls == ["connect"])
        #expect(c.feedback == .init(tone: .warning, text: "Withings access was not allowed."))
    }

    @Test func cancelledSignInIsSilent() async {
        let fake = Fake()
        let c = fake.controller()
        await c.connect { _ in throw CancellationError() }
        #expect(c.feedback == nil || c.feedback?.tone == .warning)
    }

    @Test func syncNowOnlySyncsTheAppWhenSomethingCame() async {
        let fake = Fake()
        let c = fake.controller()
        await c.syncNow()
        #expect(fake.calls == ["sync", "status"])
        #expect(c.feedback?.text == "No new weigh-ins.")
        fake.calls = []
        fake.syncResult = .success(.init(status: .synced, newMeasurements: 1))
        await c.syncNow()
        #expect(fake.calls == ["sync", "syncApp", "refreshStats", "status"])
    }

    @Test func syncFailureIsShownNotThrown() async {
        let fake = Fake()
        let c = fake.controller()
        fake.syncResult = .failure(APIError.http(502, nil))
        await c.syncNow()
        #expect(c.feedback?.tone == .warning)
        #expect(c.feedback?.text.contains("didn't answer") == true)
    }

    @Test func disconnectRefreshesTheStatus() async {
        let fake = Fake()
        fake.status = .init(configured: true, connected: true)
        let c = fake.controller()
        await c.disconnect()
        #expect(fake.calls == ["disconnect", "status"])
        #expect(c.status?.connected == false)
    }

    @Test func demoDataBlocksActions() async {
        let fake = Fake()
        fake.demo = true
        let c = fake.controller()
        await c.syncNow()
        await c.disconnect()
        #expect(fake.calls.isEmpty)
        #expect(c.feedback?.text == "Not available with demo data.")
    }
}

@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
    try CoachCoding.makeDecoder().decode(T.self, from: Data(json.utf8))
}

// MARK: - Repository

@MainActor
@Suite(.tags(.coach))
struct CoachRepositoryTests {
    private final class Server: @unchecked Sendable {
        var calls: [String] = []
        var bodies: [String: String] = [:]
        var failure: APIError?
        var queries: [String: String] = [:]
        /// `refreshAll` fires several requests at once; the recording below must not race.
        private let lock = NSLock()

        func respond(_ method: String, _ path: String, _ query: [URLQueryItem] = [], _ body: Data?) throws -> Data {
            lock.withLock {
                calls.append(method + " " + path)
                queries[method + " " + path] = query.map { "\($0.name)=\($0.value ?? "")" }.joined(separator: "&")
                if let body { bodies[method + " " + path] = String(decoding: body, as: UTF8.self) }
            }
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
        repo.transportOverride = ClosureTransport { method, path, query, body in try server.respond(method, path, query, body) }
        return repo
    }

    @Test func loadFetchesCachesAndReportsPending() async {
        let server = Server()
        let repo = makeRepo(server)
        var pending: (Int, String?)?
        repo.onPendingChanged = { pending = ($0, $1) }
        await repo.load(force: true)
        #expect(repo.proposals.count == 3 && repo.history.count == 9 && repo.settings?.approvalMode == .manual)
        #expect(pending?.0 == 2 && pending?.1 == "3")
        #expect(repo.activeVersion?.version == "v3.6" && repo.groups.pending.count == 2)
        #expect(server.queries["GET /api/v1/coach/proposals"]?.contains("limit=100") == true)
        #expect(repo.proposalsFetchedAt != nil && !repo.isOffline && repo.failure == nil)
    }

    @Test func offlineKeepsTheCachedCopy() async {
        let server = Server()
        let repo = makeRepo(server)
        await repo.load(force: true)
        repo.transportOverride = ClosureTransport { _, _, _, _ in throw APIError.transport(URLError(.notConnectedToInternet)) }
        await repo.refreshAll()
        #expect(repo.isOffline && repo.proposals.count == 3 && repo.history.count == 9)
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
        #expect(result.status == .applied && result.appliedConfigVersion == "v3.6" && result.id == "2")
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
@Suite(.tags(.coach))
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

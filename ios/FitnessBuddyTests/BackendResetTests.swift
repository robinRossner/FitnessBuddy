import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

@MainActor
private func makeEnv() throws -> AppEnvironment {
    let defaults = UserDefaults(suiteName: "backend-reset-tests")!
    defaults.removePersistentDomain(forName: "backend-reset-tests")
    let settings = SettingsStore(defaults: defaults, loadToken: false)
    return AppEnvironment(container: try Persistence.makeContainer(inMemory: true), settings: settings)
}

@MainActor
@Suite(.tags(.sync))
struct DemoIsolationTests {
    @Test func demoRowsAreNeverInTheOutbox() throws {
        let ctx = try makeContext()
        let s = LocalSession(name: "Demo upper", endedAt: .now)
        s.isDemo = true
        let set = LocalSet(sessionId: s.id, variantId: 1, exerciseOrder: 1, setOrder: 1, weightKg: 50, reps: 5, completedAt: .now)
        set.isDemo = true
        let body = LocalBodyMeasurement(weightKg: 80)
        body.isDemo = true
        ctx.insert(s); ctx.insert(set); ctx.insert(body)
        let real = LocalSession(name: "Real", endedAt: .now)
        ctx.insert(real)
        try ctx.save()

        let batch = try Outbox.collect(in: ctx)
        #expect(batch.sessions.map(\.id) == [real.id.uuidString.lowercased()])
        #expect(batch.sets.isEmpty)
        #expect(batch.bodyMeasurements.isEmpty)
    }

    @Test func loadingDemoDataFlagsRowsAndPausesSync() throws {
        let ctx = try makeContext()
        let real = LocalSession(name: "Real", endedAt: .now)
        ctx.insert(real)
        try ctx.save()

        DemoData.load(into: ctx, settings: nil)

        let sessions = try ctx.fetch(FetchDescriptor<LocalSession>())
        let demo = sessions.filter { $0.isDemo }
        #expect(demo.count == sessions.count - 1)
        #expect(!demo.isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<LocalSet>()).allSatisfy { $0.isDemo })
        #expect(SyncState.current(in: ctx).demoActive)
        // only the real session is pushable
        #expect(try Outbox.collect(in: ctx).sessions.map(\.id) == [real.id.uuidString.lowercased()])
        UserDefaults.standard.removeObject(forKey: AppClock.overrideKey)
    }

    @Test func workoutLoggedInDemoModeIsDemo() throws {
        let ctx = try makeContext()
        SyncState.current(in: ctx).demoActive = true
        let service = WorkoutService(context: ctx)
        let session = service.startEmpty(gymId: nil)
        #expect(session.isDemo)
        session.endedAt = .now
        session.touch()
        try ctx.save()
        #expect(Outbox.pendingCount(in: ctx) == 0)
    }
}

@MainActor
@Suite(.tags(.sync))
struct BackendChangeTests {
    private func seed(_ env: AppEnvironment) throws {
        let ctx = env.context
        ctx.insert(CachedPlannedSession(id: "p1", date: "2026-10-26", kind: "upper", status: "proposed", title: "Upper",
                                        updatedAt: nil, isPreview: false, payload: Data()))
        ctx.insert(CachedGym(id: 1, name: "Gym"))
        ctx.insert(CachedVariant(id: 1, exerciseId: 1, name: "Bench", gymId: 1, equipmentType: "barbell"))
        ctx.insert(CachedExercise(id: 1, name: "Bench"))
        ctx.insert(LocalSession(name: "Synced", updatedAt: date("2026-10-20T10:00:00.000Z"), dirty: false))
        let state = SyncState.current(in: ctx)
        state.cursor = "c123"
        state.lastSyncAt = .now
        state.lastError = "boom"
        state.readinessData = Data("{}".utf8)
        try ctx.save()
    }

    @Test func savingANewBackendWipesCacheAndResetsCursor() async throws {
        let env = try makeEnv()
        await env.saveBackend(url: "https://one.example.com", token: "tok-1")
        try seed(env)
        let state = SyncState.current(in: env.context)
        state.backendFingerprint = env.settings.fingerprint  // as left behind by a finished sync
        state.cursor = "c123"

        // an unsynced real workout must survive
        let mine = LocalSession(name: "Mine", endedAt: .now)
        env.context.insert(mine)
        try env.context.save()
        #expect(env.unsyncedRecordCount == 1)

        await env.saveBackend(url: "https://two.example.com/", token: "tok-1")

        let ctx = env.context
        #expect(SyncState.current(in: ctx).cursor == nil)
        #expect(SyncState.current(in: ctx).lastError == nil)
        #expect(SyncState.current(in: ctx).lastSyncAt == nil)
        #expect(SyncState.current(in: ctx).readinessData == nil)
        #expect(try ctx.fetch(FetchDescriptor<CachedPlannedSession>()).isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<CachedGym>()).isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<CachedVariant>()).isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<CachedExercise>()).isEmpty)
        let sessions = try ctx.fetch(FetchDescriptor<LocalSession>())
        #expect(sessions.map(\.name) == ["Mine"])
        #expect(sessions.first?.dirty == true)
        #expect(SyncState.current(in: ctx).backendFingerprint == env.settings.fingerprint)
        #expect(env.settings.backendURL == "https://two.example.com/")
    }

    @Test func changingOnlyTheTokenWipesToo() async throws {
        let env = try makeEnv()
        await env.saveBackend(url: "https://one.example.com", token: "tok-1")
        try seed(env)
        SyncState.current(in: env.context).backendFingerprint = env.settings.fingerprint
        SyncState.current(in: env.context).cursor = "c123"

        await env.saveBackend(url: "https://one.example.com", token: "tok-2")
        #expect(SyncState.current(in: env.context).cursor == nil)
        #expect(try env.context.fetch(FetchDescriptor<CachedGym>()).isEmpty)
    }

    @Test func resavingTheSameBackendKeepsTheCache() async throws {
        let env = try makeEnv()
        await env.saveBackend(url: "https://one.example.com", token: "tok-1")
        try seed(env)
        SyncState.current(in: env.context).backendFingerprint = env.settings.fingerprint
        SyncState.current(in: env.context).cursor = "c123"

        await env.saveBackend(url: " https://one.example.com/ ", token: "tok-1\n")
        #expect(SyncState.current(in: env.context).cursor == "c123")
        #expect(try env.context.fetch(FetchDescriptor<CachedGym>()).count == 1)
    }

    @Test func savingABackendRemovesDemoData() async throws {
        let env = try makeEnv()
        DemoData.load(into: env.context, settings: env.settings)
        #expect(SyncState.current(in: env.context).demoActive)
        #expect(!(try env.context.fetch(FetchDescriptor<LocalSession>())).isEmpty)

        await env.saveBackend(url: "http://localhost:8000", token: "tok")

        let ctx = env.context
        #expect(!SyncState.current(in: ctx).demoActive)
        #expect(try ctx.fetch(FetchDescriptor<LocalSession>()).isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<LocalSet>()).isEmpty)
        #expect(try ctx.fetch(FetchDescriptor<CachedPlannedSession>()).isEmpty)
        #expect(SyncState.current(in: ctx).cursor == nil)
        #expect(UserDefaults.standard.string(forKey: AppClock.overrideKey) == nil)
        #expect(env.settings.currentGymId == nil)
    }

    @Test func resetLocalDataKeepsUnsyncedWorkouts() async throws {
        let env = try makeEnv()
        await env.saveBackend(url: "https://one.example.com", token: "tok-1")
        try seed(env)
        let mine = LocalSession(name: "Mine", endedAt: .now)
        env.context.insert(mine)
        try env.context.save()

        await env.resetLocalDataAndResync()

        #expect(try env.context.fetch(FetchDescriptor<LocalSession>()).map(\.name) == ["Mine"])
        #expect(SyncState.current(in: env.context).cursor == nil)
        #expect(try env.context.fetch(FetchDescriptor<CachedPlannedSession>()).isEmpty)
    }

    @Test func fingerprintNeedsBothParts() {
        #expect(BackendReset.fingerprint(url: "", token: "x") == nil)
        #expect(BackendReset.fingerprint(url: "https://a.example.com", token: " ") == nil)
        #expect(BackendReset.fingerprint(url: "https://a.example.com/", token: "x")
                == BackendReset.fingerprint(url: " https://a.example.com ", token: "x\n"))
        #expect(BackendReset.fingerprint(url: "https://a.example.com", token: "x")
                != BackendReset.fingerprint(url: "https://a.example.com", token: "y"))
    }
}

@Suite(.tags(.sync, .fast))
struct ErrorMappingTests {
    @Test func unauthorized() {
        #expect(APIError.message(for: APIError.unauthorized) == "Unauthorized: check the token")
    }

    @Test func unreachable() {
        for code in [URLError.Code.notConnectedToInternet, .cannotConnectToHost, .cannotFindHost, .timedOut, .networkConnectionLost] {
            #expect(APIError.message(for: APIError.transport(URLError(code))) == "Can't reach server")
        }
    }

    @Test func tlsAndAts() {
        #expect(APIError.message(for: APIError.transport(URLError(.appTransportSecurityRequiresSecureConnection))).contains("https"))
        #expect(APIError.message(for: APIError.transport(URLError(.serverCertificateUntrusted))).contains("certificate"))
    }

    @Test func httpErrors() {
        #expect(APIError.message(for: APIError.http(404, nil)).contains("404"))
        #expect(APIError.message(for: APIError.http(500, "oops")) == "Server error 500: oops")
    }

    @Test func decodingNamesTheField() {
        let json = Data(#"{"sessions":[{"id":"a","name":5}],"cursor":null}"#.utf8)
        do {
            _ = try APICoding.makeDecoder().decode(PullResponse.self, from: json)
            Issue.record("should not decode")
        } catch {
            let message = APIError.message(for: APIError.decoding(error))
            #expect(message.hasPrefix("Unexpected response (decoding): "))
            #expect(message.count > "Unexpected response (decoding): ".count)
        }
    }

    @Test func decodingPathIsIncluded() {
        struct Row: Decodable { let updatedAt: Date }
        struct Page: Decodable { let rows: [Row] }
        let json = Data(#"{"rows":[{"updated_at":"2026-10-26T10:00:00Z"},{"updated_at":"nonsense"}]}"#.utf8)
        do {
            _ = try APICoding.makeDecoder().decode(Page.self, from: json)
            Issue.record("should not decode")
        } catch {
            let message = APIError.message(for: APIError.decoding(error))
            #expect(message.contains("rows[1].updatedAt") || message.contains("rows[1].updated_at"), Comment(rawValue: message))
        }
    }

    @Test func missingKey() {
        struct Row: Decodable { let cursor: String }
        let error = (try? APICoding.makeDecoder().decode(Row.self, from: Data("{}".utf8))) == nil
        #expect(error)
        do { _ = try APICoding.makeDecoder().decode(Row.self, from: Data("{}".utf8)) }
        catch { #expect(APIError.message(for: APIError.decoding(error)) == "Unexpected response (decoding): cursor missing") }
    }
}

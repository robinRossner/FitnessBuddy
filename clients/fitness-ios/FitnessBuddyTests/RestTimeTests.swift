import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

// MARK: - Formatting, picker values, resolution order

struct RestTimeBasicsTests {
    @Test func formatsMinutesAndSeconds() {
        #expect(RestTime.format(120) == "2:00")
        #expect(RestTime.format(135) == "2:15")
        #expect(RestTime.format(60) == "1:00")
        #expect(RestTime.format(300) == "5:00")
        #expect(RestTime.defaultLabel(default: 120) == "Default (2:00)")
    }

    @Test func pickerOffersOneToFiveMinutesInFifteenSecondSteps() {
        let values = RestTime.pickerValues
        #expect(values.first == 60 && values.last == 300)
        #expect(values.count == 17)
        #expect(zip(values, values.dropFirst()).allSatisfy { $1 - $0 == 15 })
        #expect(values.contains(120) && values.contains(180) && values.contains(135))
    }

    @Test func aCurrentValueOutsideTheListStaysSelectable() {
        #expect(RestTime.pickerChoices(current: nil) == RestTime.pickerValues)
        #expect(RestTime.pickerChoices(current: 180) == RestTime.pickerValues)
        let withCoachValue = RestTime.pickerChoices(current: 45)
        #expect(withCoachValue.first == 45 && withCoachValue.count == 18 && withCoachValue == withCoachValue.sorted())
        #expect(RestTime.pickerChoices(current: 400).last == 400)
    }

    @Test func fallbackIsTwoMinutes() {
        #expect(RestTime.fallbackSeconds == 120)
    }
}

@MainActor
struct RestResolutionTests {
    private func row(_ seconds: Int?, pending: Bool = false) -> LocalRestOverride {
        LocalRestOverride(variantId: 7, restS: seconds, pending: pending)
    }

    @Test func nothingKnownIsTwoMinutes() {
        #expect(RestTime.resolve(local: nil, planned: nil, configDefault: nil) == 120)
    }

    @Test func planBeatsTheDefault() {
        #expect(RestTime.resolve(local: nil, planned: 180, configDefault: 120) == 180)
        #expect(RestTime.resolve(local: nil, planned: nil, configDefault: 90) == 90)
    }

    @Test func localOverrideBeatsThePlan() {
        #expect(RestTime.resolve(local: row(180), planned: 120, configDefault: 120) == 180)
        #expect(RestTime.resolve(local: row(150, pending: true), planned: 180, configDefault: 120) == 150)
    }

    @Test func aLocalOverrideWithoutValueMeansDefault() {
        // reset to default: the (older) plan value must not come back
        #expect(RestTime.resolve(local: row(nil, pending: true), planned: 180, configDefault: 120) == 120)
        #expect(RestTime.resolve(local: row(nil), planned: 180, configDefault: 90) == 90)
        #expect(RestTime.resolve(local: row(nil), planned: nil, configDefault: nil) == 120)
    }

    @Test func resolvesThroughTheStore() throws {
        let ctx = try makeContext()
        #expect(RestSettings.resolve(variantId: 7, planned: nil, in: ctx) == 120)
        #expect(RestSettings.resolve(variantId: 7, planned: 150, in: ctx) == 150)
        RestSettings.set(variantId: 7, seconds: 195, in: ctx)
        #expect(RestSettings.resolve(variantId: 7, planned: 150, in: ctx) == 195)
        #expect(RestSettings.resolve(variantId: 8, planned: 150, in: ctx) == 150)  // other exercises untouched
        RestSettings.set(variantId: 7, seconds: nil, in: ctx)
        #expect(RestSettings.resolve(variantId: 7, planned: 150, in: ctx) == 120)
    }

    @Test func theSyncedConfigDefaultIsUsed() throws {
        let ctx = try makeContext()
        let cfg = ConfigDTO(lifting: LiftingConfigDTO(restDefaultS: 90, restSByVariant: [:]))
        SyncState.current(in: ctx).configData = try APICoding.makeEncoder().encode(cfg)
        #expect(RestSettings.resolve(variantId: 7, planned: nil, in: ctx) == 90)
        RestSettings.set(variantId: 7, seconds: nil, in: ctx)
        #expect(RestSettings.resolve(variantId: 7, planned: 180, in: ctx) == 90)
    }
}

// MARK: - Decoding

struct RestDecodingTests {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try APICoding.makeDecoder().decode(T.self, from: Data(json.utf8))
    }

    @Test func planExerciseWithRestS() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"variant_id": 5, "name": "Squat", "rest_s": 180, "sets": []}"#)
        #expect(ex.restS == 180)
    }

    @Test func planExerciseWithoutRestS() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"variant_id": 5, "name": "Squat", "sets": []}"#)
        #expect(ex.restS == nil)
        let existing = try Fixture.decode(PlanTodayDTO.self, "plan_today")  // fixture of an older server
        #expect(existing.sessions.first?.session?.exercises?.allSatisfy { $0.restS == nil } == true)
    }

    @Test func planExerciseRestSSurvivesTheCacheRoundTrip() throws {
        let ex = try decode(PlannedExerciseDTO.self, #"{"name": "Row", "rest_s": 150, "sets": []}"#)
        let again = try APICoding.makeDecoder().decode(PlannedExerciseDTO.self, from: APICoding.makeEncoder().encode(ex))
        #expect(again.restS == 150)
    }

    @Test func configWithAndWithoutTheLiftingBlock() throws {
        let with = try decode(ConfigDTO.self, #"""
        {"version": "v5", "lifting": {"rest_default_s": 120, "rest_s_by_variant": {"122": 180, "7": 150}}}
        """#)
        #expect(with.lifting?.restDefaultS == 120)
        #expect(with.lifting?.restSByVariant == ["122": 180, "7": 150])
        let without = try decode(ConfigDTO.self, #"{"version": "v3", "timezone": "Europe/Vienna"}"#)
        #expect(without.lifting == nil)
        let again = try APICoding.makeDecoder().decode(ConfigDTO.self, from: APICoding.makeEncoder().encode(with))
        #expect(again == with)
    }

    @Test func requestAlwaysWritesRestS() throws {
        let set = String(decoding: try APICoding.makeEncoder().encode(RestRequestDTO(restS: 180)), as: UTF8.self)
        #expect(set == #"{"rest_s":180}"#)
        let reset = String(decoding: try APICoding.makeEncoder().encode(RestRequestDTO(restS: nil)), as: UTF8.self)
        #expect(reset == #"{"rest_s":null}"#)
    }

    @Test func response() throws {
        let r = try decode(RestResponseDTO.self, #"{"variant_id": 122, "rest_s": 180, "is_default": false, "config_version": "v3.4"}"#)
        #expect(r == RestResponseDTO(variantId: 122, restS: 180, isDefault: false, configVersion: "v3.4"))
        let old = try decode(RestResponseDTO.self, #"{"variant_id": 122, "rest_s": 120}"#)
        #expect(old.isDefault == nil)
    }
}

// MARK: - Plan to workout

@MainActor
struct RestFromPlanTests {
    @Test func setsStartedFromAPlanCarryItsRest() throws {
        let ctx = try makeContext()
        let json = #"""
        {"id": "p1", "date": "2026-10-26", "kind": "lower", "status": "proposed", "title": "Lower",
         "session": {"type": "lifting", "kind": "lower", "exercises": [
           {"order": 1, "variant_id": 42, "name": "Squat", "rest_s": 180, "sets": [{"index": 1, "set_type": "working", "target_weight": 80, "target_reps": 8}]},
           {"order": 2, "variant_id": 43, "name": "Leg Curl", "sets": [{"index": 1, "set_type": "working", "target_weight": 40, "target_reps": 10}]}]}}
        """#
        let item = try APICoding.makeDecoder().decode(PlanItemDTO.self, from: Data(json.utf8))
        PlanCache.merge(planned: [item], in: ctx)
        let row = try #require(ctx.fetch(FetchDescriptor<CachedPlannedSession>()).first)
        let session = try #require(WorkoutService(context: ctx).start(from: row, gymId: nil))
        let sets = WorkoutService(context: ctx).sets(of: session)
        #expect(sets.first { $0.variantId == 42 }?.plannedRestS == 180)
        #expect(sets.first { $0.variantId == 43 }?.plannedRestS == nil)
        // and the timer uses it: plan 180, no local row
        #expect(RestSettings.resolve(variantId: 42, planned: sets[0].plannedRestS, in: ctx) == 180)
        #expect(RestSettings.resolve(variantId: 43, planned: sets[1].plannedRestS, in: ctx) == 120)
    }
}

// MARK: - Local store

@MainActor
struct RestOverrideStoreTests {
    @Test func savingStoresAPendingOverrideAndKeepsOneRowPerVariant() throws {
        let ctx = try makeContext()
        RestSettings.set(variantId: 7, seconds: 180, in: ctx)
        RestSettings.set(variantId: 7, seconds: 195, in: ctx)
        let rows = try ctx.fetch(FetchDescriptor<LocalRestOverride>())
        #expect(rows.count == 1)
        #expect(rows[0].restS == 195 && rows[0].pending)
        RestSettings.set(variantId: 7, seconds: nil, in: ctx)
        #expect(try ctx.fetch(FetchDescriptor<LocalRestOverride>()).first?.restS == nil)
        #expect(RestSettings.pending(in: ctx).count == 1)
    }

    @Test func newFieldsHaveDefaults() throws {
        // lightweight migration: a LocalSet / row created without the new fields is valid
        let ctx = try makeContext()
        let set = LocalSet(sessionId: UUID(), variantId: 1, exerciseOrder: 1, setOrder: 1)
        ctx.insert(set)
        let row = LocalRestOverride(variantId: 1)
        ctx.insert(row)
        try ctx.save()
        #expect(set.plannedRestS == nil)
        #expect(row.restS == nil && !row.pending)
    }

    @Test func aBackendResetDropsThem() throws {
        let ctx = try makeContext()
        RestSettings.set(variantId: 7, seconds: 180, in: ctx)
        BackendReset.reset(in: ctx, fingerprint: "other")
        #expect(try ctx.fetch(FetchDescriptor<LocalRestOverride>()).isEmpty)
    }
}

// MARK: - Reconcile with the server (pull)

@MainActor
struct RestReconcileTests {
    private func lifting(_ map: [String: Int]) -> LiftingConfigDTO { LiftingConfigDTO(restDefaultS: 120, restSByVariant: map) }

    @Test func serverValuesReplaceSyncedRowsAndCreateMissingOnes() throws {
        let ctx = try makeContext()
        ctx.insert(LocalRestOverride(variantId: 1, restS: 150))  // changed on the server
        ctx.insert(LocalRestOverride(variantId: 2, restS: 180))  // reset on the server
        try ctx.save()
        RestSettings.reconcile(lifting(["1": 165, "3": 210]), in: ctx)
        let rows = Dictionary(uniqueKeysWithValues: try ctx.fetch(FetchDescriptor<LocalRestOverride>()).map { ($0.variantId, $0.restS) })
        #expect(rows[1] == 165)
        #expect(rows[2] == .some(nil))
        #expect(rows[3] == 210)
    }

    @Test func aPendingOverrideIsNotOverwritten() throws {
        let ctx = try makeContext()
        RestSettings.set(variantId: 1, seconds: 240, in: ctx)
        RestSettings.reconcile(lifting(["1": 120]), in: ctx)
        let row = try #require(RestSettings.override(for: 1, in: ctx))
        #expect(row.restS == 240 && row.pending)
    }

    @Test func anOlderServerWithoutTheBlockChangesNothing() throws {
        let ctx = try makeContext()
        ctx.insert(LocalRestOverride(variantId: 1, restS: 150))
        try ctx.save()
        RestSettings.reconcile(nil, in: ctx)
        RestSettings.reconcile(LiftingConfigDTO(restDefaultS: 120, restSByVariant: nil), in: ctx)
        #expect(RestSettings.override(for: 1, in: ctx)?.restS == 150)
    }

    @Test func aPullAppliesIt() throws {
        let ctx = try makeContext()
        let cfg = ConfigDTO(version: "v5", lifting: lifting(["42": 180]))
        try SyncMerge.apply(PullResponse(config: cfg), in: ctx)
        #expect(RestSettings.resolve(variantId: 42, planned: nil, in: ctx) == 180)
        #expect(RestSettings.pending(in: ctx).isEmpty)
    }
}

// MARK: - Upload (PUT /exercises/{id}/rest)

final class RestStubProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, String))?
    nonisolated(unsafe) static var requests: [(method: String, path: String, body: String)] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        var body = request.httpBody
        if body == nil, let stream = request.httpBodyStream {
            stream.open()
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                data.append(buffer, count: n)
            }
            stream.close()
            body = data
        }
        Self.requests.append((request.httpMethod ?? "", request.url?.path ?? "", String(decoding: body ?? Data(), as: UTF8.self)))
        do {
            let (status, json) = try Self.handler?(request) ?? (500, "{}")
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: Data(json.utf8))
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}

    static func client() -> APIClient {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.protocolClasses = [RestStubProtocol.self]
        requests = []
        return APIClient(baseURL: URL(string: "http://stub.test")!, token: "t", session: URLSession(configuration: cfg))
    }
}

@MainActor
@Suite(.serialized)
struct RestUploadTests {
    private func environment() throws -> ModelContainer { try Persistence.makeContainer(inMemory: true) }

    @Test func aPendingOverrideIsSentAndCleared() async throws {
        let container = try environment()
        RestSettings.set(variantId: 122, seconds: 180, in: container.mainContext)
        RestStubProtocol.handler = { _ in (200, #"{"variant_id": 122, "rest_s": 180, "is_default": false, "config_version": "v3.4"}"#) }
        let client = RestStubProtocol.client()
        let sent = try await RestSync.push(using: client, container: container)
        #expect(sent == 1)
        #expect(RestStubProtocol.requests.count == 1)
        #expect(RestStubProtocol.requests[0].method == "PUT")
        #expect(RestStubProtocol.requests[0].path == "/api/v1/exercises/122/rest")
        #expect(RestStubProtocol.requests[0].body == #"{"rest_s":180}"#)
        let ctx = ModelContext(container)
        #expect(RestSettings.pending(in: ctx).isEmpty)
        #expect(RestSettings.override(for: 122, in: ctx)?.restS == 180)
        // nothing left: the next sync sends nothing
        let again = try await RestSync.push(using: client, container: container)
        #expect(again == 0 && RestStubProtocol.requests.count == 1)
    }

    @Test func aResetSendsNullAndStoresTheDefault() async throws {
        let container = try environment()
        RestSettings.set(variantId: 122, seconds: nil, in: container.mainContext)
        RestStubProtocol.handler = { _ in (200, #"{"variant_id": 122, "rest_s": 120, "is_default": true}"#) }
        _ = try await RestSync.push(using: RestStubProtocol.client(), container: container)
        #expect(RestStubProtocol.requests.first?.body == #"{"rest_s":null}"#)
        let row = try #require(RestSettings.override(for: 122, in: ModelContext(container)))
        #expect(row.restS == nil && !row.pending)
    }

    @Test func offlineKeepsItPendingAndTheNextSyncRetries() async throws {
        let container = try environment()
        RestSettings.set(variantId: 122, seconds: 180, in: container.mainContext)
        RestStubProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        let client = RestStubProtocol.client()
        await #expect(throws: APIError.self) { try await RestSync.push(using: client, container: container) }
        let afterFailure = ModelContext(container)
        #expect(RestSettings.pending(in: afterFailure).count == 1)
        #expect(RestSettings.resolve(variantId: 122, planned: nil, in: afterFailure) == 180)  // applied locally all along

        RestStubProtocol.handler = { _ in (200, #"{"variant_id": 122, "rest_s": 180, "is_default": false}"#) }
        let sent = try await RestSync.push(using: client, container: container)
        #expect(sent == 1)
        #expect(RestSettings.pending(in: ModelContext(container)).isEmpty)
    }

    @Test func aServerErrorKeepsItPending() async throws {
        let container = try environment()
        RestSettings.set(variantId: 122, seconds: 180, in: container.mainContext)
        RestStubProtocol.handler = { _ in (503, "{}") }
        await #expect(throws: APIError.self) { try await RestSync.push(using: RestStubProtocol.client(), container: container) }
        #expect(RestSettings.pending(in: ModelContext(container)).count == 1)
    }

    @Test func aRejectedRequestIsDroppedFromTheQueue() async throws {
        let container = try environment()
        RestSettings.set(variantId: 999, seconds: 180, in: container.mainContext)
        RestStubProtocol.handler = { _ in (404, #"{"detail": "variant not found"}"#) }
        let sent = try await RestSync.push(using: RestStubProtocol.client(), container: container)
        #expect(sent == 0)
        #expect(RestSettings.pending(in: ModelContext(container)).isEmpty)
    }

    @Test func aChangeMadeWhileSendingStaysPending() async throws {
        let container = try environment()
        let main = container.mainContext
        RestSettings.set(variantId: 122, seconds: 180, in: main, now: date("2026-10-26T10:00:00.000Z"))
        RestStubProtocol.handler = { _ in
            // the user picks another time while the request is in flight
            DispatchQueue.main.sync {
                MainActor.assumeIsolated { RestSettings.set(variantId: 122, seconds: 240, in: main, now: date("2026-10-26T10:00:05.000Z")) }
            }
            return (200, #"{"variant_id": 122, "rest_s": 180, "is_default": false}"#)
        }
        _ = try await RestSync.push(using: RestStubProtocol.client(), container: container)
        let row = try #require(RestSettings.override(for: 122, in: ModelContext(container)))
        #expect(row.pending && row.restS == 240)
    }

    @Test func itRunsInsideTheSyncEngineBeforeThePull() async throws {
        let container = try environment()
        RestSettings.set(variantId: 42, seconds: 180, in: container.mainContext)
        RestStubProtocol.handler = { request in
            let path = request.url?.path ?? ""
            if path.hasSuffix("/rest") { return (200, #"{"variant_id": 42, "rest_s": 180, "is_default": false}"#) }
            if path.hasSuffix("/sync/pull") {
                return (200, #"{"cursor": "c1", "has_more": false, "config": {"version": "v3.4", "lifting": {"rest_default_s": 120, "rest_s_by_variant": {"42": 180}}}}"#)
            }
            return (200, #"{"date": "2026-10-26", "start": "2026-10-26", "days": [], "sessions": []}"#)
        }
        let engine = SyncEngine(container: container)
        _ = try await engine.sync(using: RestStubProtocol.client())
        let paths = RestStubProtocol.requests.map(\.path)
        let putIndex = try #require(paths.firstIndex { $0.hasSuffix("/exercises/42/rest") })
        let pullIndex = try #require(paths.firstIndex { $0.hasSuffix("/sync/pull") })
        #expect(putIndex < pullIndex)
        let ctx = ModelContext(container)
        #expect(RestSettings.pending(in: ctx).isEmpty)
        #expect(RestSettings.resolve(variantId: 42, planned: nil, in: ctx) == 180)
    }
}

// MARK: - Running timer

@MainActor
struct RestTimerRetargetTests {
    @Test func aRunningRestFollowsTheNewTimeOfItsExercise() {
        let timer = RestTimer()
        defer { timer.cancel() }
        timer.start(seconds: 120, variantId: 5)
        timer.retarget(variantId: 5, to: 180)
        #expect(timer.totalSeconds == 180)
        #expect(abs(timer.remaining() - 180) < 2)
    }

    @Test func otherExercisesAndIdleTimersAreIgnored() {
        let timer = RestTimer()
        defer { timer.cancel() }
        timer.retarget(variantId: 5, to: 180)
        #expect(!timer.isRunning)
        timer.start(seconds: 120, variantId: 5)
        timer.retarget(variantId: 6, to: 240)
        #expect(timer.totalSeconds == 120)
    }

    @Test func plusMinusFifteenIsAOneOffAdjustment() {
        let timer = RestTimer()
        defer { timer.cancel() }
        timer.start(seconds: 120, variantId: 5)
        timer.adjust(by: 15)
        #expect(timer.totalSeconds == 135)
        timer.cancel()
        timer.start(seconds: 120, variantId: 5)  // the next rest starts from the exercise's time again
        #expect(timer.totalSeconds == 120)
    }

    @Test func aShorterTimeNeverEndsInThePast() {
        let timer = RestTimer()
        defer { timer.cancel() }
        timer.start(seconds: 300, variantId: 5)
        timer.retarget(variantId: 5, to: 60, now: Date().addingTimeInterval(100))  // already 100 s into the rest
        #expect(timer.isRunning)
        #expect(timer.remaining(at: Date().addingTimeInterval(100)) >= 0.9)
    }
}

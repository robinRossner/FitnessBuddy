@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

// MARK: - Decoding

/// Hand-made gym payloads with crafted values (ladders, notes, a missing `previous`, an unmapped
/// exercise) that the generated `docs/api/examples` data doesn't have.
enum GymSamples {
    static let list = Data(#"""
        {"current_gym_id":4,"gyms":[{"id":3,"name":"Gym 3","created_at":"2026-09-01T08:00:00Z","machine_count":9,"is_current":false,"source":"import"},{"id":4,"name":"Gym 4","created_at":"2026-10-20T08:00:00Z","machine_count":2,"is_current":true,"source":"user"}]}
        """#.utf8)

    static let machines = Data(#"""
        {"gym":{"id":4,"name":"Gym 4","created_at":"2026-10-20T08:00:00Z","machine_count":2,"is_current":true,"source":"user"},"items":[
        {"exercise_id":1,"exercise_name":"Chest Press","body_region":"chest","in_program":true,"equipment_type":"machine_stack","variant":{"id":201,"name":"Chest Press @ Gym 4","equipment_type":"machine_stack","weight_ladder":[7,14,21,28,35],"notes":"seat 4","active":true,"sessions":3,"last_used":"2026-10-23"},"previous":{"gym_name":"Gym 3","variant_id":101,"last_top_set":{"weight_kg":65.0,"reps":9},"weight_ladder":[5,10,15]}},
        {"exercise_id":4,"exercise_name":"Lat Pulldown","body_region":"back","in_program":true,"equipment_type":"cable","variant":null,"previous":{"gym_name":"Gym 3","variant_id":104,"last_top_set":{"weight_kg":52.5,"reps":10},"weight_ladder":[2.5,5,7.5,10]}},
        {"exercise_id":6,"exercise_name":"Shoulder Press","body_region":"shoulders","in_program":true,"equipment_type":"machine_stack","variant":{"id":206,"name":"Shoulder Press @ Gym 4","equipment_type":"machine_stack","weight_ladder":[],"active":true}},
        {"exercise_id":9,"exercise_name":"Leg Curl","body_region":"legs","in_program":true,"equipment_type":"machine_stack","variant":null,"previous":null},
        {"exercise_id":3,"exercise_name":"Pec Deck","body_region":"chest","in_program":false,"equipment_type":"machine_stack","variant":null}
        ]}
        """#.utf8)

    static func listDTO() throws -> GymListDTO { try APICoding.makeDecoder().decode(GymListDTO.self, from: list) }
    static func machinesDTO() throws -> GymMachinesDTO { try APICoding.makeDecoder().decode(GymMachinesDTO.self, from: machines) }
}

// MARK: - Current gym sync

@MainActor
@Suite(.tags(.gyms, .sync))
struct CurrentGymSyncTests {
    @Test func serverValueWinsOtherwiseLocal() {
        #expect(GymSync.resolve(local: 3, server: 4) == 4)
        #expect(GymSync.resolve(local: 3, server: nil) == 3)  // offline / no config in the pull
        #expect(GymSync.resolve(local: nil, server: 4) == 4)
        #expect(GymSync.resolve(local: nil, server: nil) == nil)
    }

    @Test func pullStoresServerGymInConfig() throws {
        let ctx = try makeContext()
        var pull = PullResponse()
        pull.config = ConfigDTO(currentGymId: 4)
        try SyncMerge.apply(pull, in: ctx)
        #expect(SyncState.current(in: ctx).config?.currentGymId == 4)
    }

    @Test func startingAWorkoutOfflineUsesTheLocalGym() throws {
        let ctx = try makeContext()
        let settings = SettingsStore(defaults: UserDefaults(suiteName: "gym-sync-\(UUID().uuidString)")!, loadToken: false)
        settings.currentGymId = 4
        let session = WorkoutService(context: ctx).startEmpty(gymId: GymSync.resolve(local: settings.currentGymId, server: nil))
        #expect(session.gymId == 4)
    }
}

// MARK: - Setup / baseline UI rules

@Suite(.tags(.gyms))
struct SetupRulesTests {
    @Test func setupButtonOnlyWhenNeeded() {
        #expect(SetupRules.showsSetupButton(needsSetup: true))
        #expect(!SetupRules.showsSetupButton(needsSetup: false) && !SetupRules.showsSetupButton(needsSetup: nil))
    }

    @Test func baselineNoteAndHint() {
        #expect(SetupRules.isBaseline(state: "baseline") && !SetupRules.isBaseline(state: "deload") && !SetupRules.isBaseline(state: nil))
        #expect(SetupRules.baselineNote == "First session here: pick a weight for ~10 reps")
        #expect(SetupRules.placeholder(hintWeightKg: 42.5) == Fmt.number(42.5) && SetupRules.placeholder(hintWeightKg: nil) == nil)
    }

    @Test func baselineIsNeverPrefilled() {
        #expect(SetupRules.prefillWeight(target: nil, last: 60, baseline: true) == nil)
        #expect(SetupRules.prefillWeight(target: 50, last: 60, baseline: true) == nil)
        #expect(SetupRules.prefillWeight(target: 50, last: 60, baseline: false) == 50)
        #expect(SetupRules.prefillWeight(target: nil, last: 60, baseline: false) == 60)
    }

    @MainActor
    @Test func baselineWorkoutSetsStartEmptyWithAHint() throws {
        let ctx = try makeContext()
        let ex = PlannedExerciseDTO(order: 1, variantId: 201, name: "Chest Press @ Gym 4", repRange: [8, 12],
                                    sets: [PlannedSetDTO(index: 1, targetWeight: nil, targetReps: 10)],
                                    last: LastPerformanceDTO(date: "2026-10-20", sets: [LastSetDTO(weight: 65, reps: 9, durationS: nil)]),
                                    needsSetup: true, progressionState: "baseline", hintWeightKg: 42.5)
        let item = PlanItemDTO(id: "p1", date: AppClock.todayString, kind: "upper", title: "Upper",
                               session: PrescriptionDTO(type: "lifting", kind: "upper", exercises: [ex]))
        let row = CachedPlannedSession(id: "p1", date: item.date, kind: "upper", status: "proposed", title: "Upper", updatedAt: nil,
                                       isPreview: false, payload: try APICoding.makeEncoder().encode(item))
        ctx.insert(row)
        let service = WorkoutService(context: ctx)
        let session = try #require(service.start(from: row, gymId: 4))
        let set = try #require(service.sets(of: session).first)
        #expect(set.weightKg == nil && set.prevWeightKg == nil && set.targetWeightKg == nil)
        #expect(set.isBaseline && set.hintWeightKg == 42.5 && set.needsSetup)
        // ticking an empty baseline set does nothing until a weight is picked
        #expect(service.toggleComplete(set) == false && !set.isCompleted)
        service.setWeight(set, 40, in: session)
        #expect(service.toggleComplete(set) && set.weightKg == 40)
    }
}

// MARK: - Repository

@MainActor
@Suite(.tags(.gyms))
struct GymRepositoryTests {
    private final class Server: @unchecked Sendable {
        private let lock = NSLock()
        var calls: [String] = []
        var bodies: [String: String] = [:]
        var failure: APIError?
        var offline = false
        /// After `PUT /gyms/current` the list reports this gym as current.
        var current = 4

        func respond(_ method: String, _ path: String, _ body: Data?) throws -> Data {
            lock.withLock {
                calls.append(method + " " + path)
                if let body { bodies[method + " " + path] = String(decoding: body, as: UTF8.self) }
            }
            if offline { throw APIError.transport(URLError(.notConnectedToInternet)) }
            if let failure, method != "GET" { throw failure }
            switch (method, path) {
            case ("GET", "/api/v1/gyms"):
                return current == 4 ? GymSamples.list
                                    : Data("{\"current_gym_id\":\(current),\"gyms\":[{\"id\":3,\"name\":\"Gym 3\"},{\"id\":4,\"name\":\"Gym 4\"}]}".utf8)
            case ("GET", "/api/v1/gyms/4/machines"): return GymSamples.machines
            case ("PUT", "/api/v1/gyms/current"): return Data(#"{"current_gym_id":3,"unmapped_count":2}"#.utf8)
            case ("POST", "/api/v1/gyms"): return Data(#"{"id":5,"name":"Gym 5","machine_count":0,"is_current":false,"source":"user"}"#.utf8)
            case ("POST", "/api/v1/weight-ladder/preview"): return Data(#"{"weight_ladder":[5,10,15]}"#.utf8)
            case ("POST", _), ("PATCH", _), ("DELETE", _): return Data("{}".utf8)
            default: throw APIError.http(404, nil)
            }
        }
    }

    private func makeRepo(_ server: Server, configured: Bool = true, store: any StatsCacheStore = MemoryStatsCache(),
                          localGym: Int? = nil) -> (GymRepository, SettingsStore) {
        let settings = SettingsStore(defaults: UserDefaults(suiteName: "gym-tests-\(UUID().uuidString)")!, loadToken: false)
        if configured { settings.backendURL = "http://localhost:8000"; settings.apiToken = "test-token" }
        settings.currentGymId = localGym
        let repo = GymRepository(settings: settings, network: NetworkMonitor(), store: store)
        repo.transportOverride = { method, path, _, body in try server.respond(method, path, body) }
        return (repo, settings)
    }

    @Test func listAdoptsTheServersCurrentGym() async {
        let (repo, settings) = makeRepo(Server(), localGym: 3)
        await repo.loadList()
        #expect(repo.list?.gyms.count == 2 && repo.currentGymId == 4)
        #expect(settings.currentGymId == 4)  // server wins over the stale local value
    }

    @Test func offlineKeepsCacheAndLocalGym() async {
        let store = MemoryStatsCache()
        let online = Server()
        let (first, _) = makeRepo(online, store: store)
        await first.loadList()
        await first.loadMachines(gymId: 4)
        #expect(first.machineList(4)?.items.count == 5)

        let off = Server()
        off.offline = true
        let (second, settings) = makeRepo(off, store: store, localGym: 3)
        await second.loadList()
        await second.loadMachines(gymId: 4)
        #expect(second.isOffline && second.list?.gyms.count == 2 && second.machineList(4)?.items.count == 5)
        #expect(settings.currentGymId == 3)  // the local value is kept offline
    }

    @Test func switchGymStoresLocallyReplansAndReportsUnmapped() async throws {
        let server = Server()
        server.current = 3
        let (repo, settings) = makeRepo(server, localGym: 4)
        var replans = 0
        repo.afterPlanChange = { replans += 1 }
        let unmapped = try await repo.switchGym(to: 3)
        #expect(unmapped == 2 && repo.lastUnmappedCount == 2 && settings.currentGymId == 3 && replans == 1)
        #expect(server.bodies["PUT /api/v1/gyms/current"] == "{\"gym_id\":3}")
    }

    @Test func writesNeedAConnectionAndAreNeverQueued() async {
        let server = Server()
        let (repo, _) = makeRepo(server, configured: false)
        #expect(!repo.canWrite && repo.writeBlockedHint != nil)
        await #expect(throws: GymError.notConfigured) { try await repo.switchGym(to: 3) }
        #expect(server.calls.isEmpty)
    }

    @Test func conflictsCarryTheServersMessage() async {
        let server = Server()
        server.failure = .http(409, #"{"detail":"Gym 3 has logged sessions and can't be deleted."}"#)
        let (repo, _) = makeRepo(server)
        await #expect(throws: GymError.conflict("Gym 3 has logged sessions and can't be deleted.")) { try await repo.deleteGym(id: 3) }
    }

    @Test func saveCreatesOrPatches() async throws {
        let server = Server()
        let (repo, _) = makeRepo(server)
        let items = try GymSamples.machinesDTO().items
        let draft = MachineDraft(name: " Lat @ Gym 4 ", equipmentType: "cable", notes: "", ladder: [5, 10])
        _ = try await repo.saveMachine(gymId: 4, item: items[1], draft: draft)  // no variant: POST
        #expect(server.calls.contains("POST /api/v1/gyms/4/machines"))
        let post = try #require(server.bodies["POST /api/v1/gyms/4/machines"])
        #expect(post.contains("\"exercise_id\":4") && post.contains("\"name\":\"Lat @ Gym 4\"") && !post.contains("notes"))
        _ = try await repo.saveMachine(gymId: 4, item: items[0], draft: draft)  // has a variant: PATCH
        #expect(server.calls.contains("PATCH /api/v1/variants/201"))
    }

    @Test func previewUsesTheServerThenFallsBackToTheLocalRule() async {
        let server = Server()
        let (repo, _) = makeRepo(server)
        #expect(await repo.previewLadder(start: 5, step: 5, count: 3) == [5, 10, 15])
        server.offline = true
        #expect(await repo.previewLadder(start: 7, step: 7, count: 3) == [7, 14, 21])
        let (offline, _) = makeRepo(Server(), configured: false)
        #expect(await offline.previewLadder(start: 2.5, step: 2.5, count: 2) == [2.5, 5])
    }
}

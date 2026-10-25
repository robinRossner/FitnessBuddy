import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

// MARK: - Decoding

struct GymDecodingTests {
    @Test func gymListFixture() throws {
        let list = try Fixture.decode(GymListDTO.self, "gyms_list")
        #expect(list.currentGymId == 4 && list.gyms.count == 2)
        #expect(list.gyms[1].isCurrent == true && list.gyms[1].source == "user" && list.gyms[0].machineCount == 9)
        #expect(list.gyms[0].createdAt != nil)
    }

    @Test func machinesFixture() throws {
        let dto = try Fixture.decode(GymMachinesDTO.self, "gym_machines")
        #expect(dto.gym.name == "Gym 4" && dto.items.count == 5)
        let chest = dto.items[0]
        #expect(chest.variant?.ladder == [7, 14, 21, 28, 35] && chest.variant?.notes == "seat 4" && chest.variant?.lastUsed == "2026-10-23")
        #expect(chest.previous?.lastTopSet?.weightKg == 65 && chest.previous?.lastTopSet?.reps == 9)
        #expect(dto.items[1].variant == nil && dto.items[1].previous?.weightLadder == [2.5, 5, 7.5, 10])
    }

    @Test func serverExampleFixturesDecode() throws {
        let list = try Fixture.decode(GymListDTO.self, "gyms_list_server")
        #expect(list.currentGymId == 4 && list.gyms.count >= 3 && list.gyms.allSatisfy { $0.machineCount != nil })
        let dto = try Fixture.decode(GymMachinesDTO.self, "gym_machines_server")
        #expect(!dto.items.isEmpty && dto.items[0].variant?.weightLadder == nil && dto.items[0].previous?.gymName == "Gym 2")
        // a smith machine without steps counts as set up; the program progress is computed from the list
        #expect(MachineLogic.isSetUp(dto.items[0]) && MachineLogic.progress(dto.items).total > 0)
    }

    @Test func minimalPayloadsStillDecode() throws {
        let decoder = APICoding.makeDecoder()
        let list = try decoder.decode(GymListDTO.self, from: Data(#"{"gyms":[{"id":1,"name":"A"}]}"#.utf8))
        #expect(list.currentGymId == nil && list.gyms[0].machineCount == nil && list.gyms[0].source == nil)
        let item = try decoder.decode(MachineItemDTO.self, from: Data(#"{"exercise_id":7,"exercise_name":"Row"}"#.utf8))
        #expect(item.variant == nil && item.previous == nil && !item.inProgram && item.bodyRegion == nil)
        // a wrong shape in an additive field must not hide the row
        let odd = try decoder.decode(MachineItemDTO.self, from: Data(#"{"exercise_id":7,"exercise_name":"Row","variant":"x","previous":5}"#.utf8))
        #expect(odd.variant == nil && odd.previous == nil)
    }

    @Test func planExerciseWithAndWithoutSetupFields() throws {
        let decoder = APICoding.makeDecoder()
        let old = try decoder.decode(PlannedExerciseDTO.self, from: Data(#"{"name":"Pull Up","variant_id":42,"sets":[]}"#.utf8))
        #expect(old.needsSetup == nil && old.progressionState == nil && old.hintWeightKg == nil && old.progressionNote == nil)
        #expect(!old.showsSetupButton && !old.isBaseline && old.setupNote == nil)
        let new = try decoder.decode(PlannedExerciseDTO.self, from: Data(
            #"{"name":"Chest Press @ Gym 4","variant_id":201,"needs_setup":true,"progression_state":"baseline","hint_weight_kg":42.5,"progression_note":"first","sets":[]}"#.utf8))
        #expect(new.needsSetup == true && new.progressionState == "baseline" && new.hintWeightKg == 42.5 && new.progressionNote == "first")
        #expect(new.showsSetupButton && new.isBaseline)
        // survives the plan cache round trip (re-encoded, then decoded)
        let again = try decoder.decode(PlannedExerciseDTO.self, from: APICoding.makeEncoder().encode(new))
        #expect(again == new)
    }

    @Test func configWithAndWithoutCurrentGym() throws {
        let decoder = APICoding.makeDecoder()
        #expect(try decoder.decode(ConfigDTO.self, from: Data(#"{"version":"v1"}"#.utf8)).currentGymId == nil)
        #expect(try decoder.decode(ConfigDTO.self, from: Data(#"{"version":"v1","current_gym_id":4}"#.utf8)).currentGymId == 4)
    }

    @Test func requestsEncodeSnakeCaseAndSkipNil() throws {
        let enc = APICoding.makeEncoder()
        let create = try String(decoding: enc.encode(MachineCreateRequest(exerciseId: 4, name: "Lat", equipmentType: "cable", weightLadder: [5, 10], notes: nil)), as: UTF8.self)
        #expect(create.contains("\"exercise_id\":4") && create.contains("\"equipment_type\":\"cable\"") && create.contains("\"weight_ladder\":[5,10]"))
        #expect(!create.contains("notes"))
        #expect(String(decoding: try enc.encode(GymCurrentRequest(gymId: 4)), as: UTF8.self) == "{\"gym_id\":4}")
    }
}

// MARK: - Ladder rules

struct LadderRulesTests {
    @Test func generatesStartPlusStep() {
        #expect(LadderRules.generate(start: 5, step: 5, count: 4) == [5, 10, 15, 20])
        #expect(LadderRules.generate(start: 5, step: 5, count: 15).last == 75)
    }

    @Test func roundsToQuarterSortsAndDedupes() {
        #expect(LadderRules.generate(start: 2.6, step: 2.6, count: 3) == [2.5, 5.25, 7.75])
        #expect(LadderRules.generate(start: 1, step: 0.1, count: 4) == [1, 1.25])  // 1, 1.1, 1.2, 1.3 collapse
        #expect(LadderRules.normalize([20, 5, 5.01, 12.5, 20]) == [5, 12.5, 20])
        #expect(LadderRules.generate(start: 5, step: 5, count: 3, extra: [2.5, 10, 12.5]) == [2.5, 5, 10, 12.5, 15])
    }

    @Test func atMostSixtySteps() {
        #expect(LadderRules.generate(start: 1, step: 1, count: 500).count == 60)
        #expect(LadderRules.normalize((1...100).map(Double.init)).count == 60)
        #expect(LadderRules.generate(start: 1, step: 1, count: 500).last == 60)
    }

    @Test func invalidInputGivesNothing() {
        #expect(LadderRules.generate(start: 0, step: 5, count: 5).isEmpty)
        #expect(LadderRules.generate(start: 5, step: 0, count: 5).isEmpty)
        #expect(LadderRules.generate(start: 5, step: 5, count: 0).isEmpty)
        #expect(LadderRules.generate(start: -5, step: 5, count: 5).isEmpty)
        #expect(LadderRules.generate(start: 5, step: .nan, count: 5).isEmpty)
    }

    @Test func chipAddAndRemove() {
        var ladder: [Double] = [5, 10, 15]
        ladder = LadderRules.adding(2.5, to: ladder)
        #expect(ladder == [2.5, 5, 10, 15])
        ladder = LadderRules.adding(10, to: ladder)  // duplicate
        #expect(ladder == [2.5, 5, 10, 15])
        ladder = LadderRules.adding(77.3, to: ladder)  // odd top weight, rounded
        #expect(ladder.last == 77.25)
        ladder = LadderRules.removing(5, from: ladder)
        #expect(ladder == [2.5, 10, 15, 77.25])
        #expect(LadderRules.removing(99, from: ladder) == ladder)
        #expect(LadderRules.adding(0, to: ladder) == ladder && LadderRules.adding(-3, to: ladder) == ladder)
    }

    @Test func parsingAndPresetsAndSummary() {
        #expect(LadderRules.parse("2,5") == 2.5 && LadderRules.parse(" 12.5 kg ") == 12.5)
        #expect(LadderRules.parse("") == nil && LadderRules.parse("abc") == nil && LadderRules.parse("0") == nil)
        #expect(LadderRules.presets.map(\.title) == ["5 kg steps", "2.5 kg steps", "7 kg plates"])
        let seven = LadderRules.presets[2]
        #expect(LadderRules.generate(start: seven.start, step: seven.step, count: seven.count).prefix(3) == [7, 14, 21])
        #expect(LadderRules.summary(LadderRules.generate(start: 5, step: 5, count: 15)) == "15 steps · 5–75 kg")
        #expect(LadderRules.summary([]) == nil && LadderRules.summary([20]) == "1 step · 20–20 kg")
        let seed = LadderRules.generatorSeed(for: [7, 14, 21])
        #expect(seed.start == 7 && seed.step == 7 && seed.count == 3)
    }
}

// MARK: - Machine list

struct MachineLogicTests {
    private func items() throws -> [MachineItemDTO] { try Fixture.decode(GymMachinesDTO.self, "gym_machines").items }

    @Test func setUpAndProgress() throws {
        let items = try items()
        #expect(MachineLogic.isSetUp(items[0]))
        #expect(!MachineLogic.isSetUp(items[1]))  // no machine yet
        #expect(!MachineLogic.isSetUp(items[2]))  // stack machine without steps
        let p = MachineLogic.progress(items)
        #expect(p.done == 1 && p.total == 4 && p.remaining == 3 && !p.isComplete)  // "Pec Deck" is not in the program
        #expect(p.text == "1 of 4 program machines set up")
        #expect(MachineLogic.progress([]).text == "No program machines at this gym")
    }

    @Test func smithAndPlateLoadedNeedNoSteps() {
        let smith = MachineItemDTO(exerciseId: 2, exerciseName: "Incline", inProgram: true, equipmentType: "smith",
                                   variant: MachineVariantDTO(id: 1, name: "x", equipmentType: "smith", weightLadder: []))
        #expect(MachineLogic.isSetUp(smith) && MachineLogic.statusText(smith) == "Set up")
        var inactive = smith
        inactive.variant?.active = false
        #expect(!MachineLogic.isSetUp(inactive))
    }

    @Test func statusAndHintText() throws {
        let items = try items()
        #expect(MachineLogic.statusText(items[0]) == "5 steps · 7–35 kg")
        #expect(MachineLogic.statusText(items[1]) == "Needs setup")
        #expect(MachineLogic.previousHint(items[1].previous) == "Gym 3: \(Fmt.number(52.5)) kg × 10")
        #expect(MachineLogic.previousHint(nil) == nil)
        #expect(MachineLogic.previousHint(PreviousMachineDTO(gymName: "Gym 2", weightLadder: [5, 10])) == "Gym 2: 2 steps · 5–10 kg")
        #expect(MachineLogic.needsSetupText(3) == "3 machines need setup" && MachineLogic.needsSetupText(1) == "1 machine needs setup")
        #expect(MachineLogic.needsSetupText(0) == nil && MachineLogic.needsSetupText(nil) == nil)
        #expect(MachineLogic.defaultName(exercise: "Lat Pulldown", gym: "Gym 4") == "Lat Pulldown @ Gym 4")
    }

    @Test func sectionsGroupProgramByRegionThenOthers() throws {
        let s = MachineLogic.sections(try items())
        #expect(s.program.map(\.title) == ["Chest", "Back", "Shoulders", "Legs"])
        #expect(s.other.map(\.exerciseName) == ["Pec Deck"])
    }

    @Test func matchesPlanExercises() throws {
        let items = try items()
        #expect(MachineLogic.match(items, exerciseId: 4, slug: nil, name: nil)?.exerciseName == "Lat Pulldown")
        #expect(MachineLogic.match(items, exerciseId: nil, slug: "leg_curl", name: nil)?.exerciseId == 9)
        #expect(MachineLogic.match(items, exerciseId: nil, slug: nil, name: "Chest Press @ Gym 4")?.exerciseId == 1)
        #expect(MachineLogic.match(items, exerciseId: 99, slug: "nope", name: "Nope") == nil)
    }

    @Test func draftValidity() {
        #expect(!MachineDraft(name: " ", equipmentType: "smith", notes: "", ladder: []).isValid)
        #expect(!MachineDraft(name: "A", equipmentType: "machine_stack", notes: "", ladder: []).isValid)
        #expect(MachineDraft(name: "A", equipmentType: "machine_stack", notes: "", ladder: [5]).isValid)
        #expect(MachineDraft(name: "A", equipmentType: "smith", notes: "", ladder: []).isValid)
    }
}

// MARK: - Current gym sync

@MainActor
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
                return current == 4 ? try Fixture.data("gyms_list")
                                    : Data("{\"current_gym_id\":\(current),\"gyms\":[{\"id\":3,\"name\":\"Gym 3\"},{\"id\":4,\"name\":\"Gym 4\"}]}".utf8)
            case ("GET", "/api/v1/gyms/4/machines"): return try Fixture.data("gym_machines")
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
        let items = try Fixture.decode(GymMachinesDTO.self, "gym_machines").items
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

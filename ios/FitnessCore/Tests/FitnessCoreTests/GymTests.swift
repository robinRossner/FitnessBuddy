import Foundation
import Testing
@testable import FitnessCore

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

@Suite(.tags(.gyms, .decoding, .fast))
struct GymDecodingTests {
    @Test func gymListFixture() throws {
        let list = try GymSamples.listDTO()
        #expect(list.currentGymId == 4 && list.gyms.count == 2)
        #expect(list.gyms[1].isCurrent == true && list.gyms[1].source == "user" && list.gyms[0].machineCount == 9)
        #expect(list.gyms[0].createdAt != nil)
    }

    @Test func machinesFixture() throws {
        let dto = try GymSamples.machinesDTO()
        #expect(dto.gym.name == "Gym 4" && dto.items.count == 5)
        let chest = dto.items[0]
        #expect(chest.variant?.ladder == [7, 14, 21, 28, 35] && chest.variant?.notes == "seat 4" && chest.variant?.lastUsed == "2026-10-23")
        #expect(chest.previous?.lastTopSet?.weightKg == 65 && chest.previous?.lastTopSet?.reps == 9)
        #expect(dto.items[1].variant == nil && dto.items[1].previous?.weightLadder == [2.5, 5, 7.5, 10])
    }

    @Test func serverExampleFixturesDecode() throws {
        let list = try Fixture.decode(GymListDTO.self, "gyms_list")
        #expect(list.currentGymId == 4 && list.gyms.count >= 3 && list.gyms.allSatisfy { $0.machineCount != nil })
        let dto = try Fixture.decode(GymMachinesDTO.self, "gym_machines")
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

@Suite(.tags(.gyms, .fast))
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

@Suite(.tags(.gyms, .fast))
struct MachineLogicTests {
    private func items() throws -> [MachineItemDTO] { try GymSamples.machinesDTO().items }

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

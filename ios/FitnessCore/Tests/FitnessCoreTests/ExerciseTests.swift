import Foundation
import Testing
@testable import FitnessCore

// MARK: - Today

@Suite(.tags(.workout, .fast))
struct TodayTitleTests {
    @Test func liftingCardsOnlyNameTheHalf() {
        #expect(PlanFormat.cardTitle(type: "lifting", kind: "upper", title: "Upper (deload)") == "Upper body")
        #expect(PlanFormat.cardTitle(type: "lifting", kind: "lower", title: nil) == "Lower body")
        #expect(PlanFormat.cardTitle(type: "easy_run", kind: "easy_run", title: nil) == "Easy run")
    }
}

// MARK: - Progression

@Suite(.tags(.workout, .fast))
struct ProgressionTests {
    private func decode(_ json: String) throws -> ExerciseDTO {
        try APICoding.makeDecoder().decode(ExerciseDTO.self, from: Data(json.utf8))
    }

    private func object(_ value: some Encodable) throws -> [String: Any] {
        try #require(JSONSerialization.jsonObject(with: APICoding.makeEncoder().encode(value)) as? [String: Any])
    }

    @Test func serverValueIsDecoded() throws {
        let x = try decode(#"{"id": 1, "name": "Knee Raise", "is_timed": false, "progression": "reps"}"#)
        #expect(x.progression == .reps && x.resolvedProgression == .reps)
        #expect(try decode(#"{"id": 2, "name": "Plank", "is_timed": true, "progression": "time"}"#).resolvedProgression == .time)
    }

    @Test func olderServersFallBackToIsTimed() throws {
        let plain = try decode(#"{"id": 1, "name": "Bench"}"#)
        #expect(plain.progression == nil && plain.resolvedProgression == .load)
        #expect(try decode(#"{"id": 2, "name": "Plank", "is_timed": true}"#).resolvedProgression == .time)
        #expect(try decode(#"{"id": 3, "name": "Row", "is_timed": false}"#).resolvedProgression == .load)
    }

    @Test func unknownValueDoesNotBreakTheExercise() throws {
        let x = try decode(#"{"id": 1, "name": "Bench", "is_timed": true, "progression": "tempo"}"#)
        #expect(x.progression == nil && x.resolvedProgression == .time)
    }

    @Test func resolveAndLabels() {
        #expect(Progression.resolve("reps", isTimed: nil) == .reps)
        #expect(Progression.resolve(nil, isTimed: true) == .time)
        #expect(Progression.resolve("nonsense", isTimed: false) == .load)
        #expect(Progression.allCases.map(\.label) == ["Weight", "Reps only", "Time"])
        #expect(Progression.editable == [.load, .reps])
    }

    @Test func patchSendsOnlyTheProgression() throws {
        let body = try object(ExercisePatchRequest(progression: .reps))
        #expect(body["progression"] as? String == "reps")
        #expect(body.keys.contains("name") == false && body.keys.contains("muscle_group") == false)
        #expect(try object(ExercisePatchRequest(name: "Pec Fly")).keys.contains("progression") == false)
    }

    @Test func createSendsProgressionAndTimedConsistently() throws {
        let reps = try object(ExerciseCreateRequest(name: "Pec Fly", muscleGroup: "chest", equipmentType: "cable",
                                                    timed: false, progression: .reps, gymId: 4, incrementKg: nil))
        #expect(reps["progression"] as? String == "reps" && reps["timed"] as? Bool == false)
        let time = try object(ExerciseCreateRequest(name: "Plank", muscleGroup: "abs", equipmentType: "bodyweight",
                                                    timed: false, progression: .time, gymId: nil, incrementKg: nil))
        #expect(time["progression"] as? String == "time" && time["timed"] as? Bool == true)
        // without a progression (old call sites) only `timed` goes out
        let legacy = try object(ExerciseCreateRequest(name: "Plank", muscleGroup: "abs", equipmentType: "bodyweight",
                                                      timed: true, gymId: nil, incrementKg: nil))
        #expect(legacy["timed"] as? Bool == true && legacy.keys.contains("progression") == false)
    }
}

@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

// MARK: - Set validation

@MainActor
@Suite(.tags(.workout))
struct SetValidationTests {
    @Test func weightAndRepsAreRequired() {
        #expect(SetValidation.missing(weight: nil, reps: nil, duration: nil, timed: false, equipmentType: "machine_stack") == [.weight, .reps])
        #expect(SetValidation.missing(weight: 0, reps: 8, duration: nil, timed: false, equipmentType: "cable") == [.weight])
        #expect(SetValidation.missing(weight: 40, reps: 0, duration: nil, timed: false, equipmentType: "cable") == [.reps])
        #expect(SetValidation.missing(weight: 40, reps: 8, duration: nil, timed: false, equipmentType: "cable").isEmpty)
    }

    @Test func bodyweightAndHoldsNeedNoWeight() {
        #expect(SetValidation.missing(weight: nil, reps: 6, duration: nil, timed: false, equipmentType: "bodyweight").isEmpty)
        #expect(SetValidation.missing(weight: nil, reps: nil, duration: 30, timed: true, equipmentType: "other").isEmpty)
        #expect(SetValidation.missing(weight: nil, reps: nil, duration: nil, timed: true, equipmentType: "other") == [.duration])
    }

    @Test func anEmptyMachineSetCannotBeTicked() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = svc.startEmpty(gymId: 4)
        let machine = CachedVariant(id: 9, exerciseId: 2, name: "Lat Pulldown (Cable) @ Gym 4", gymId: 4, equipmentType: "cable")
        ctx.insert(machine)
        svc.addExercise(machine, to: session)
        let set = try #require(svc.sets(of: session).first)
        #expect(set.weightKg == nil)
        #expect(!svc.toggleComplete(set) && !set.isCompleted)
        #expect(svc.missingFields(set) == [.weight, .reps])
        svc.setWeight(set, 45, in: session)
        svc.setReps(set, 10)
        #expect(svc.missingFields(set).isEmpty)
        #expect(svc.toggleComplete(set) && set.weightKg == 45)
    }

    @Test func bodyweightSetIsLoggedWithZeroAddedWeight() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = svc.startEmpty(gymId: nil)
        let pullUp = CachedVariant(id: 3, exerciseId: 1, name: "Pull Up", gymId: nil, equipmentType: "bodyweight")
        ctx.insert(pullUp)
        svc.addExercise(pullUp, to: session)
        let set = try #require(svc.sets(of: session).first)
        svc.setReps(set, 8)
        #expect(svc.toggleComplete(set) && set.weightKg == 0)
    }
}

// MARK: - Picker grouping

@MainActor
@Suite(.tags(.workout))
struct ExerciseCatalogTests {
    @Test func groupsByRegionAndOffersSetupForExercisesMissingAtThisGym() throws {
        let ctx = try makeContext()
        let lat = CachedExercise(id: 1, name: "Lat Pulldown", muscleGroup: "lats", bodyRegion: "Back")
        let press = CachedExercise(id: 2, name: "Chest Press", muscleGroup: "chest")  // older server: no region
        let bench = CachedExercise(id: 3, name: "Bench Press", muscleGroup: "chest", bodyRegion: "Chest")
        let cardio = CachedExercise(id: 4, name: "Cardio", muscleGroup: "full_body", movementPattern: "cardio")
        let variants = [
            CachedVariant(id: 10, exerciseId: 1, name: "Lat Pulldown (Cable) @ Gym 3", gymId: 3, equipmentType: "cable"),
            CachedVariant(id: 11, exerciseId: 2, name: "Chest Press (Machine) @ Gym 4", gymId: 4, equipmentType: "machine_stack"),
            CachedVariant(id: 12, exerciseId: 3, name: "Bench Press (Barbell)", gymId: nil, equipmentType: "barbell"),
            CachedVariant(id: 13, exerciseId: 3, name: "Bench Press (Old)", gymId: nil, equipmentType: "barbell", active: false),
        ]
        for x in [lat, press, bench, cardio] { ctx.insert(x) }
        for v in variants { ctx.insert(v) }

        let groups = ExerciseCatalog.groups(exercises: [lat, press, bench, cardio], variants: variants, gymId: 4)
        #expect(groups.map(\.region) == ["Chest", "Back"])
        let chest = try #require(groups.first)
        #expect(chest.rows.map(\.title) == ["Bench Press (Barbell)", "Chest Press (Machine)"])
        let back = try #require(groups.last?.rows.first)
        #expect(back.variantId == nil && back.exerciseId == 1)  // only at Gym 3: set it up here

        let search = ExerciseCatalog.groups(exercises: [lat, press, bench], variants: variants, gymId: 4, search: "pull")
        #expect(search.flatMap(\.rows).map(\.exerciseId) == [1])
    }
}

// MARK: - Exercise step, requests

@Suite(.tags(.workout))
struct ExerciseSettingsTests {
    @Test func ownStepReplacesLadderAndDefault() {
        #expect(WeightStepper.next(from: 40, ladder: [40, 50], equipmentType: "machine_stack", dumbbellIncrement: nil, step: 2.5) == 42.5)
        #expect(WeightStepper.previous(from: 1, ladder: [], equipmentType: "barbell", dumbbellIncrement: nil, step: 2.5) == 0)
    }

    @Test func variantPatchAlwaysSendsTheStep() throws {
        let reset = try JSONSerialization.jsonObject(with: APICoding.makeEncoder().encode(
            VariantSettingsPatch(equipmentType: "cable", incrementKg: nil))) as? [String: Any]
        #expect(reset?["equipment_type"] as? String == "cable")
        #expect(reset?.keys.contains("increment_kg") == true && reset?["increment_kg"] is NSNull)
        let create = try JSONSerialization.jsonObject(with: APICoding.makeEncoder().encode(
            ExerciseCreateRequest(name: "Pec Deck", muscleGroup: "chest", equipmentType: "machine_stack", timed: false,
                                  gymId: 4, incrementKg: nil))) as? [String: Any]
        #expect(create?["muscle_group"] as? String == "chest" && create?["gym_id"] as? Int == 4)
        #expect(create?["increment_kg"] == nil)
    }

    @Test func renameSendsOnlyTheName() throws {
        let body = try JSONSerialization.jsonObject(with: APICoding.makeEncoder().encode(
            ExercisePatchRequest(name: "Pec Fly"))) as? [String: Any]
        #expect(body?["name"] as? String == "Pec Fly" && body?.keys.contains("muscle_group") == false)
        let muscle = try JSONSerialization.jsonObject(with: APICoding.makeEncoder().encode(
            ExercisePatchRequest(muscleGroup: "abs"))) as? [String: Any]
        #expect(muscle?["muscle_group"] as? String == "abs" && muscle?.keys.contains("name") == false)
    }

    @Test func editableNameDropsTheGymAndTheTypeLabel() {
        #expect(ExerciseOptions.editableName(variantName: "Pec Deck (Machine) @ Gym 3", equipmentType: "machine_stack") == "Pec Deck")
        #expect(ExerciseOptions.editableName(variantName: "Squat (Smith Machine)", equipmentType: "smith") == "Squat")
        #expect(ExerciseOptions.editableName(variantName: "Pull Up", equipmentType: "bodyweight") == "Pull Up")
        #expect(ExerciseOptions.editableName(variantName: "Row (Cable)", equipmentType: "barbell") == "Row (Cable)")  // another label
        #expect(ExerciseOptions.editableName(variantName: "Sled Push", equipmentType: "other") == "Sled Push")
        #expect(ExerciseOptions.cleanName("  Pec \n  Fly ") == "Pec Fly")
    }

    @Test func renameAnswerCarriesTheRenamedVariants() throws {
        let json = #"""
        {"id": 7, "name": "Pec Fly", "muscle_group": "chest", "movement_pattern": "other",
         "variants": [{"id": 70, "exercise_id": 7, "name": "Pec Fly (Machine) @ Gym 3", "gym_id": 3, "equipment_type": "machine_stack"}]}
        """#
        let answer = try APICoding.makeDecoder().decode(ExerciseRenameResponse.self, from: Data(json.utf8))
        #expect(answer.exercise.name == "Pec Fly" && answer.variants.map(\.name) == ["Pec Fly (Machine) @ Gym 3"])
        let plain = try APICoding.makeDecoder().decode(ExerciseRenameResponse.self, from: Data(#"{"id": 7, "name": "X"}"#.utf8))
        #expect(plain.variants.isEmpty)
    }

    @MainActor
    @Test func renameUpdatesTheCacheAndTheSetsOfThisDevice() throws {
        let ctx = try makeContext()
        let ex = CachedExercise(id: 7, name: "Pec Deck", muscleGroup: "chest")
        let v = CachedVariant(id: 70, exerciseId: 7, name: "Pec Deck (Machine) @ Gym 3", gymId: 3, equipmentType: "machine_stack")
        let set = LocalSet(sessionId: UUID(), variantId: 70, variantName: v.name, exerciseOrder: 0, setOrder: 0)
        ctx.insert(ex); ctx.insert(v); ctx.insert(set)
        let answer = try APICoding.makeDecoder().decode(ExerciseRenameResponse.self, from: Data(#"""
            {"id": 7, "name": "Pec Fly", "muscle_group": "chest",
             "variants": [{"id": 70, "exercise_id": 7, "name": "Pec Fly (Machine) @ Gym 3", "gym_id": 3, "equipment_type": "machine_stack"}]}
            """#.utf8))
        ExerciseLibrary.apply(answer, in: ctx)
        #expect(ex.name == "Pec Fly" && v.name == "Pec Fly (Machine) @ Gym 3" && set.variantName == v.name)
    }

    @MainActor
    @Test func aPulledVariantRenameReachesTheSetsOfThatVariant() throws {
        let ctx = try makeContext()
        let v = CachedVariant(id: 70, exerciseId: 7, name: "Pec Deck (Machine) @ Gym 3", gymId: 3, equipmentType: "machine_stack")
        let dirty = LocalSet(sessionId: UUID(), variantId: 70, variantName: v.name, exerciseOrder: 0, setOrder: 0)
        dirty.weightKg = 40  // an unsynced edit of this device
        let other = LocalSet(sessionId: UUID(), variantId: 71, variantName: "Pec Deck (Machine) @ Gym 3", exerciseOrder: 0, setOrder: 0)
        let custom = LocalSet(sessionId: UUID(), variantId: 70, variantName: "Something else", exerciseOrder: 1, setOrder: 0)
        ctx.insert(v); ctx.insert(dirty); ctx.insert(other); ctx.insert(custom)
        let updatedAt = dirty.updatedAt
        let dto = VariantDTO(id: 70, exerciseId: 7, name: "Pec Fly (Machine) @ Gym 3", gymId: 3, equipmentType: "machine_stack")
        SyncMerge.upsert(variant: dto, existing: v, in: ctx)
        #expect(v.name == "Pec Fly (Machine) @ Gym 3" && dirty.variantName == v.name)
        #expect(dirty.dirty && dirty.weightKg == 40 && dirty.updatedAt == updatedAt)  // the edit is untouched, not re-pushed
        #expect(other.variantName == "Pec Deck (Machine) @ Gym 3" && custom.variantName == "Something else")
    }

    @Test func pulledExerciseFieldsAreOptional() throws {
        let json = #"{"id": 1, "name": "Bench Press", "muscle_group": "chest", "movement_pattern": "horizontal_push"}"#
        let old = try APICoding.makeDecoder().decode(ExerciseDTO.self, from: Data(json.utf8))
        #expect(old.bodyRegion == nil && old.isTimed == nil)
    }
}

// MARK: - Finish reminder

@MainActor
@Suite(.tags(.workout))
struct FinishReminderTests {
    @Test func lastSetArmsTheReminderThreeMinutesAfterTheRest() throws {
        let timer = RestTimer(silent: true)
        defer { timer.cancel(); timer.cancelFinishReminder() }
        timer.start(seconds: 120, variantId: 5, finishReminder: true)
        let end = try #require(timer.endDate)
        #expect(timer.finishReminderDate == end.addingTimeInterval(WorkoutNotifications.finishReminderDelay))
        timer.adjust(by: 15)  // the reminder follows the rest
        #expect(timer.finishReminderDate == timer.endDate?.addingTimeInterval(WorkoutNotifications.finishReminderDelay))
    }

    @Test func skippingTheRestCountsFromNowAndAnotherSetCancels() throws {
        let timer = RestTimer(silent: true)
        defer { timer.cancel(); timer.cancelFinishReminder() }
        timer.start(seconds: 120, variantId: 5, finishReminder: true)
        timer.cancel()
        let due = try #require(timer.finishReminderDate)
        #expect(abs(due.timeIntervalSinceNow - WorkoutNotifications.finishReminderDelay) < 2)
        timer.start(seconds: 120, variantId: 6)  // a set that wasn't the last one
        #expect(timer.finishReminderDate == nil)
    }

    @Test func restIsSilentInTheAppReminderIsShown() {
        #expect(WorkoutNotifications.inAppPresentation(forId: WorkoutNotifications.restId).isEmpty)
        #expect(WorkoutNotifications.inAppPresentation(forId: WorkoutNotifications.finishReminderId).contains(.banner))
    }
}

import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

@Suite(.tags(.workout, .fast))
struct WeightStepperTests {
    let ladder: [Double] = [10, 12.5, 15, 20, 25]

    @Test func ladderNextAndPrevious() {
        #expect(WeightStepper.next(from: 15, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 20)
        #expect(WeightStepper.previous(from: 15, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 12.5)
        // off-ladder values snap to the adjacent rung
        #expect(WeightStepper.next(from: 16, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 20)
        #expect(WeightStepper.previous(from: 16, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 15)
    }

    @Test func beyondLadderFallsBackToEquipmentStep() {
        #expect(WeightStepper.next(from: 25, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 27.5)
        #expect(WeightStepper.previous(from: 10, ladder: ladder, equipmentType: "cable", dumbbellIncrement: nil) == 7.5)
    }

    @Test func equipmentDefaults() {
        #expect(WeightStepper.next(from: 60, ladder: [], equipmentType: "barbell", dumbbellIncrement: 2) == 65)
        #expect(WeightStepper.previous(from: 60, ladder: [], equipmentType: "barbell", dumbbellIncrement: 2) == 55)
        #expect(WeightStepper.next(from: 20, ladder: [], equipmentType: "dumbbell", dumbbellIncrement: 2) == 22)
        #expect(WeightStepper.next(from: 20, ladder: [], equipmentType: "dumbbell", dumbbellIncrement: 2.5) == 22.5)
        #expect(WeightStepper.next(from: 20, ladder: [], equipmentType: "machine_stack", dumbbellIncrement: 2) == 22.5)
        #expect(WeightStepper.next(from: nil, ladder: [], equipmentType: "other", dumbbellIncrement: nil) == 2.5)
    }

    @Test func backendIncrementsOverrideDefaults() {
        let inc: [String: Double] = ["barbell": 5, "smith": 5, "dumbbell": 2, "default_stack_step": 5, "bodyweight_added": 2.5, "other": 2.5]
        #expect(WeightStepper.next(from: 70, ladder: [], equipmentType: "smith", dumbbellIncrement: nil, increments: inc) == 75)
        #expect(WeightStepper.next(from: 40, ladder: [], equipmentType: "machine_stack", dumbbellIncrement: nil, increments: inc) == 45)
        #expect(WeightStepper.next(from: 17.5, ladder: [], equipmentType: "bodyweight", dumbbellIncrement: nil, increments: inc) == 20)
        #expect(WeightStepper.next(from: 20, ladder: [], equipmentType: "dumbbell", dumbbellIncrement: 2.5, increments: inc) == 22.5)
    }

    @Test func neverGoesNegative() {
        #expect(WeightStepper.previous(from: 2.5, ladder: [], equipmentType: "barbell", dumbbellIncrement: nil) == 0)
        #expect(WeightStepper.previous(from: nil, ladder: [], equipmentType: "other", dumbbellIncrement: nil) == 0)
        #expect(CountStepper.reps(0, delta: -1) == 0)
    }
}

@MainActor
@Suite(.tags(.workout, .fast))
struct WorkoutSummaryTests {
    @Test func volumeCountsCompletedNonWarmupSetsOnly() {
        let sid = UUID()
        let sets = [
            LocalSet(sessionId: sid, variantId: 1, exerciseOrder: 1, setOrder: 1, weightKg: 100, reps: 5, completedAt: .now),
            LocalSet(sessionId: sid, variantId: 1, exerciseOrder: 1, setOrder: 2, weightKg: 100, reps: 5, completedAt: .now),
            LocalSet(sessionId: sid, variantId: 1, exerciseOrder: 1, setOrder: 0, setType: "warmup", weightKg: 40, reps: 10, completedAt: .now),
            LocalSet(sessionId: sid, variantId: 2, exerciseOrder: 2, setOrder: 1, weightKg: 20, reps: 10, completedAt: .now),
            LocalSet(sessionId: sid, variantId: 2, exerciseOrder: 2, setOrder: 2, weightKg: 20, reps: 10),  // not done
            LocalSet(sessionId: sid, variantId: 2, exerciseOrder: 2, setOrder: 3, weightKg: nil, reps: 12, completedAt: .now),  // bodyweight
        ]
        let start = Date(timeIntervalSince1970: 0)
        let s = WorkoutSummary.compute(name: "Upper", startedAt: start, endedAt: start.addingTimeInterval(3300), sets: sets)
        #expect(s.volumeKg == 1200)
        #expect(s.setCount == 5)
        #expect(s.exerciseCount == 2)
        #expect(s.duration == 3300)
    }
}

@MainActor
@Suite(.tags(.workout))
struct WorkoutServiceTests {
    private func loadedContext() throws -> ModelContext {
        let ctx = try makeContext()
        try SyncMerge.apply(try Fixture.decode(PullResponse.self, "sync_pull"), in: ctx)
        return ctx
    }

    @Test func startFromPlanPrefillsTargetsWithoutLoggingAnything() throws {
        let ctx = try loadedContext()
        let rows = try ctx.fetch(FetchDescriptor<CachedPlannedSession>())
        let row = try #require(rows.first { $0.kind == "lower" })
        let svc = WorkoutService(context: ctx)
        let session = try #require(svc.start(from: row, gymId: 4))
        let sets = svc.sets(of: session)
        let planned = row.item!.session!.exercises!.filter { $0.variantId != nil }.flatMap(\.sets)
        #expect(sets.count == planned.count)
        #expect(sets.allSatisfy { !$0.isCompleted })
        #expect(sets.first?.weightKg == planned.first?.targetWeight)
        #expect(sets.first?.prevReps != nil)
        #expect(session.isActive && session.plannedSessionId == row.id)
        #expect(session.gymId == 4)  // resolved from the plan's gym name when no gym is selected
        // nothing is pushable until a set is performed
        #expect(try Outbox.collect(in: ctx).isEmpty)
        // only one active workout at a time
        #expect(svc.start(from: row, gymId: 4)?.id == session.id)
    }

    @Test func completeEditFinishFlow() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = svc.startEmpty(gymId: nil)
        let variant = CachedVariant(id: 7, exerciseId: 1, name: "Bench Press @ Gym 3", gymId: 3, equipmentType: "barbell")
        ctx.insert(variant)
        svc.addExercise(variant, to: session)
        var sets = svc.sets(of: session)
        #expect(sets.count == 3)
        svc.setWeight(sets[0], 60, in: session)
        svc.setReps(sets[0], 8)
        #expect(sets[1].weightKg == 60)  // later unchecked sets follow the edit
        #expect(svc.toggleComplete(sets[0]))
        #expect(sets[0].dirty && sets[0].completedAt != nil)
        let summary = try #require(svc.finish(session))
        #expect(summary.setCount == 1)
        #expect(summary.volumeKg == 480)
        #expect(session.endedAt != nil && session.dirty)
        sets = svc.sets(of: session)
        #expect(sets.count == 1)  // unchecked sets are dropped
        let batch = try Outbox.collect(in: ctx)
        #expect(batch.sessions.count == 1 && batch.sets.count == 1)
        #expect(svc.activeSession() == nil)
    }

    @Test func finishingWithNothingPerformedDiscardsTheSession() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = svc.startEmpty(gymId: nil)
        #expect(svc.finish(session) == nil)
        #expect(try ctx.fetchCount(FetchDescriptor<LocalSession>()) == 0)
    }

    @Test func previousPerformanceComesFromLocalHistory() throws {
        let ctx = try loadedContext()
        let svc = WorkoutService(context: ctx)
        let pulled = try Fixture.decode(PullResponse.self, "sync_pull")
        let variantId = try #require(pulled.sets.first { $0.reps != nil }?.variantId)
        #expect(!svc.previousPerformance(variantId: variantId, excluding: nil).isEmpty)
        #expect(svc.previousPerformance(variantId: -1, excluding: nil).isEmpty)
    }

    private func threeExercises(_ svc: WorkoutService, _ ctx: ModelContext) -> LocalSession {
        let session = svc.startEmpty(gymId: nil)
        for (id, name) in [(1, "Squat"), (2, "Bench"), (3, "Row")] {
            let v = CachedVariant(id: id, exerciseId: id, name: name, gymId: 3, equipmentType: "barbell")
            ctx.insert(v)
            svc.addExercise(v, to: session, setCount: 2)
        }
        return session
    }

    @Test func movingExercisesRenumbersAndMarksSetsDirty() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = threeExercises(svc, ctx)
        for s in svc.sets(of: session) { s.dirty = false; s.completedAt = .now }  // as if pushed
        svc.moveExercises(in: session, orders: [3, 1, 2])
        let sets = svc.sets(of: session)
        #expect(sets.map(\.variantId) == [3, 3, 1, 1, 2, 2])
        #expect(sets.map(\.exerciseOrder) == [1, 1, 2, 2, 3, 3])
        #expect(sets.map(\.setOrder) == [1, 2, 1, 2, 1, 2])
        #expect(sets.allSatisfy { $0.dirty })
        #expect(try Outbox.collect(in: ctx).sets.count == 6)
        // an exercise missing from the list keeps its place at the end
        svc.moveExercises(in: session, orders: [3, 2])  // order 1 (missing) goes last
        #expect(svc.sets(of: session).map(\.variantId) == [2, 2, 1, 1, 3, 3])
    }

    @Test func movingToTheSameOrderChangesNothing() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = threeExercises(svc, ctx)
        for s in svc.sets(of: session) { s.dirty = false }
        svc.moveExercises(in: session, orders: [1, 2, 3])
        #expect(svc.sets(of: session).allSatisfy { !$0.dirty })
    }

    @Test func deletingAnExerciseRemovesAllItsSets() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = threeExercises(svc, ctx)
        let sets = svc.sets(of: session)
        sets.filter { $0.exerciseOrder == 1 }.forEach { $0.hasBeenPushed = true }  // server knows these: tombstones
        svc.deleteExercise(in: session, order: 1)
        svc.deleteExercise(in: session, order: 2)  // never pushed: just vanishes
        #expect(svc.sets(of: session).map(\.variantId) == [3, 3])
        let all = try ctx.fetch(FetchDescriptor<LocalSet>())
        #expect(all.count == 4)
        #expect(all.filter { $0.deletedAt != nil && $0.dirty }.count == 2)
    }
}

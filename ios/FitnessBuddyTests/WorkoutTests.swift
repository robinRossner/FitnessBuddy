@testable import FitnessCore
import Foundation
import SwiftData
import Testing
@testable import FitnessBuddy

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

    @Test func hiddenLastSessionPrefillsTargetsAndNeverLastNumbers() throws {
        let ctx = try loadedContext()
        let defaults = try #require(UserDefaults(suiteName: "WorkoutTests-\(UUID().uuidString)"))
        defaults.set(false, forKey: LastSession.defaultsKey)
        let row = try #require(try ctx.fetch(FetchDescriptor<CachedPlannedSession>()).first { $0.kind == "lower" })
        let svc = WorkoutService(context: ctx, defaults: defaults)
        let session = try #require(svc.start(from: row, gymId: 4))
        let sets = svc.sets(of: session)
        let withTarget = sets.filter { $0.targetWeightKg != nil }
        #expect(!withTarget.isEmpty)
        #expect(withTarget.allSatisfy { $0.weightKg == $0.targetWeightKg })
        #expect(sets.contains { $0.prevReps != nil })  // still stored for the ticked-without-values fallback

        // an exercise with history but no plan target: no weight from last session, reps at the top of the range
        let variant = CachedVariant(id: 9001, exerciseId: 1, name: "Test Curl", gymId: 3, equipmentType: "dumbbell",
                                    repRangeLow: 8, repRangeHigh: 12)
        ctx.insert(variant)
        let old = LocalSession(gymId: nil, kind: "other", name: "Old")
        old.endedAt = .now.addingTimeInterval(-86_400)
        ctx.insert(old)
        let done = LocalSet(sessionId: old.id, variantId: 9001, exerciseOrder: 1, setOrder: 1, weightKg: 20, reps: 9)
        ctx.insert(done)
        svc.addExercise(variant, to: session)
        let added = svc.sets(of: session).filter { $0.variantId == 9001 }
        #expect(added.count == 1)
        #expect(added[0].weightKg == nil && added[0].reps == 12)
        #expect(added[0].prevWeightKg == 20 && added[0].prevReps == 9)

        // setting on: unchanged behaviour
        let shown = WorkoutService(context: ctx)
        shown.addExercise(variant, to: session)
        let second = svc.sets(of: session).filter { $0.variantId == 9001 && $0.exerciseOrder != added[0].exerciseOrder }
        #expect(second.first?.weightKg == 20 && second.first?.reps == 9)
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

    @Test func typeChangeMovesOnlyUncompletedSetsToTheNewVariant() throws {
        let ctx = try makeContext()
        let svc = WorkoutService(context: ctx)
        let session = svc.startEmpty(gymId: nil)
        let old = CachedVariant(id: 7, exerciseId: 1, name: "Curl (Cable) @ Gym 3", gymId: 3, equipmentType: "cable")
        let new = CachedVariant(id: 8, exerciseId: 1, name: "Curl (Machine) @ Gym 3", gymId: 3, equipmentType: "machine")
        ctx.insert(old)
        ctx.insert(new)
        svc.addExercise(old, to: session)
        let first = try #require(svc.sets(of: session).first)
        svc.setWeight(first, 20, in: session)
        svc.setReps(first, 10)
        #expect(svc.toggleComplete(first))
        svc.replaceVariant(in: session, from: 7, to: 8)
        let sets = svc.sets(of: session)
        #expect(sets.map(\.variantId) == [7, 8, 8])
        #expect(sets[0].variantName == "Curl (Cable) @ Gym 3")
        #expect(sets[1].variantName == "Curl (Machine) @ Gym 3")
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

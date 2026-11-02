import Foundation
import Testing
@testable import FitnessCore

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

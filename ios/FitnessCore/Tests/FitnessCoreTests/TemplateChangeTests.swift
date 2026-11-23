import Foundation
import Testing
@testable import FitnessCore

/// Finish offers "update template" only when the exercise identity/order differs from the planned template.
@Suite(.tags(.workout, .fast))
struct TemplateChangeTests {
    private func ex(_ id: Int, _ sets: Int) -> TemplateExercise { TemplateExercise(variantId: id, sets: sets) }
    private let template = [TemplateExercise(variantId: 1, sets: 3), TemplateExercise(variantId: 2, sets: 4), TemplateExercise(variantId: 3, sets: 2)]

    @Test func sameExercisesAndSetCountChangesAreNotAChange() {
        #expect(TemplateChange.newTemplate(template: template, performed: [ex(1, 5), ex(2, 1), ex(3, 2)]) == nil)
    }

    @Test func swapKeepsTemplateSetsForKnownAndUsesPerformedForNew() {
        let r = TemplateChange.newTemplate(template: template, performed: [ex(1, 3), ex(9, 4), ex(3, 2)])
        #expect(r == [ex(1, 3), ex(9, 4), ex(3, 2)])
    }

    @Test func addedRemovedAndReordered() {
        #expect(TemplateChange.newTemplate(template: template, performed: [ex(1, 3), ex(2, 3), ex(3, 2), ex(7, 2)])
                == [ex(1, 3), ex(2, 4), ex(3, 2), ex(7, 2)])
        #expect(TemplateChange.newTemplate(template: template, performed: [ex(1, 3), ex(3, 2)]) == [ex(1, 3), ex(3, 2)])
        #expect(TemplateChange.newTemplate(template: template, performed: [ex(3, 2), ex(1, 3), ex(2, 4)]) == [ex(3, 2), ex(1, 3), ex(2, 4)])
    }

    @Test func emptyPerformedIsNotOffered() {
        #expect(TemplateChange.newTemplate(template: template, performed: []) == nil)
    }

    private func planned(_ id: Int?, name: String = "X", sets: Int = 3, templateSets: Int? = nil, slot: String? = nil,
                         substituteFor: String? = nil) -> PlannedExerciseDTO {
        PlannedExerciseDTO(order: nil, variantId: id, name: name,
                           sets: (0..<sets).map { PlannedSetDTO(index: $0, setType: "working", targetWeight: 50, targetReps: 8) },
                           slot: slot, templateSets: templateSets, substituteFor: substituteFor)
    }

    @Test func baseSetCountUsedDuringACutWeek() {
        let t = TemplateChange.templateExercises(of: [planned(1, sets: 2, templateSets: 3), planned(2, sets: 4)])
        #expect(t == [TemplateExercise(variantId: 1, sets: 3), TemplateExercise(variantId: 2, sets: 4)])
    }

    @Test func substituteKeepsSlotAndDoneAsPlannedIsNoChange() {
        let t = TemplateChange.templateExercises(of: [planned(1), planned(5, templateSets: 3, substituteFor: "Pulldown")])
        #expect(t[1] == TemplateExercise(variantId: 5, sets: 3, slot: "Pulldown"))
        let done = [ex(1, 3), TemplateExercise(variantId: 5, sets: 2, slot: "Pulldown")]
        #expect(TemplateChange.newTemplate(template: t, performed: done) == nil)
    }

    @Test func swappedSubstituteIsAChangeWithoutSlot() {
        let t = [ex(1, 3), TemplateExercise(variantId: 5, sets: 3, slot: "Pulldown")]
        #expect(TemplateChange.newTemplate(template: t, performed: [ex(1, 3), ex(9, 3)]) == [ex(1, 3), ex(9, 3)])
    }

    @Test func unresolvedSlotKeptInPositionWhenAnotherIsRemoved() {
        let t = TemplateChange.templateExercises(of: [planned(1), planned(nil, name: "Row", slot: "Row"), planned(3)])
        #expect(t[1] == TemplateExercise(variantId: nil, sets: 3, slot: "Row"))
        #expect(TemplateChange.newTemplate(template: t, performed: [ex(1, 3), ex(3, 3)]) == nil)
        let r = TemplateChange.newTemplate(template: t, performed: [ex(3, 3)])
        #expect(r?.count == 2)
        #expect(r?.contains(t[1]) == true && r?.first(where: { $0.variantId != nil }) == ex(3, 3))
    }

    @Test func jsonOmitsNilVariantAndSlot() throws {
        let a = String(decoding: try JSONEncoder().encode(TemplateExercise(variantId: nil, sets: 3, slot: "Row")), as: UTF8.self)
        #expect(!a.contains("variant_id") && a.contains("\"slot\""))
        let b = String(decoding: try JSONEncoder().encode(ex(1, 3)), as: UTF8.self)
        #expect(!b.contains("slot") && b.contains("variant_id"))
    }
}

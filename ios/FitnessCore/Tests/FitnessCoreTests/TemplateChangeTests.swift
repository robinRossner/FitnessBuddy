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
}

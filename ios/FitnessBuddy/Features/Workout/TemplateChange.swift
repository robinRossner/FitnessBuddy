import FitnessCore
import Foundation
import SwiftData

extension TemplateChange {
    /// Exercise groups by exerciseOrder. Only removed exercises leave the structure: unticked sets don't,
    /// so a workout cut short (finish and drop unchecked sets) doesn't read as "removed the rest".
    static func performed(sets: [LocalSet]) -> [TemplateExercise] {
        let kept = sets.filter { $0.deletedAt == nil }
        return Dictionary(grouping: kept, by: \.exerciseOrder).sorted { $0.key < $1.key }
            .compactMap { _, group in group.first.map { TemplateExercise(variantId: $0.variantId, sets: group.count, slot: $0.slotName) } }
    }

    /// The new template for a running workout, or nil when it should not be offered.
    static func offer(for session: LocalSession, sets: [LocalSet], in ctx: ModelContext) -> [TemplateExercise]? {
        guard let plannedId = session.plannedSessionId, ["upper", "lower"].contains(session.kind) else { return nil }
        let row = try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == plannedId })).first
        guard let exercises = row?.item?.session?.exercises else { return nil }
        let template = templateExercises(of: exercises)
        guard !template.isEmpty else { return nil }
        return newTemplate(template: template, performed: performed(sets: sets))
    }
}

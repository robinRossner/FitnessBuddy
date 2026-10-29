import Foundation
import SwiftData

/// One exercise of a program template as sent to `PUT /api/v1/program/{kind}`.
struct TemplateExercise: Codable, Sendable, Equatable {
    var variantId: Int
    var sets: Int

    enum CodingKeys: String, CodingKey { case variantId = "variant_id", sets }
}

/// Body of `PUT /api/v1/program/{kind}`; `exercises` in performed order.
struct TemplateUpdateRequest: Codable, Sendable, Equatable {
    var exercises: [TemplateExercise]
}

/// A template update waiting to be sent (latest per kind wins).
struct PendingTemplateUpdate: Codable, Sendable, Equatable {
    var kind: String
    var exercises: [TemplateExercise]
}

/// Detects whether a finished workout changed the exercise structure of the planned session it started from.
enum TemplateChange {
    /// The structure that would become the new template, or nil when the exercises are the same and in the same
    /// order as the template (set-count differences alone do not count).
    /// - `performed`: one entry per exercise group in exercise order (variant id, number of sets that are kept).
    static func newTemplate(template: [TemplateExercise], performed: [TemplateExercise]) -> [TemplateExercise]? {
        var merged: [TemplateExercise] = []
        for p in performed where p.sets > 0 {
            if let i = merged.firstIndex(where: { $0.variantId == p.variantId }) { merged[i].sets += p.sets }
            else { merged.append(p) }
        }
        guard !merged.isEmpty, merged.map(\.variantId) != template.map(\.variantId) else { return nil }
        return merged.map { p in
            TemplateExercise(variantId: p.variantId, sets: template.first { $0.variantId == p.variantId }?.sets ?? max(1, p.sets))
        }
    }

    /// Template exercises of a planned session (unresolved ones without a variant are skipped, as at start).
    static func templateExercises(of planned: [PlannedExerciseDTO]) -> [TemplateExercise] {
        planned.compactMap { ex in ex.variantId.map { TemplateExercise(variantId: $0, sets: ex.sets.count) } }
    }

    /// Performed groups by exerciseOrder; `dropUnchecked` mirrors what Finish would keep.
    static func performed(sets: [LocalSet], dropUnchecked: Bool) -> [TemplateExercise] {
        let kept = sets.filter { $0.deletedAt == nil && (!dropUnchecked || $0.isCompleted) }
        return Dictionary(grouping: kept, by: \.exerciseOrder).sorted { $0.key < $1.key }
            .compactMap { _, group in group.first.map { TemplateExercise(variantId: $0.variantId, sets: group.count) } }
    }

    /// The new template for a running workout, or nil when it should not be offered.
    static func offer(for session: LocalSession, sets: [LocalSet], dropUnchecked: Bool, in ctx: ModelContext) -> [TemplateExercise]? {
        guard let plannedId = session.plannedSessionId, ["upper", "lower"].contains(session.kind) else { return nil }
        let row = try? ctx.fetch(FetchDescriptor<CachedPlannedSession>(predicate: #Predicate { $0.id == plannedId })).first
        guard let exercises = row?.item?.session?.exercises else { return nil }
        let template = templateExercises(of: exercises)
        guard !template.isEmpty else { return nil }
        return newTemplate(template: template, performed: performed(sets: sets, dropUnchecked: dropUnchecked))
    }
}

/// Template updates that have not reached the server yet, kept in UserDefaults so they survive offline/relaunch.
/// Sent by the sync engine before the plan refresh, so the new structure shows up right after.
enum TemplateSync {
    private static let key = "pendingTemplateUpdates"

    static func pending(_ defaults: UserDefaults = .standard) -> [PendingTemplateUpdate] {
        defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([PendingTemplateUpdate].self, from: $0) } ?? []
    }

    static func enqueue(kind: String, exercises: [TemplateExercise], _ defaults: UserDefaults = .standard) {
        var all = pending(defaults).filter { $0.kind != kind }
        all.append(PendingTemplateUpdate(kind: kind, exercises: exercises))
        store(all, defaults)
    }

    private static func store(_ all: [PendingTemplateUpdate], _ defaults: UserDefaults) {
        defaults.set(try? JSONEncoder().encode(all), forKey: key)
    }

    /// Transient failures throw (the sync retries with backoff); a rejection (404/422) drops the update.
    @discardableResult
    static func push(using client: APIClient, _ defaults: UserDefaults = .standard) async throws -> Int {
        var sent = 0
        for item in pending(defaults) {
            try Task.checkCancellation()
            do { try await client.updateProgram(kind: item.kind, exercises: item.exercises) }
            catch let error as APIError {
                guard case .http(let code, _) = error, code == 404 || code == 422 else { throw error }
            }
            store(pending(defaults).filter { $0 != item }, defaults)
            sent += 1
        }
        return sent
    }
}

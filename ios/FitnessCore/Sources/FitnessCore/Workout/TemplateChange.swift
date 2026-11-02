import Foundation

/// One exercise of a program template as sent to `PUT /api/v1/program/{kind}`.
public struct TemplateExercise: Codable, Sendable, Equatable {
    public var variantId: Int
    public var sets: Int

    public enum CodingKeys: String, CodingKey, Sendable { case variantId = "variant_id", sets }

    public init(variantId: Int, sets: Int) { self.variantId = variantId; self.sets = sets }
}

/// Body of `PUT /api/v1/program/{kind}`; `exercises` in performed order.
public struct TemplateUpdateRequest: Codable, Sendable, Equatable {
    public var exercises: [TemplateExercise]
}

/// A template update waiting to be sent (latest per kind wins).
public struct PendingTemplateUpdate: Codable, Sendable, Equatable {
    public var kind: String
    public var exercises: [TemplateExercise]
}

/// Detects whether a finished workout changed the exercise structure of the planned session it started from.
public enum TemplateChange: Sendable {
    /// The structure that would become the new template, or nil when the exercises are the same and in the same
    /// order as the template (set-count differences alone do not count).
    /// - `performed`: one entry per exercise group in exercise order (variant id, number of sets that are kept).
    public static func newTemplate(template: [TemplateExercise], performed: [TemplateExercise]) -> [TemplateExercise]? {
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
    public static func templateExercises(of planned: [PlannedExerciseDTO]) -> [TemplateExercise] {
        planned.compactMap { ex in ex.variantId.map { TemplateExercise(variantId: $0, sets: ex.sets.count) } }
    }
}

/// Template updates that have not reached the server yet, kept in UserDefaults so they survive offline/relaunch.
/// Sent by the sync engine before the plan refresh, so the new structure shows up right after.
public enum TemplateSync: Sendable {
    private static let key = "pendingTemplateUpdates"

    public static func pending(_ defaults: UserDefaults = .standard) -> [PendingTemplateUpdate] {
        defaults.data(forKey: key).flatMap { try? JSONDecoder().decode([PendingTemplateUpdate].self, from: $0) } ?? []
    }

    public static func enqueue(kind: String, exercises: [TemplateExercise], _ defaults: UserDefaults = .standard) {
        var all = pending(defaults).filter { $0.kind != kind }
        all.append(PendingTemplateUpdate(kind: kind, exercises: exercises))
        store(all, defaults)
    }

    private static func store(_ all: [PendingTemplateUpdate], _ defaults: UserDefaults) {
        defaults.set(try? JSONEncoder().encode(all), forKey: key)
    }

    /// Transient failures throw (the sync retries with backoff); a rejection (404/422) drops the update.
    @discardableResult
    public static func push(using client: APIClient, _ defaults: UserDefaults = .standard) async throws -> Int {
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

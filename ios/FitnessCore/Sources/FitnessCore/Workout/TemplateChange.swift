import Foundation

/// One exercise of a program template as sent to `PUT /api/v1/program/{kind}`.
public struct TemplateExercise: Codable, Sendable, Equatable {
    /// Nil for a slot with no usable variant at this gym (the server keeps its default).
    public var variantId: Int?
    /// The program slot (its default variant name); when it names a current program entry the server keeps that entry.
    public var slot: String?
    public var sets: Int

    public enum CodingKeys: String, CodingKey, Sendable { case variantId = "variant_id", slot, sets }

    public init(variantId: Int?, sets: Int, slot: String? = nil) {
        self.variantId = variantId; self.sets = sets; self.slot = slot
    }

    /// Identity of the program slot this entry stands for.
    var key: String { slot ?? "id:\(variantId.map(String.init) ?? "nil")" }
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
    /// order as the template (set-count differences alone do not count). Entries are identified by slot (a
    /// substitute done as planned is unchanged) or else by variant id.
    /// - `performed`: one entry per exercise group in exercise order (variant id, slot, number of sets kept).
    public static func newTemplate(template: [TemplateExercise], performed: [TemplateExercise]) -> [TemplateExercise]? {
        var merged: [TemplateExercise] = []
        for p in performed where p.sets > 0 {
            if let i = merged.firstIndex(where: { $0.key == p.key }) { merged[i].sets += p.sets }
            else { merged.append(p) }
        }
        guard !merged.isEmpty else { return nil }
        let performedKeys = Set(merged.map(\.key))
        // Unresolved template entries nobody performed stay where they are; they are not removals.
        let kept = template.enumerated().filter { $0.element.variantId == nil && !performedKeys.contains($0.element.key) }
        let comparable = template.filter { t in !kept.contains { $0.element.key == t.key } }
        guard merged.map(\.key) != comparable.map(\.key) else { return nil }
        var result = merged.map { p -> TemplateExercise in
            guard let t = template.first(where: { $0.key == p.key }) else {
                return TemplateExercise(variantId: p.variantId, sets: max(1, p.sets), slot: p.slot)
            }
            return TemplateExercise(variantId: p.variantId, sets: t.sets, slot: t.slot)
        }
        for (i, t) in kept { result.insert(t, at: min(i, result.count)) }
        return result
    }

    /// Template exercises of a planned session: base set counts (not the cut ones), substitutes keep their slot,
    /// unresolved items stay as variant-less slots.
    public static func templateExercises(of planned: [PlannedExerciseDTO]) -> [TemplateExercise] {
        planned.map { ex in
            let sets = ex.templateSets ?? ex.sets.count
            guard let id = ex.variantId else { return TemplateExercise(variantId: nil, sets: sets, slot: ex.slot ?? ex.name) }
            return TemplateExercise(variantId: id, sets: sets, slot: ex.substituteFor)
        }
    }
}

/// Template updates that have not reached the server yet, kept in UserDefaults so they survive offline/relaunch.
/// Sent by the sync engine before the plan refresh, so the new structure shows up right after.
public enum TemplateSync: Sendable {
    public static let key = "pendingTemplateUpdates"

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
    public static func push(using client: any APITransport, _ defaults: UserDefaults = .standard) async throws -> Int {
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

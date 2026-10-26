import Foundation
import SwiftData

/// Muscles, body regions and equipment types as the backend names them (docs/api/exercises.md).
enum ExerciseOptions {
    static let regions = ["Chest", "Back", "Shoulders", "Arms", "Legs", "Core", "Other"]

    /// (key, label, region), in picker order.
    static let muscles: [(key: String, label: String, region: String)] = [
        ("chest", "Chest", "Chest"),
        ("lats", "Lats", "Back"), ("back", "Upper back", "Back"), ("traps", "Traps", "Back"),
        ("shoulders", "Shoulders", "Shoulders"), ("rear_delts", "Rear delts", "Shoulders"),
        ("biceps", "Biceps", "Arms"), ("triceps", "Triceps", "Arms"), ("forearms", "Forearms", "Arms"),
        ("quads", "Quads", "Legs"), ("hamstrings", "Hamstrings", "Legs"), ("glutes", "Glutes", "Legs"),
        ("adductors", "Adductors", "Legs"), ("calves", "Calves", "Legs"),
        ("abs", "Abs", "Core"),
    ]

    /// (key, label), in picker order.
    static let equipment: [(key: String, label: String)] = [
        ("machine_stack", "Machine (stack)"), ("plate_loaded", "Machine (plate loaded)"), ("cable", "Cable"),
        ("smith", "Smith machine"), ("barbell", "Barbell"), ("dumbbell", "Dumbbell"),
        ("bodyweight", "Bodyweight"), ("other", "Other"),
    ]

    /// Machines belong to one gym (their own variant per gym); the rest are the same everywhere.
    static let gymBound: Set<String> = ["machine_stack", "plate_loaded", "cable", "smith"]

    /// Load steps offered in the editor (kg).
    static let increments: [Double] = [0.5, 1, 1.25, 2, 2.5, 4, 5, 7.5, 10]

    static func region(ofMuscle muscle: String?) -> String {
        muscles.first { $0.key == muscle }?.region ?? "Other"
    }

    static func muscleLabel(_ key: String?) -> String {
        muscles.first { $0.key == key }?.label ?? (key ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    }

    static func equipmentLabel(_ key: String?) -> String {
        equipment.first { $0.key == key }?.label ?? (key ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    }
}

/// One line of the exercise picker: a variant usable at the current gym, or an exercise that has none here yet
/// (picking it sets one up first).
struct ExercisePickRow: Identifiable, Equatable {
    var exerciseId: Int
    var exerciseName: String
    /// nil: the exercise needs a variant at this gym first.
    var variantId: Int?
    var title: String
    var equipmentType: String?

    var id: String { variantId.map { "v\($0)" } ?? "e\(exerciseId)" }
}

struct ExercisePickGroup: Identifiable, Equatable {
    var region: String
    var rows: [ExercisePickRow]
    var id: String { region }
}

enum ExerciseCatalog {
    /// Picker sections by body region (fixed order), rows alphabetical. A variant is usable when it is active, not
    /// tombstoned, and generic or at `gymId`. Every exercise without a usable variant gets one "set up" row, so
    /// exercises seen at another gym (or never) can still be added.
    static func groups(exercises: [CachedExercise], variants: [CachedVariant], gymId: Int?, search: String = "") -> [ExercisePickGroup] {
        let byId = Dictionary(exercises.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let usable = variants.filter { $0.active && $0.deletedAt == nil && (gymId == nil || $0.gymId == nil || $0.gymId == gymId) }
        var rows: [(region: String, row: ExercisePickRow)] = []
        var covered = Set<Int>()
        for v in usable {
            let ex = byId[v.exerciseId]
            covered.insert(v.exerciseId)
            rows.append((region(ex), ExercisePickRow(exerciseId: v.exerciseId, exerciseName: ex?.name ?? v.name.withoutGymSuffix,
                                                     variantId: v.id, title: v.name.withoutGymSuffix, equipmentType: v.equipmentType)))
        }
        for ex in exercises where !covered.contains(ex.id) && ex.movementPattern != "cardio" {
            rows.append((region(ex), ExercisePickRow(exerciseId: ex.id, exerciseName: ex.name, variantId: nil, title: ex.name,
                                                     equipmentType: nil)))
        }
        let query = search.trimmingCharacters(in: .whitespaces)
        if !query.isEmpty {
            rows = rows.filter { $0.row.title.localizedCaseInsensitiveContains(query) || $0.row.exerciseName.localizedCaseInsensitiveContains(query) }
        }
        let grouped = Dictionary(grouping: rows, by: { $0.region })
        return ExerciseOptions.regions.compactMap { region in
            guard let items = grouped[region], !items.isEmpty else { return nil }
            let sorted = items.map { $0.row }.sorted {
                let a = $0.title.localizedCaseInsensitiveCompare($1.title)
                return a == .orderedSame ? ($0.variantId ?? 0) < ($1.variantId ?? 0) : a == .orderedAscending
            }
            return ExercisePickGroup(region: region, rows: sorted)
        }
    }

    static func region(_ ex: CachedExercise?) -> String {
        guard let ex else { return "Other" }
        if let r = ex.bodyRegion, ExerciseOptions.regions.contains(r) { return r }
        return ExerciseOptions.region(ofMuscle: ex.muscleGroup)
    }
}

// MARK: - Server writes

enum ExerciseEndpoints {
    static let exercises = "/api/v1/exercises"
    static func exercise(_ id: Int) -> String { exercises + "/\(id)" }
    static func variants(ofExercise id: Int) -> String { exercise(id) + "/variants" }
    static func variant(_ id: Int) -> String { "/api/v1/variants/\(id)" }
}

/// What the "New exercise" form sends.
struct NewExerciseDraft: Equatable {
    var name = ""
    var muscleGroup = "chest"
    var equipmentType = "machine_stack"
    var timed = false
    var incrementKg: Double?

    var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    var isValid: Bool { !trimmedName.isEmpty }
}

struct ExerciseCreateRequest: Encodable, Sendable {
    var name: String
    var muscleGroup: String
    var equipmentType: String
    var timed: Bool
    var gymId: Int?
    var incrementKg: Double?
}

struct ExerciseCreateResponse: Decodable, Sendable {
    var exercise: ExerciseDTO
    var variant: VariantDTO
}

struct VariantAddRequest: Encodable, Sendable {
    var equipmentType: String
    var gymId: Int?
}

struct ExercisePatchRequest: Encodable, Sendable {
    var muscleGroup: String
}

/// `PATCH /variants/{id}` with the equipment type and the load step; `increment_kg: null` resets the step, so the
/// key is always written.
struct VariantSettingsPatch: Encodable, Sendable, Equatable {
    var equipmentType: String
    var incrementKg: Double?

    enum CodingKeys: String, CodingKey { case equipmentType, incrementKg }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(equipmentType, forKey: .equipmentType)
        if let incrementKg { try c.encode(incrementKg, forKey: .incrementKg) } else { try c.encodeNil(forKey: .incrementKg) }
    }
}

/// The variant fields `PATCH /variants/{id}` answers with.
struct VariantPatchResponse: Decodable, Sendable {
    var id: Int
    var equipmentType: String?
    var incrementKg: Double?
}

/// Creates and edits exercises on the server, then mirrors the answer into the local cache at once (the next pull
/// confirms it). Writes need a connection and are never queued, like the gym writes.
@MainActor
struct ExerciseLibrary {
    let settings: SettingsStore
    let network: NetworkMonitor
    let context: ModelContext

    var canWrite: Bool { settings.isConfigured && network.isConnected }

    private func send(_ method: String, _ path: String, _ body: some Encodable) async throws -> Data {
        guard settings.isConfigured, let client = settings.makeClient() else { throw GymError.notConfigured }
        guard network.isConnected else { throw GymError.needsConnection }
        let data = try APICoding.makeEncoder().encode(body)
        do { return try await client.perform(method: method, path: path, body: data) }
        catch { throw GymError.map(error) }
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try APICoding.makeDecoder().decode(T.self, from: data) }
        catch { throw GymError.failed(APIError.message(for: APIError.decoding(error))) }
    }

    private func cachedVariant(_ id: Int) -> CachedVariant? {
        try? context.fetch(FetchDescriptor<CachedVariant>(predicate: #Predicate { $0.id == id })).first
    }

    private func cachedExercise(_ id: Int) -> CachedExercise? {
        try? context.fetch(FetchDescriptor<CachedExercise>(predicate: #Predicate { $0.id == id })).first
    }

    /// A brand-new exercise with its first variant (machines at `gymId`). Returns the variant to log.
    func create(_ draft: NewExerciseDraft, gymId: Int?) async throws -> CachedVariant {
        let body = ExerciseCreateRequest(name: draft.trimmedName, muscleGroup: draft.muscleGroup, equipmentType: draft.equipmentType,
                                         timed: draft.timed, gymId: gymId, incrementKg: draft.incrementKg)
        let response = try decode(ExerciseCreateResponse.self, try await send("POST", ExerciseEndpoints.exercises, body))
        SyncMerge.upsert(exercise: response.exercise, existing: cachedExercise(response.exercise.id), in: context)
        let variant = SyncMerge.upsert(variant: response.variant, existing: cachedVariant(response.variant.id), in: context)
        try? context.save()
        return variant
    }

    /// The variant of an existing exercise with this equipment (set up at `gymId` for machines). Idempotent.
    func addVariant(exerciseId: Int, equipmentType: String, gymId: Int?) async throws -> CachedVariant {
        let body = VariantAddRequest(equipmentType: equipmentType, gymId: ExerciseOptions.gymBound.contains(equipmentType) ? gymId : nil)
        let dto = try decode(VariantDTO.self, try await send("POST", ExerciseEndpoints.variants(ofExercise: exerciseId), body))
        let variant = SyncMerge.upsert(variant: dto, existing: cachedVariant(dto.id), in: context)
        try? context.save()
        return variant
    }

    func setMuscle(exerciseId: Int, muscle: String) async throws {
        let dto = try decode(ExerciseDTO.self, try await send("PATCH", ExerciseEndpoints.exercise(exerciseId), ExercisePatchRequest(muscleGroup: muscle)))
        SyncMerge.upsert(exercise: dto, existing: cachedExercise(dto.id), in: context)
        try? context.save()
    }

    func setVariant(_ variantId: Int, equipmentType: String, incrementKg: Double?) async throws {
        let body = VariantSettingsPatch(equipmentType: equipmentType, incrementKg: incrementKg)
        let dto = try decode(VariantPatchResponse.self, try await send("PATCH", ExerciseEndpoints.variant(variantId), body))
        if let v = cachedVariant(dto.id) {
            v.equipmentType = dto.equipmentType ?? equipmentType
            v.incrementKg = dto.incrementKg
            try? context.save()
        }
    }
}

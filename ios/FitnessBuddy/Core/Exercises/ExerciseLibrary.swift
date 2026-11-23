import FitnessCore
import Foundation
import SwiftData

/// Muscles, body regions and equipment types as the backend names them (docs/api/exercises.md).
enum ExerciseOptions {
    static let regions = ["Chest", "Back", "Shoulders", "Arms", "Legs", "Core", "Other"]

    /// (key, label, region), in picker order.
    static let muscles: [(key: String, label: String, region: String)] = [
        ("chest", "Chest", "Chest"),
        ("lats", "Lats", "Back"), ("back", "Upper back", "Back"), ("traps", "Traps", "Back"),
        ("shoulders", "Shoulders", "Shoulders"),
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
        muscles.first { $0.key == MuscleGroup.merged(muscle) }?.region ?? "Other"
    }

    static func muscleLabel(_ key: String?) -> String {
        muscles.first { $0.key == MuscleGroup.merged(key) }?.label ?? (key ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    }

    static func equipmentLabel(_ key: String?) -> String {
        equipment.first { $0.key == key }?.label ?? (key ?? "").replacingOccurrences(of: "_", with: " ").capitalized
    }

    /// The label the backend puts in brackets after an exercise name, "Pec Deck (Machine)"; nil: none (`other`).
    static func nameLabel(_ key: String?) -> String? {
        switch key {
        case "barbell": "Barbell"
        case "dumbbell": "Dumbbell"
        case "machine_stack": "Machine"
        case "plate_loaded": "Plate Loaded"
        case "cable": "Cable"
        case "bodyweight": "Bodyweight"
        case "smith": "Smith Machine"
        default: nil
        }
    }

    /// What the edit sheet offers as the name: the variant's name without the gym and without its own equipment
    /// label (the server adds the label again when it renames).
    static func editableName(variantName: String, equipmentType: String?) -> String {
        let base = variantName.withoutGymSuffix
        if let label = nameLabel(equipmentType), base.hasSuffix(" (\(label))"), base.count > label.count + 3 {
            return String(base.dropLast(label.count + 3))
        }
        return base
    }

    /// A typed name the way the server stores it: trimmed, inner whitespace collapsed.
    static func cleanName(_ name: String) -> String {
        name.split(whereSeparator: \.isWhitespace).joined(separator: " ")
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
        let searching = !search.trimmingCharacters(in: .whitespaces).isEmpty
        for ex in exercises where !covered.contains(ex.id) && (searching || ex.movementPattern != "cardio") {
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
    var progression: Progression = .load
    var incrementKg: Double?

    var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    var isValid: Bool { !trimmedName.isEmpty }
}

struct ExerciseCreateResponse: Decodable, Sendable {
    var exercise: ExerciseDTO
    var variant: VariantDTO
}

struct VariantAddRequest: Encodable, Sendable {
    var equipmentType: String
    var gymId: Int?
}

/// The exercise, plus the variants a rename gave a new name (absent otherwise).
struct ExerciseRenameResponse: Decodable, Sendable {
    var exercise: ExerciseDTO
    var variants: [VariantDTO]

    private enum CodingKeys: String, CodingKey { case variants }

    init(from decoder: Decoder) throws {
        exercise = try ExerciseDTO(from: decoder)
        variants = try decoder.container(keyedBy: CodingKeys.self).decodeIfPresent([VariantDTO].self, forKey: .variants) ?? []
    }
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
    var exerciseId: Int?
    var gymId: Int?
    var name: String?
    var equipmentType: String?
    var incrementKg: Double?
    var weightLadder: [Double]?
    var active: Bool?
    /// Set when the type changed: the answer is the new variant of that type and this one was retired (another
    /// type is another exercise, with its own history).
    var replacedVariantId: Int?
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

    /// A cached variant by id (nil: not synced yet).
    func variant(id: Int) -> CachedVariant? { cachedVariant(id) }

    /// A brand-new exercise with its first variant (machines at `gymId`). Returns the variant to log.
    func create(_ draft: NewExerciseDraft, gymId: Int?) async throws -> CachedVariant {
        let body = ExerciseCreateRequest(name: draft.trimmedName, muscleGroup: draft.muscleGroup, equipmentType: draft.equipmentType,
                                         timed: draft.progression == .time, progression: draft.progression,
                                         gymId: gymId, incrementKg: draft.incrementKg)
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

    /// Switches a non-timed exercise between weight and reps-only progression; the server re-runs the engine (422 for `time`).
    func setProgression(exerciseId: Int, to progression: Progression) async throws {
        let dto = try decode(ExerciseDTO.self, try await send("PATCH", ExerciseEndpoints.exercise(exerciseId),
                                                              ExercisePatchRequest(progression: progression)))
        SyncMerge.upsert(exercise: dto, existing: cachedExercise(dto.id), in: context)
        try? context.save()
    }

    /// Renames the exercise; the server gives its variants the new name too ("<name> (<type>) @ gym") and answers with
    /// them.
    func rename(exerciseId: Int, to name: String) async throws {
        let body = ExercisePatchRequest(name: ExerciseOptions.cleanName(name))
        let response = try decode(ExerciseRenameResponse.self, try await send("PATCH", ExerciseEndpoints.exercise(exerciseId), body))
        Self.apply(response, in: context)
    }

    /// Writes a rename answer into the cache. The sets of this device keep their own copy of the variant name, so
    /// they get the new one too.
    static func apply(_ response: ExerciseRenameResponse, in context: ModelContext) {
        let id = response.exercise.id
        let existing = try? context.fetch(FetchDescriptor<CachedExercise>(predicate: #Predicate { $0.id == id })).first
        SyncMerge.upsert(exercise: response.exercise, existing: existing, in: context)
        for dto in response.variants {
            let variantId = dto.id
            let cached = try? context.fetch(FetchDescriptor<CachedVariant>(predicate: #Predicate { $0.id == variantId })).first
            let variant = SyncMerge.upsert(variant: dto, existing: cached, in: context)
            let sets = (try? context.fetch(FetchDescriptor<LocalSet>(predicate: #Predicate { $0.variantId == variantId }))) ?? []
            for set in sets where set.variantName != variant.name { set.variantName = variant.name }
        }
        try? context.save()
    }

    /// Returns the variant the edit ended on: another one than `variantId` when the type changed (the old one is
    /// retired in the cache, the next pull confirms both).
    @discardableResult
    func setVariant(_ variantId: Int, equipmentType: String, incrementKg: Double?) async throws -> Int {
        let body = VariantSettingsPatch(equipmentType: equipmentType, incrementKg: incrementKg)
        let dto = try decode(VariantPatchResponse.self, try await send("PATCH", ExerciseEndpoints.variant(variantId), body))
        if let oldId = dto.replacedVariantId, let old = cachedVariant(oldId) { old.active = false }
        if let v = cachedVariant(dto.id) {
            v.equipmentType = dto.equipmentType ?? equipmentType
            v.incrementKg = dto.incrementKg
            if let active = dto.active { v.active = active }
            if let name = dto.name { v.name = name }
        } else if dto.replacedVariantId != nil, let exerciseId = dto.exerciseId, let name = dto.name {
            let old = cachedVariant(dto.replacedVariantId ?? 0)
            let v = CachedVariant(id: dto.id, exerciseId: exerciseId, name: name, gymId: dto.gymId,
                                  equipmentType: dto.equipmentType ?? equipmentType, loadConvention: old?.loadConvention ?? "total",
                                  active: dto.active ?? true, weightLadder: dto.weightLadder ?? [])
            v.incrementKg = dto.incrementKg
            context.insert(v)
        }
        try? context.save()
        return dto.id
    }
}

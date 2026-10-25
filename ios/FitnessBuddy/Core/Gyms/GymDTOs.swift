import Foundation

// Gym and machine management (server operations). All fields beyond the identifying ones are optional so an
// additive backend change never breaks decoding.

struct GymSummaryDTO: Codable, Sendable, Equatable, Identifiable {
    var id: Int
    var name: String
    var createdAt: Date?
    var machineCount: Int?
    var isCurrent: Bool?
    /// "import" (came with the history import) | "user" (added in the app).
    var source: String?

    init(id: Int, name: String, createdAt: Date? = nil, machineCount: Int? = nil, isCurrent: Bool? = nil, source: String? = nil) {
        self.id = id; self.name = name; self.createdAt = createdAt; self.machineCount = machineCount
        self.isCurrent = isCurrent; self.source = source
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        createdAt = (try? c.decodeIfPresent(Date.self, forKey: .createdAt)) ?? nil
        machineCount = (try? c.decodeIfPresent(Int.self, forKey: .machineCount)) ?? nil
        isCurrent = (try? c.decodeIfPresent(Bool.self, forKey: .isCurrent)) ?? nil
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? nil
    }
}

struct GymListDTO: Codable, Sendable, Equatable {
    var currentGymId: Int?
    var gyms: [GymSummaryDTO]

    init(currentGymId: Int?, gyms: [GymSummaryDTO]) { self.currentGymId = currentGymId; self.gyms = gyms }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        currentGymId = (try? c.decodeIfPresent(Int.self, forKey: .currentGymId)) ?? nil
        gyms = try c.decodeIfPresent([GymSummaryDTO].self, forKey: .gyms) ?? []
    }
}

/// The gym-specific variant (machine) of an exercise.
struct MachineVariantDTO: Codable, Sendable, Equatable {
    var id: Int
    var name: String
    var equipmentType: String?
    var weightLadder: [Double]?
    var notes: String?
    var active: Bool?
    var sessions: Int?
    var lastUsed: String?

    init(id: Int, name: String, equipmentType: String? = nil, weightLadder: [Double]? = nil, notes: String? = nil,
         active: Bool? = nil, sessions: Int? = nil, lastUsed: String? = nil) {
        self.id = id; self.name = name; self.equipmentType = equipmentType; self.weightLadder = weightLadder
        self.notes = notes; self.active = active; self.sessions = sessions; self.lastUsed = lastUsed
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        equipmentType = (try? c.decodeIfPresent(String.self, forKey: .equipmentType)) ?? nil
        weightLadder = (try? c.decodeIfPresent([Double].self, forKey: .weightLadder)) ?? nil
        notes = (try? c.decodeIfPresent(String.self, forKey: .notes)) ?? nil
        active = (try? c.decodeIfPresent(Bool.self, forKey: .active)) ?? nil
        sessions = (try? c.decodeIfPresent(Int.self, forKey: .sessions)) ?? nil
        lastUsed = (try? c.decodeIfPresent(String.self, forKey: .lastUsed)) ?? nil
    }

    var ladder: [Double] { weightLadder ?? [] }
}

struct MachineTopSetDTO: Codable, Sendable, Equatable {
    var weightKg: Double?
    var reps: Int?

    init(weightKg: Double?, reps: Int?) { self.weightKg = weightKg; self.reps = reps }
}

/// The same exercise at the previously used gym (a hint for the new machine).
struct PreviousMachineDTO: Codable, Sendable, Equatable {
    var gymName: String?
    var variantId: Int?
    var lastTopSet: MachineTopSetDTO?
    var weightLadder: [Double]?

    init(gymName: String?, variantId: Int? = nil, lastTopSet: MachineTopSetDTO? = nil, weightLadder: [Double]? = nil) {
        self.gymName = gymName; self.variantId = variantId; self.lastTopSet = lastTopSet; self.weightLadder = weightLadder
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        gymName = (try? c.decodeIfPresent(String.self, forKey: .gymName)) ?? nil
        variantId = (try? c.decodeIfPresent(Int.self, forKey: .variantId)) ?? nil
        lastTopSet = (try? c.decodeIfPresent(MachineTopSetDTO.self, forKey: .lastTopSet)) ?? nil
        weightLadder = (try? c.decodeIfPresent([Double].self, forKey: .weightLadder)) ?? nil
    }
}

struct MachineItemDTO: Codable, Sendable, Equatable, Identifiable {
    var exerciseId: Int
    var exerciseName: String
    var bodyRegion: String?
    var inProgram: Bool
    var equipmentType: String?
    var variant: MachineVariantDTO?
    var previous: PreviousMachineDTO?

    var id: Int { exerciseId }

    init(exerciseId: Int, exerciseName: String, bodyRegion: String? = nil, inProgram: Bool = false, equipmentType: String? = nil,
         variant: MachineVariantDTO? = nil, previous: PreviousMachineDTO? = nil) {
        self.exerciseId = exerciseId; self.exerciseName = exerciseName; self.bodyRegion = bodyRegion
        self.inProgram = inProgram; self.equipmentType = equipmentType; self.variant = variant; self.previous = previous
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        exerciseId = try c.decode(Int.self, forKey: .exerciseId)
        exerciseName = try c.decodeIfPresent(String.self, forKey: .exerciseName) ?? "Exercise"
        bodyRegion = (try? c.decodeIfPresent(String.self, forKey: .bodyRegion)) ?? nil
        inProgram = (try? c.decodeIfPresent(Bool.self, forKey: .inProgram)) ?? false
        equipmentType = (try? c.decodeIfPresent(String.self, forKey: .equipmentType)) ?? nil
        variant = (try? c.decodeIfPresent(MachineVariantDTO.self, forKey: .variant)) ?? nil
        previous = (try? c.decodeIfPresent(PreviousMachineDTO.self, forKey: .previous)) ?? nil
    }
}

struct GymMachinesDTO: Codable, Sendable, Equatable {
    var gym: GymSummaryDTO
    var items: [MachineItemDTO]

    init(gym: GymSummaryDTO, items: [MachineItemDTO]) { self.gym = gym; self.items = items }
}

// MARK: Requests / responses

struct GymNameRequest: Encodable, Sendable { var name: String }
struct GymCurrentRequest: Encodable, Sendable { var gymId: Int }

struct GymCurrentResponse: Codable, Sendable, Equatable {
    var currentGymId: Int?
    var unmappedCount: Int?
}

struct MachineCreateRequest: Encodable, Sendable, Equatable {
    var exerciseId: Int
    var name: String?
    var equipmentType: String
    var weightLadder: [Double]?
    var notes: String?
}

struct VariantPatchRequest: Encodable, Sendable, Equatable {
    var name: String?
    var weightLadder: [Double]?
    var notes: String?
    var active: Bool?
}

struct LadderPreviewRequest: Encodable, Sendable, Equatable {
    var start: Double
    var step: Double
    var count: Int
    var extra: [Double]?
}

struct LadderPreviewResponse: Codable, Sendable, Equatable {
    var weightLadder: [Double]
}

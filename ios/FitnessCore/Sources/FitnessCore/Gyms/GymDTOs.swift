import Foundation

// Gym and machine management (server operations). All fields beyond the identifying ones are optional so an
// additive backend change never breaks decoding.

public struct GymSummaryDTO: Codable, Sendable, Equatable, Identifiable {
    public var id: Int
    public var name: String
    public var createdAt: Date?
    public var machineCount: Int?
    public var isCurrent: Bool?
    /// "import" (came with the history import) | "user" (added in the app).
    public var source: String?

    public init(id: Int, name: String, createdAt: Date? = nil, machineCount: Int? = nil, isCurrent: Bool? = nil, source: String? = nil) {
        self.id = id; self.name = name; self.createdAt = createdAt; self.machineCount = machineCount
        self.isCurrent = isCurrent; self.source = source
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        createdAt = (try? c.decodeIfPresent(Date.self, forKey: .createdAt)) ?? nil
        machineCount = (try? c.decodeIfPresent(Int.self, forKey: .machineCount)) ?? nil
        isCurrent = (try? c.decodeIfPresent(Bool.self, forKey: .isCurrent)) ?? nil
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? nil
    }
}

public struct GymListDTO: Codable, Sendable, Equatable {
    public var currentGymId: Int?
    public var gyms: [GymSummaryDTO]

    public init(currentGymId: Int?, gyms: [GymSummaryDTO]) { self.currentGymId = currentGymId; self.gyms = gyms }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        currentGymId = (try? c.decodeIfPresent(Int.self, forKey: .currentGymId)) ?? nil
        gyms = try c.decodeIfPresent([GymSummaryDTO].self, forKey: .gyms) ?? []
    }
}

/// The gym-specific variant (machine) of an exercise.
public struct MachineVariantDTO: Codable, Sendable, Equatable {
    public var id: Int
    public var name: String
    public var equipmentType: String?
    public var weightLadder: [Double]?
    public var notes: String?
    public var active: Bool?
    public var sessions: Int?
    public var lastUsed: String?

    public init(id: Int, name: String, equipmentType: String? = nil, weightLadder: [Double]? = nil, notes: String? = nil,
         active: Bool? = nil, sessions: Int? = nil, lastUsed: String? = nil) {
        self.id = id; self.name = name; self.equipmentType = equipmentType; self.weightLadder = weightLadder
        self.notes = notes; self.active = active; self.sessions = sessions; self.lastUsed = lastUsed
    }

    public init(from decoder: Decoder) throws {
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

    public var ladder: [Double] { weightLadder ?? [] }
}

public struct MachineTopSetDTO: Codable, Sendable, Equatable {
    public var weightKg: Double?
    public var reps: Int?

    public init(weightKg: Double?, reps: Int?) { self.weightKg = weightKg; self.reps = reps }
}

/// The same exercise at the previously used gym (a hint for the new machine).
public struct PreviousMachineDTO: Codable, Sendable, Equatable {
    public var gymName: String?
    public var variantId: Int?
    public var lastTopSet: MachineTopSetDTO?
    public var weightLadder: [Double]?

    public init(gymName: String?, variantId: Int? = nil, lastTopSet: MachineTopSetDTO? = nil, weightLadder: [Double]? = nil) {
        self.gymName = gymName; self.variantId = variantId; self.lastTopSet = lastTopSet; self.weightLadder = weightLadder
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        gymName = (try? c.decodeIfPresent(String.self, forKey: .gymName)) ?? nil
        variantId = (try? c.decodeIfPresent(Int.self, forKey: .variantId)) ?? nil
        lastTopSet = (try? c.decodeIfPresent(MachineTopSetDTO.self, forKey: .lastTopSet)) ?? nil
        weightLadder = (try? c.decodeIfPresent([Double].self, forKey: .weightLadder)) ?? nil
    }
}

public struct MachineItemDTO: Codable, Sendable, Equatable, Identifiable {
    public var exerciseId: Int
    public var exerciseName: String
    public var bodyRegion: String?
    public var inProgram: Bool
    public var equipmentType: String?
    public var variant: MachineVariantDTO?
    public var previous: PreviousMachineDTO?

    public var id: Int { exerciseId }

    public init(exerciseId: Int, exerciseName: String, bodyRegion: String? = nil, inProgram: Bool = false, equipmentType: String? = nil,
         variant: MachineVariantDTO? = nil, previous: PreviousMachineDTO? = nil) {
        self.exerciseId = exerciseId; self.exerciseName = exerciseName; self.bodyRegion = bodyRegion
        self.inProgram = inProgram; self.equipmentType = equipmentType; self.variant = variant; self.previous = previous
    }

    public init(from decoder: Decoder) throws {
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

public struct GymMachinesDTO: Codable, Sendable, Equatable {
    public var gym: GymSummaryDTO
    public var items: [MachineItemDTO]

    public init(gym: GymSummaryDTO, items: [MachineItemDTO]) { self.gym = gym; self.items = items }
}

// MARK: Requests / responses

public struct GymNameRequest: Encodable, Sendable { public var name: String; public init(name: String) { self.name = name } }
public struct GymCurrentRequest: Encodable, Sendable { public var gymId: Int; public init(gymId: Int) { self.gymId = gymId } }

public struct GymCurrentResponse: Codable, Sendable, Equatable {
    public var currentGymId: Int?
    public var unmappedCount: Int?
}

public struct MachineCreateRequest: Encodable, Sendable, Equatable {
    public var exerciseId: Int
    public var name: String?
    public var equipmentType: String
    public var weightLadder: [Double]?
    public var notes: String?

    public init(exerciseId: Int, name: String? = nil, equipmentType: String, weightLadder: [Double]? = nil, notes: String? = nil) {
        self.exerciseId = exerciseId
        self.name = name
        self.equipmentType = equipmentType
        self.weightLadder = weightLadder
        self.notes = notes
    }
}

public struct VariantPatchRequest: Encodable, Sendable, Equatable {
    public var name: String?
    public var weightLadder: [Double]?
    public var notes: String?
    public var active: Bool?

    public init(name: String? = nil, weightLadder: [Double]? = nil, notes: String? = nil, active: Bool? = nil) {
        self.name = name
        self.weightLadder = weightLadder
        self.notes = notes
        self.active = active
    }
}

public struct LadderPreviewRequest: Encodable, Sendable, Equatable {
    public var start: Double
    public var step: Double
    public var count: Int
    public var extra: [Double]?

    public init(start: Double, step: Double, count: Int, extra: [Double]? = nil) {
        self.start = start
        self.step = step
        self.count = count
        self.extra = extra
    }
}

public struct LadderPreviewResponse: Codable, Sendable, Equatable {
    public var weightLadder: [Double]
}

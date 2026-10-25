import Foundation

// MARK: - Plan

struct SignalDTO: Codable, Sendable, Equatable {
    var name: String?
    var level: String?
    var detail: String?
    var value: Double?
    var baseline: Double?
}

struct ReadinessDTO: Codable, Sendable, Equatable {
    var date: String?
    /// "green" | "yellow" | "red" | nil (unknown)
    var color: String?
    var known: Bool?
    var reason: String?
    var signals: [SignalDTO]?

    var level: ReadinessLevel { ReadinessLevel(color: color, known: known ?? (color != nil)) }
}

struct PlannedSetDTO: Codable, Sendable, Equatable {
    var index: Int
    var setType: String
    var targetWeight: Double?
    var targetReps: Int?
    var targetDurationS: Double?

    init(index: Int, setType: String = "working", targetWeight: Double?, targetReps: Int?, targetDurationS: Double? = nil) {
        self.index = index
        self.setType = setType
        self.targetWeight = targetWeight
        self.targetReps = targetReps
        self.targetDurationS = targetDurationS
    }
}

struct LastSetDTO: Codable, Sendable, Equatable {
    var weight: Double?
    var reps: Int?
    var durationS: Double?
}

struct LastPerformanceDTO: Codable, Sendable, Equatable {
    var date: String?
    var sets: [LastSetDTO]
    var bestE1rm: Double?

    init(date: String?, sets: [LastSetDTO], bestE1rm: Double? = nil) {
        self.date = date; self.sets = sets; self.bestE1rm = bestE1rm
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decodeIfPresent(String.self, forKey: .date)
        sets = try c.decodeIfPresent([LastSetDTO].self, forKey: .sets) ?? []
        bestE1rm = try c.decodeIfPresent(Double.self, forKey: .bestE1rm)
    }
}

struct PlannedExerciseDTO: Codable, Sendable, Equatable {
    var order: Int?
    var variantId: Int?
    var variantKey: String?
    var name: String
    var exercise: String?
    var equipmentType: String?
    var loadConvention: String?
    var repRange: [Int]?
    var sets: [PlannedSetDTO]
    var last: LastPerformanceDTO?
    var rule: String?
    var reason: String?
    var flags: [String]
    var unresolved: Bool?

    init(order: Int?, variantId: Int?, name: String, equipmentType: String? = nil, repRange: [Int]? = nil,
         sets: [PlannedSetDTO], last: LastPerformanceDTO? = nil, reason: String? = nil, flags: [String] = []) {
        self.order = order; self.variantId = variantId; self.name = name; self.equipmentType = equipmentType
        self.repRange = repRange; self.sets = sets; self.last = last; self.reason = reason; self.flags = flags
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        order = try c.decodeIfPresent(Int.self, forKey: .order)
        variantId = try c.decodeIfPresent(Int.self, forKey: .variantId)
        variantKey = try c.decodeIfPresent(String.self, forKey: .variantKey)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "Exercise"
        exercise = try c.decodeIfPresent(String.self, forKey: .exercise)
        equipmentType = try c.decodeIfPresent(String.self, forKey: .equipmentType)
        loadConvention = try c.decodeIfPresent(String.self, forKey: .loadConvention)
        repRange = try c.decodeIfPresent([Int].self, forKey: .repRange)
        sets = try c.decodeIfPresent([PlannedSetDTO].self, forKey: .sets) ?? []
        last = try c.decodeIfPresent(LastPerformanceDTO.self, forKey: .last)
        rule = try c.decodeIfPresent(String.self, forKey: .rule)
        reason = try c.decodeIfPresent(String.self, forKey: .reason)
        flags = try c.decodeIfPresent([String].self, forKey: .flags) ?? []
        unresolved = try c.decodeIfPresent(Bool.self, forKey: .unresolved)
    }
}

struct HRRangeDTO: Codable, Sendable, Equatable {
    var zone: String?
    var low: Double?
    var high: Double?
    var basis: String?
}

struct PaceRangeDTO: Codable, Sendable, Equatable {
    var minSPerKm: Double?
    var maxSPerKm: Double?
    var label: String?
    var note: String?
}

struct IntervalWorkDTO: Codable, Sendable, Equatable {
    var reps: Int?
    var distanceM: Double?
    var paceSPerKm: Double?
    var paceLabel: String?
    var paceRangeSPerKm: [Double]?
    var hr: HRRangeDTO?
}

struct IntervalRecoveryDTO: Codable, Sendable, Equatable {
    var distanceM: Double?
    var type: String?
}

struct IntervalLegDTO: Codable, Sendable, Equatable {
    var distanceKm: Double?
    var hr: HRRangeDTO?
}

struct IntervalStructureDTO: Codable, Sendable, Equatable {
    var warmup: IntervalLegDTO?
    var work: IntervalWorkDTO?
    var recovery: IntervalRecoveryDTO?
    var cooldown: IntervalLegDTO?
}

struct DeloadDTO: Codable, Sendable, Equatable {
    var active: Bool?
    var from: String?
    var until: String?
}

/// The `session` object of a plan item: a union keyed by `type`
/// (`lifting` | `easy_run` | `long_run` | `intervals` | `rest`).
struct PrescriptionDTO: Codable, Sendable, Equatable {
    var type: String
    // lifting
    var kind: String?
    var gym: String?
    var exercises: [PlannedExerciseDTO]?
    var deload: DeloadDTO?
    // runs
    var distanceKm: Double?
    var hr: HRRangeDTO?
    var pace: PaceRangeDTO?
    var reason: String?
    // intervals
    var structure: IntervalStructureDTO?
    var totalKm: Double?
    // rest
    var optional: Bool?

    init(type: String, kind: String? = nil, gym: String? = nil, exercises: [PlannedExerciseDTO]? = nil,
         distanceKm: Double? = nil, hr: HRRangeDTO? = nil, pace: PaceRangeDTO? = nil,
         structure: IntervalStructureDTO? = nil, totalKm: Double? = nil) {
        self.type = type; self.kind = kind; self.gym = gym; self.exercises = exercises
        self.distanceKm = distanceKm; self.hr = hr; self.pace = pace
        self.structure = structure; self.totalKm = totalKm
    }
}

/// `status` of a plan item. Unknown values fall back to `.unknown`, so a new backend state never breaks decoding.
enum PlanStatus: String, TolerantEnum {
    case proposed, approved, done, skipped, modified, moved, unknown
    static var fallback: PlanStatus { .unknown }
}

struct PlanItemDTO: Codable, Sendable, Equatable {
    var id: String?
    var date: String
    var kind: String
    var status: String?
    var title: String?
    var slotId: String?
    var templateKind: String?
    var readiness: ReadinessDTO?
    var adjustments: [String]
    var notes: [String]
    var session: PrescriptionDTO?
    var updatedAt: Date?
    var deletedAt: Date?
    /// Additive (all optional): the session may still change with the last run, readiness or weekly volume.
    var adaptive: Bool?
    /// What it depends on ("last_run", "readiness", "weekly_volume", ...).
    var adaptsOn: [String]?
    /// "YYYY-MM-DD" the session was originally planned for, when it was moved.
    var movedFrom: String?
    var changeReason: String?

    init(id: String?, date: String, kind: String, status: String? = "proposed", title: String?,
         readiness: ReadinessDTO? = nil, adjustments: [String] = [], notes: [String] = [],
         session: PrescriptionDTO?, updatedAt: Date? = nil, adaptive: Bool? = nil, adaptsOn: [String]? = nil,
         movedFrom: String? = nil, changeReason: String? = nil) {
        self.id = id; self.date = date; self.kind = kind; self.status = status; self.title = title
        self.readiness = readiness; self.adjustments = adjustments; self.notes = notes
        self.session = session; self.updatedAt = updatedAt
        self.adaptive = adaptive; self.adaptsOn = adaptsOn; self.movedFrom = movedFrom; self.changeReason = changeReason
    }

    /// Typed `status` (unknown strings and nil map to `.unknown`).
    var planStatus: PlanStatus { status.flatMap(PlanStatus.init(rawValue:)) ?? .unknown }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id)
        date = try c.decode(String.self, forKey: .date)
        kind = try c.decode(String.self, forKey: .kind)
        status = try c.decodeIfPresent(String.self, forKey: .status)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        slotId = try c.decodeIfPresent(String.self, forKey: .slotId)
        templateKind = try c.decodeIfPresent(String.self, forKey: .templateKind)
        readiness = try c.decodeIfPresent(ReadinessDTO.self, forKey: .readiness)
        adjustments = try c.decodeIfPresent([String].self, forKey: .adjustments) ?? []
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
        session = try c.decodeIfPresent(PrescriptionDTO.self, forKey: .session)
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt)
        deletedAt = try c.decodeIfPresent(Date.self, forKey: .deletedAt)
        // Additive fields: a wrong shape must not hide the session.
        adaptive = (try? c.decodeIfPresent(Bool.self, forKey: .adaptive)) ?? nil
        adaptsOn = (try? c.decodeIfPresent([String].self, forKey: .adaptsOn)) ?? nil
        movedFrom = (try? c.decodeIfPresent(String.self, forKey: .movedFrom)) ?? nil
        changeReason = (try? c.decodeIfPresent(String.self, forKey: .changeReason)) ?? nil
    }

    /// Stable local key; preview items (no server id yet) get a synthetic one.
    var cacheId: String { id ?? "preview-\(date)-\(kind)" }
}

struct PlanDayDTO: Codable, Sendable, Equatable {
    var date: String
    var sessions: [PlanItemDTO]
}

struct PlanWeekDTO: Codable, Sendable, Equatable {
    var start: String
    var days: [PlanDayDTO]
}

struct PlanTodayDTO: Codable, Sendable, Equatable {
    var date: String
    var readiness: ReadinessDTO?
    var sessions: [PlanItemDTO]
    /// Headline + compact metrics + detail lines (newer backends only).
    var readinessToday: ReadinessTodayDTO?
    /// Proposals waiting for review (newer backends only).
    var coach: CoachSummaryDTO?

    init(date: String, readiness: ReadinessDTO? = nil, sessions: [PlanItemDTO], readinessToday: ReadinessTodayDTO? = nil,
         coach: CoachSummaryDTO? = nil) {
        self.date = date; self.readiness = readiness; self.sessions = sessions; self.readinessToday = readinessToday
        self.coach = coach
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        readiness = try c.decodeIfPresent(ReadinessDTO.self, forKey: .readiness)
        sessions = try c.decode([PlanItemDTO].self, forKey: .sessions)
        // Additive block: a shape change there must not hide today's plan.
        readinessToday = (try? c.decodeIfPresent(ReadinessTodayDTO.self, forKey: .readinessToday)) ?? nil
        coach = (try? c.decodeIfPresent(CoachSummaryDTO.self, forKey: .coach)) ?? nil
    }
}

// MARK: - Reference data

struct VariantDTO: Codable, Sendable, Equatable {
    var id: Int
    var exerciseId: Int?
    var name: String
    var gymId: Int?
    var equipmentType: String?
    var loadConvention: String?
    var active: Bool?
    var weightLadder: [Double]?
    var repRange: [Int]?
    var deletedAt: Date?
}

struct ExerciseDTO: Codable, Sendable, Equatable {
    var id: Int
    var name: String
    var muscleGroup: String?
    var movementPattern: String?
}

struct GymDTO: Codable, Sendable, Equatable {
    var id: Int
    var name: String
    var deletedAt: Date?
}

struct ConfigDTO: Codable, Sendable, Equatable {
    var version: String?
    var timezone: String?
    var dumbbellIncrementKg: Double?
    var repRange: [Int]?
    var rirTarget: [Int]?
    var incrementsKg: [String: Double]?
}

// MARK: - Sync

struct SyncSessionDTO: Codable, Sendable, Equatable {
    var id: String
    var plannedSessionId: String?
    var gymId: Int?
    var kind: String
    var name: String
    var startedAt: Date
    var endedAt: Date?
    var notes: String?
    var updatedAt: Date
    var deletedAt: Date?
}

struct SyncSetDTO: Codable, Sendable, Equatable {
    var id: String
    var sessionId: String
    var variantId: Int
    var exerciseOrder: Int
    var setOrder: Int
    var setType: String
    var weightKg: Double?
    var reps: Int?
    var durationS: Double?
    var completedAt: Date?
    var notes: String?
    var updatedAt: Date
    var deletedAt: Date?
}

struct SyncBodyDTO: Codable, Sendable, Equatable {
    var id: String
    var measuredAt: Date
    var weightKg: Double
    var note: String?
    var updatedAt: Date
    var deletedAt: Date?
}

/// Explicit encoding: nil optionals are sent as `null` (the server treats absent and null alike, but
/// being explicit makes tombstones and "uncompleted" sets unambiguous).
extension SyncSessionDTO {
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(plannedSessionId, forKey: .plannedSessionId)
        try c.encode(gymId, forKey: .gymId)
        try c.encode(kind, forKey: .kind)
        try c.encode(name, forKey: .name)
        try c.encode(startedAt, forKey: .startedAt)
        try c.encode(endedAt, forKey: .endedAt)
        try c.encode(notes, forKey: .notes)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(deletedAt, forKey: .deletedAt)
    }
}

extension SyncSetDTO {
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(sessionId, forKey: .sessionId)
        try c.encode(variantId, forKey: .variantId)
        try c.encode(exerciseOrder, forKey: .exerciseOrder)
        try c.encode(setOrder, forKey: .setOrder)
        try c.encode(setType, forKey: .setType)
        try c.encode(weightKg, forKey: .weightKg)
        try c.encode(reps, forKey: .reps)
        try c.encode(durationS, forKey: .durationS)
        try c.encode(completedAt, forKey: .completedAt)
        try c.encode(notes, forKey: .notes)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(deletedAt, forKey: .deletedAt)
    }
}

extension SyncBodyDTO {
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(measuredAt, forKey: .measuredAt)
        try c.encode(weightKg, forKey: .weightKg)
        try c.encode(note, forKey: .note)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(deletedAt, forKey: .deletedAt)
    }
}

struct PushRequest: Codable, Sendable, Equatable {
    var deviceId: String
    var sessions: [SyncSessionDTO]
    var sets: [SyncSetDTO]
    var bodyMeasurements: [SyncBodyDTO]
}

struct PushResultDTO: Codable, Sendable, Equatable {
    var entity: String
    var id: String
    /// "applied" | "stale" | "rejected"
    var status: String
    var reason: String?
}

struct PushResponse: Codable, Sendable, Equatable {
    var serverTime: Date?
    var results: [PushResultDTO]

    init(serverTime: Date?, results: [PushResultDTO]) { self.serverTime = serverTime; self.results = results }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        serverTime = try c.decodeIfPresent(Date.self, forKey: .serverTime)
        results = try c.decodeIfPresent([PushResultDTO].self, forKey: .results) ?? []
    }
}

struct PullResponse: Codable, Sendable, Equatable {
    var cursor: String?
    var hasMore: Bool
    var serverTime: Date?
    var sessions: [SyncSessionDTO]
    var sets: [SyncSetDTO]
    var bodyMeasurements: [SyncBodyDTO]
    var plannedSessions: [PlanItemDTO]
    var variants: [VariantDTO]
    var exercises: [ExerciseDTO]
    var gyms: [GymDTO]
    var config: ConfigDTO?

    init(cursor: String? = nil, hasMore: Bool = false, sessions: [SyncSessionDTO] = [], sets: [SyncSetDTO] = [],
         bodyMeasurements: [SyncBodyDTO] = [], plannedSessions: [PlanItemDTO] = [], variants: [VariantDTO] = [],
         exercises: [ExerciseDTO] = [], gyms: [GymDTO] = [], config: ConfigDTO? = nil) {
        self.cursor = cursor; self.hasMore = hasMore; self.sessions = sessions; self.sets = sets
        self.bodyMeasurements = bodyMeasurements; self.plannedSessions = plannedSessions
        self.variants = variants; self.exercises = exercises; self.gyms = gyms; self.config = config
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        cursor = try c.decodeIfPresent(String.self, forKey: .cursor)
        hasMore = try c.decodeIfPresent(Bool.self, forKey: .hasMore) ?? false
        serverTime = try c.decodeIfPresent(Date.self, forKey: .serverTime)
        sessions = try c.decodeIfPresent([SyncSessionDTO].self, forKey: .sessions) ?? []
        sets = try c.decodeIfPresent([SyncSetDTO].self, forKey: .sets) ?? []
        bodyMeasurements = try c.decodeIfPresent([SyncBodyDTO].self, forKey: .bodyMeasurements) ?? []
        plannedSessions = try c.decodeIfPresent([PlanItemDTO].self, forKey: .plannedSessions) ?? []
        variants = try c.decodeIfPresent([VariantDTO].self, forKey: .variants) ?? []
        exercises = try c.decodeIfPresent([ExerciseDTO].self, forKey: .exercises) ?? []
        gyms = try c.decodeIfPresent([GymDTO].self, forKey: .gyms) ?? []
        config = try c.decodeIfPresent(ConfigDTO.self, forKey: .config)
    }
}

struct HealthDTO: Codable, Sendable, Equatable {
    var status: String
    var version: String?
}

struct VariantHistorySetDTO: Codable, Sendable, Equatable {
    var weightKg: Double?
    var reps: Int?
    var durationS: Double?
    var setType: String?
}

struct VariantHistoryEntryDTO: Codable, Sendable, Equatable {
    var sessionId: String?
    var date: String?
    var sets: [VariantHistorySetDTO]
    var bestE1rm: Double?
}

import Foundation

// MARK: - Plan

public struct SignalDTO: Codable, Sendable, Equatable {
    public var name: String?
    public var level: String?
    public var detail: String?
    public var value: Double?
    public var baseline: Double?
}

public struct ReadinessDTO: Codable, Sendable, Equatable {
    public var date: String?
    /// "green" | "yellow" | "red" | nil (unknown)
    public var color: String?
    public var known: Bool?
    public var reason: String?
    public var signals: [SignalDTO]?

    public var level: ReadinessLevel { ReadinessLevel(color: color, known: known ?? (color != nil)) }
}

public struct PlannedSetDTO: Codable, Sendable, Equatable {
    public var index: Int
    public var setType: String
    public var targetWeight: Double?
    public var targetReps: Int?
    public var targetDurationS: Double?

    public init(index: Int, setType: String = "working", targetWeight: Double?, targetReps: Int?, targetDurationS: Double? = nil) {
        self.index = index
        self.setType = setType
        self.targetWeight = targetWeight
        self.targetReps = targetReps
        self.targetDurationS = targetDurationS
    }
}

public struct LastSetDTO: Codable, Sendable, Equatable {
    public var weight: Double?
    public var reps: Int?
    public var durationS: Double?

    public init(weight: Double? = nil, reps: Int? = nil, durationS: Double? = nil) {
        self.weight = weight
        self.reps = reps
        self.durationS = durationS
    }
}

public struct LastPerformanceDTO: Codable, Sendable, Equatable {
    public var date: String?
    public var sets: [LastSetDTO]
    public var bestE1rm: Double?

    public init(date: String?, sets: [LastSetDTO], bestE1rm: Double? = nil) {
        self.date = date; self.sets = sets; self.bestE1rm = bestE1rm
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decodeIfPresent(String.self, forKey: .date)
        sets = try c.decodeIfPresent([LastSetDTO].self, forKey: .sets) ?? []
        bestE1rm = try c.decodeIfPresent(Double.self, forKey: .bestE1rm)
    }
}

public struct PlannedExerciseDTO: Codable, Sendable, Equatable {
    public var order: Int?
    public var variantId: Int?
    public var variantKey: String?
    public var name: String
    public var exercise: String?
    public var equipmentType: String?
    public var loadConvention: String?
    public var repRange: [Int]?
    public var sets: [PlannedSetDTO]
    public var last: LastPerformanceDTO?
    public var rule: String?
    public var reason: String?
    public var flags: [String]
    public var unresolved: Bool?
    /// Rest timer in seconds (the exercise's override or the default); absent on older servers.
    public var restS: Int?
    /// Additive (gym machines): no machine mapped at the current gym yet.
    public var needsSetup: Bool?
    /// "baseline" = first session at this machine: no target, pick a weight.
    public var progressionState: String?
    public var hintWeightKg: Double?
    public var progressionNote: String?
    /// Present once the backend sends it; otherwise resolved through the variant / name.
    public var exerciseId: Int?

    public init(order: Int?, variantId: Int?, name: String, equipmentType: String? = nil, repRange: [Int]? = nil,
         sets: [PlannedSetDTO], last: LastPerformanceDTO? = nil, reason: String? = nil, flags: [String] = [],
         restS: Int? = nil, exercise: String? = nil, needsSetup: Bool? = nil, progressionState: String? = nil,
         hintWeightKg: Double? = nil, progressionNote: String? = nil, exerciseId: Int? = nil) {
        self.order = order; self.variantId = variantId; self.name = name; self.equipmentType = equipmentType
        self.repRange = repRange; self.sets = sets; self.last = last; self.reason = reason; self.flags = flags
        self.restS = restS; self.exercise = exercise; self.needsSetup = needsSetup
        self.progressionState = progressionState; self.hintWeightKg = hintWeightKg
        self.progressionNote = progressionNote; self.exerciseId = exerciseId
    }

    public init(from decoder: Decoder) throws {
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
        restS = try c.decodeIfPresent(Int.self, forKey: .restS)
        // Additive fields: a wrong shape must not hide the exercise.
        needsSetup = (try? c.decodeIfPresent(Bool.self, forKey: .needsSetup)) ?? nil
        progressionState = (try? c.decodeIfPresent(String.self, forKey: .progressionState)) ?? nil
        hintWeightKg = (try? c.decodeIfPresent(Double.self, forKey: .hintWeightKg)) ?? nil
        progressionNote = (try? c.decodeIfPresent(String.self, forKey: .progressionNote)) ?? nil
        exerciseId = (try? c.decodeIfPresent(Int.self, forKey: .exerciseId)) ?? nil
    }
}

public struct HRRangeDTO: Codable, Sendable, Equatable {
    public var zone: String?
    public var low: Double?
    public var high: Double?
    public var basis: String?
}

public struct PaceRangeDTO: Codable, Sendable, Equatable {
    public var minSPerKm: Double?
    public var maxSPerKm: Double?
    public var label: String?
    public var note: String?
}

public struct IntervalWorkDTO: Codable, Sendable, Equatable {
    public var reps: Int?
    public var distanceM: Double?
    public var paceSPerKm: Double?
    public var paceLabel: String?
    public var paceRangeSPerKm: [Double]?
    public var hr: HRRangeDTO?
}

public struct IntervalRecoveryDTO: Codable, Sendable, Equatable {
    public var distanceM: Double?
    public var type: String?
}

public struct IntervalLegDTO: Codable, Sendable, Equatable {
    public var distanceKm: Double?
    public var hr: HRRangeDTO?
}

/// `structure.strides` of an easy or long run: `reps` x `duration_s` fast strides, `recovery_s` easy jog between.
public struct StridesDTO: Codable, Sendable, Equatable {
    public var reps: Int?
    public var durationS: Double?
    public var recoveryS: Double?
    public var note: String?

    public init(reps: Int? = nil, durationS: Double? = nil, recoveryS: Double? = nil, note: String? = nil) {
        self.reps = reps; self.durationS = durationS; self.recoveryS = recoveryS; self.note = note
    }
}

/// `structure` of a run: the interval parts, or only `strides` on an easy / long run.
public struct IntervalStructureDTO: Codable, Sendable, Equatable {
    public var warmup: IntervalLegDTO?
    public var work: IntervalWorkDTO?
    public var recovery: IntervalRecoveryDTO?
    public var cooldown: IntervalLegDTO?
    public var strides: StridesDTO? = nil
}

public struct DeloadDTO: Codable, Sendable, Equatable {
    public var active: Bool?
    public var from: String?
    public var until: String?
}

/// The `session` object of a plan item: a union keyed by `type`
/// (`lifting` | `easy_run` | `long_run` | `intervals` | `rest`).
public struct PrescriptionDTO: Codable, Sendable, Equatable {
    public var type: String
    // lifting
    public var kind: String?
    public var gym: String?
    public var exercises: [PlannedExerciseDTO]?
    public var deload: DeloadDTO?
    // runs
    public var distanceKm: Double?
    public var hr: HRRangeDTO?
    public var pace: PaceRangeDTO?
    public var reason: String?
    // intervals
    public var structure: IntervalStructureDTO?
    public var totalKm: Double?
    // rest
    public var optional: Bool?
    /// What easy movement is fine on an optional rest day ("mobility 10-15 min").
    public var suggestion: String?

    public init(type: String, kind: String? = nil, gym: String? = nil, exercises: [PlannedExerciseDTO]? = nil,
         distanceKm: Double? = nil, hr: HRRangeDTO? = nil, pace: PaceRangeDTO? = nil,
         structure: IntervalStructureDTO? = nil, totalKm: Double? = nil) {
        self.type = type; self.kind = kind; self.gym = gym; self.exercises = exercises
        self.distanceKm = distanceKm; self.hr = hr; self.pace = pace
        self.structure = structure; self.totalKm = totalKm
    }

    /// Tolerant on `optional`: older servers sent the suggestion as a string there ("mobility 10-15 min"),
    /// which failed the whole sync pull.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decode(String.self, forKey: .type)
        kind = try c.decodeIfPresent(String.self, forKey: .kind)
        gym = try c.decodeIfPresent(String.self, forKey: .gym)
        exercises = try c.decodeIfPresent([PlannedExerciseDTO].self, forKey: .exercises)
        deload = try c.decodeIfPresent(DeloadDTO.self, forKey: .deload)
        distanceKm = try c.decodeIfPresent(Double.self, forKey: .distanceKm)
        hr = try c.decodeIfPresent(HRRangeDTO.self, forKey: .hr)
        pace = try c.decodeIfPresent(PaceRangeDTO.self, forKey: .pace)
        reason = try c.decodeIfPresent(String.self, forKey: .reason)
        structure = try c.decodeIfPresent(IntervalStructureDTO.self, forKey: .structure)
        totalKm = try c.decodeIfPresent(Double.self, forKey: .totalKm)
        suggestion = try c.decodeIfPresent(String.self, forKey: .suggestion)
        if let flag = try? c.decodeIfPresent(Bool.self, forKey: .optional) {
            optional = flag
        } else if let text = try? c.decodeIfPresent(String.self, forKey: .optional) {
            optional = true
            if suggestion == nil, !text.isEmpty { suggestion = text }
        } else {
            optional = nil
        }
    }
}

/// `status` of a plan item. Unknown values fall back to `.unknown`, so a new backend state never breaks decoding.
public enum PlanStatus: String, TolerantEnum, Sendable {
    case proposed, approved, done, skipped, modified, moved, unknown
    public static var fallback: PlanStatus { .unknown }
}

public struct PlanItemDTO: Codable, Sendable, Equatable {
    public var id: String?
    public var date: String
    public var kind: String
    public var status: String?
    public var title: String?
    public var slotId: String?
    public var templateKind: String?
    public var readiness: ReadinessDTO?
    public var adjustments: [String]
    public var notes: [String]
    public var session: PrescriptionDTO?
    public var updatedAt: Date?
    public var deletedAt: Date?
    /// Additive (all optional): the session may still change with the last run, readiness or weekly volume.
    public var adaptive: Bool?
    /// What it depends on ("last_run", "readiness", "weekly_volume", ...).
    public var adaptsOn: [String]?
    /// "YYYY-MM-DD" the session was originally planned for, when it was moved.
    public var movedFrom: String?
    public var changeReason: String?

    public init(id: String?, date: String, kind: String, status: String? = "proposed", title: String?,
         readiness: ReadinessDTO? = nil, adjustments: [String] = [], notes: [String] = [],
         session: PrescriptionDTO?, updatedAt: Date? = nil, adaptive: Bool? = nil, adaptsOn: [String]? = nil,
         movedFrom: String? = nil, changeReason: String? = nil) {
        self.id = id; self.date = date; self.kind = kind; self.status = status; self.title = title
        self.readiness = readiness; self.adjustments = adjustments; self.notes = notes
        self.session = session; self.updatedAt = updatedAt
        self.adaptive = adaptive; self.adaptsOn = adaptsOn; self.movedFrom = movedFrom; self.changeReason = changeReason
    }

    /// Typed `status` (unknown strings and nil map to `.unknown`).
    public var planStatus: PlanStatus { status.flatMap(PlanStatus.init(rawValue:)) ?? .unknown }

    public init(from decoder: Decoder) throws {
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
    public var cacheId: String { id ?? "preview-\(date)-\(kind)" }
}

public struct PlanDayDTO: Codable, Sendable, Equatable {
    public var date: String
    public var sessions: [PlanItemDTO]

    public init(date: String, sessions: [PlanItemDTO]) { self.date = date; self.sessions = sessions }
}

public struct PlanWeekDTO: Codable, Sendable, Equatable {
    public var start: String
    public var days: [PlanDayDTO]

    public init(start: String, days: [PlanDayDTO]) { self.start = start; self.days = days }
}

public struct PlanTodayDTO: Codable, Sendable, Equatable {
    public var date: String
    public var readiness: ReadinessDTO?
    public var sessions: [PlanItemDTO]
    /// Headline + compact metrics + detail lines (newer backends only).
    public var readinessToday: ReadinessTodayDTO?
    /// Proposals waiting for review (newer backends only).
    public var coach: CoachSummaryDTO?

    public init(date: String, readiness: ReadinessDTO? = nil, sessions: [PlanItemDTO], readinessToday: ReadinessTodayDTO? = nil,
         coach: CoachSummaryDTO? = nil) {
        self.date = date; self.readiness = readiness; self.sessions = sessions; self.readinessToday = readinessToday
        self.coach = coach
    }
    public init(from decoder: Decoder) throws {
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

public struct VariantDTO: Codable, Sendable, Equatable {
    public var id: Int
    public var exerciseId: Int?
    public var name: String
    public var gymId: Int?
    public var equipmentType: String?
    public var loadConvention: String?
    public var active: Bool?
    public var weightLadder: [Double]?
    public var repRange: [Int]?
    public var deletedAt: Date?
    /// The user's load step (additive; absent on older servers).
    public var incrementKg: Double?
}

public struct ExerciseDTO: Codable, Sendable, Equatable {
    public var id: Int
    public var name: String
    public var muscleGroup: String?
    public var movementPattern: String?
    /// Additive fields (docs/api/exercises.md); absent on older servers.
    public var bodyRegion: String?
    public var defaultEquipment: String?
    public var isTimed: Bool?
    /// `load | reps | time`; nil on older servers (see `resolvedProgression`).
    public var progression: Progression?

    public init(id: Int, name: String, muscleGroup: String? = nil, movementPattern: String? = nil, bodyRegion: String? = nil,
                defaultEquipment: String? = nil, isTimed: Bool? = nil, progression: Progression? = nil) {
        self.id = id
        self.name = name
        self.muscleGroup = muscleGroup
        self.movementPattern = movementPattern
        self.bodyRegion = bodyRegion
        self.defaultEquipment = defaultEquipment
        self.isTimed = isTimed
        self.progression = progression
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, muscleGroup, movementPattern, bodyRegion, defaultEquipment, isTimed, progression
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        muscleGroup = try c.decodeIfPresent(String.self, forKey: .muscleGroup)
        movementPattern = try c.decodeIfPresent(String.self, forKey: .movementPattern)
        bodyRegion = try c.decodeIfPresent(String.self, forKey: .bodyRegion)
        defaultEquipment = try c.decodeIfPresent(String.self, forKey: .defaultEquipment)
        isTimed = try c.decodeIfPresent(Bool.self, forKey: .isTimed)
        // an unknown value (a newer server) counts as absent, so the exercise still decodes
        progression = (try? c.decodeIfPresent(Progression.self, forKey: .progression)) ?? nil
    }

    /// The server's `progression`, else derived from `is_timed`.
    public var resolvedProgression: Progression {
        progression ?? (isTimed == true ? .time : .load)
    }
}

public struct GymDTO: Codable, Sendable, Equatable {
    public var id: Int
    public var name: String
    public var deletedAt: Date?
}

public struct ConfigDTO: Codable, Sendable, Equatable {
    public var version: String?
    public var timezone: String?
    public var dumbbellIncrementKg: Double?
    public var repRange: [Int]?
    public var rirTarget: [Int]?
    public var incrementsKg: [String: Double]?
    /// The server's current gym (additive; the server value wins after a pull).
    public var currentGymId: Int?
    /// Rest timers; absent on older servers.
    public var lifting: LiftingConfigDTO?
}

public struct LiftingConfigDTO: Codable, Sendable, Equatable {
    public var restDefaultS: Int?
    /// Per-exercise overrides under every variant id of the exercise (JSON keys are strings).
    public var restSByVariant: [String: Int]?
}

/// Body of `PUT /exercises/{variant_id}/rest`; `rest_s: null` resets the exercise to the default, so the key is
/// always written (the synthesized encoder would drop a nil).
public struct RestRequestDTO: Encodable, Sendable, Equatable {
    public var restS: Int?

    public enum CodingKeys: String, CodingKey, Sendable { case restS }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        if let restS { try c.encode(restS, forKey: .restS) } else { try c.encodeNil(forKey: .restS) }
    }
}

public struct RestResponseDTO: Codable, Sendable, Equatable {
    public var variantId: Int
    public var restS: Int
    public var isDefault: Bool?
    public var configVersion: String?
}

// MARK: - Sync

public struct SyncSessionDTO: Codable, Sendable, Equatable {
    public var id: String
    public var plannedSessionId: String?
    public var gymId: Int?
    public var kind: String
    public var name: String
    public var startedAt: Date
    public var endedAt: Date?
    public var notes: String?
    public var updatedAt: Date
    public var deletedAt: Date?

    public init(id: String, plannedSessionId: String? = nil, gymId: Int? = nil, kind: String, name: String, startedAt: Date, endedAt: Date? = nil, notes: String? = nil, updatedAt: Date, deletedAt: Date? = nil) {
        self.id = id
        self.plannedSessionId = plannedSessionId
        self.gymId = gymId
        self.kind = kind
        self.name = name
        self.startedAt = startedAt
        self.endedAt = endedAt
        self.notes = notes
        self.updatedAt = updatedAt
        self.deletedAt = deletedAt
    }
}

public struct SyncSetDTO: Codable, Sendable, Equatable {
    public var id: String
    public var sessionId: String
    public var variantId: Int
    public var exerciseOrder: Int
    public var setOrder: Int
    public var setType: String
    public var weightKg: Double?
    public var reps: Int?
    public var durationS: Double?
    public var completedAt: Date?
    public var notes: String?
    public var updatedAt: Date
    public var deletedAt: Date?

    public init(id: String, sessionId: String, variantId: Int, exerciseOrder: Int, setOrder: Int, setType: String, weightKg: Double? = nil, reps: Int? = nil, durationS: Double? = nil, completedAt: Date? = nil, notes: String? = nil, updatedAt: Date, deletedAt: Date? = nil) {
        self.id = id
        self.sessionId = sessionId
        self.variantId = variantId
        self.exerciseOrder = exerciseOrder
        self.setOrder = setOrder
        self.setType = setType
        self.weightKg = weightKg
        self.reps = reps
        self.durationS = durationS
        self.completedAt = completedAt
        self.notes = notes
        self.updatedAt = updatedAt
        self.deletedAt = deletedAt
    }
}

public struct SyncBodyDTO: Codable, Sendable, Equatable {
    public var id: String
    public var measuredAt: Date
    public var weightKg: Double
    public var note: String?
    public var updatedAt: Date
    public var deletedAt: Date?

    public init(id: String, measuredAt: Date, weightKg: Double, note: String? = nil, updatedAt: Date, deletedAt: Date? = nil) {
        self.id = id
        self.measuredAt = measuredAt
        self.weightKg = weightKg
        self.note = note
        self.updatedAt = updatedAt
        self.deletedAt = deletedAt
    }
}

/// Explicit encoding: nil optionals are sent as `null` (the server treats absent and null alike, but
/// being explicit makes tombstones and "uncompleted" sets unambiguous).
extension SyncSessionDTO {
    public func encode(to encoder: Encoder) throws {
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
    public func encode(to encoder: Encoder) throws {
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
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(measuredAt, forKey: .measuredAt)
        try c.encode(weightKg, forKey: .weightKg)
        try c.encode(note, forKey: .note)
        try c.encode(updatedAt, forKey: .updatedAt)
        try c.encode(deletedAt, forKey: .deletedAt)
    }
}

public struct PushRequest: Codable, Sendable, Equatable {
    public var deviceId: String
    public var sessions: [SyncSessionDTO]
    public var sets: [SyncSetDTO]
    public var bodyMeasurements: [SyncBodyDTO]

    public init(deviceId: String, sessions: [SyncSessionDTO], sets: [SyncSetDTO], bodyMeasurements: [SyncBodyDTO]) {
        self.deviceId = deviceId
        self.sessions = sessions
        self.sets = sets
        self.bodyMeasurements = bodyMeasurements
    }
}

public struct PushResultDTO: Codable, Sendable, Equatable {
    public var entity: String
    public var id: String
    /// "applied" | "stale" | "rejected"
    public var status: String
    public var reason: String?
}

public struct PushResponse: Codable, Sendable, Equatable {
    public var serverTime: Date?
    public var results: [PushResultDTO]

    public init(serverTime: Date?, results: [PushResultDTO]) { self.serverTime = serverTime; self.results = results }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        serverTime = try c.decodeIfPresent(Date.self, forKey: .serverTime)
        results = try c.decodeIfPresent([PushResultDTO].self, forKey: .results) ?? []
    }
}

public struct PullResponse: Codable, Sendable, Equatable {
    public var cursor: String?
    public var hasMore: Bool
    public var serverTime: Date?
    public var sessions: [SyncSessionDTO]
    public var sets: [SyncSetDTO]
    public var bodyMeasurements: [SyncBodyDTO]
    public var plannedSessions: [PlanItemDTO]
    public var variants: [VariantDTO]
    public var exercises: [ExerciseDTO]
    public var gyms: [GymDTO]
    public var config: ConfigDTO?

    public init(cursor: String? = nil, hasMore: Bool = false, sessions: [SyncSessionDTO] = [], sets: [SyncSetDTO] = [],
         bodyMeasurements: [SyncBodyDTO] = [], plannedSessions: [PlanItemDTO] = [], variants: [VariantDTO] = [],
         exercises: [ExerciseDTO] = [], gyms: [GymDTO] = [], config: ConfigDTO? = nil) {
        self.cursor = cursor; self.hasMore = hasMore; self.sessions = sessions; self.sets = sets
        self.bodyMeasurements = bodyMeasurements; self.plannedSessions = plannedSessions
        self.variants = variants; self.exercises = exercises; self.gyms = gyms; self.config = config
    }

    public init(from decoder: Decoder) throws {
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

public struct HealthDTO: Codable, Sendable, Equatable {
    public var status: String
    public var version: String?
}

public struct VariantHistorySetDTO: Codable, Sendable, Equatable {
    public var weightKg: Double?
    public var reps: Int?
    public var durationS: Double?
    public var setType: String?
}

public struct VariantHistoryEntryDTO: Codable, Sendable, Equatable {
    public var sessionId: String?
    public var date: String?
    public var sets: [VariantHistorySetDTO]
    public var bestE1rm: Double?
}

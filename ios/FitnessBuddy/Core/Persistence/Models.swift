import Foundation
import SwiftData

// MARK: - Synced, locally-owned records (outbox pattern)
//
// Every local mutation calls `touch()`: updatedAt = now, dirty = true. The SyncEngine pushes dirty
// records and clears `dirty` only when the server answered applied/stale AND the record was not
// edited again in the meantime (updatedAt unchanged since the snapshot).

@Model
final class LocalSession {
    @Attribute(.unique) var id: UUID
    var plannedSessionId: String?
    var gymId: Int?
    var kind: String
    var name: String
    var startedAt: Date
    var endedAt: Date?
    var notes: String?
    var updatedAt: Date
    var deletedAt: Date?
    var dirty: Bool
    /// Set when the server rejected the record; excluded from pushes until edited again.
    var rejectedReason: String?
    var hasBeenPushed: Bool
    /// True for workouts logged on this device. Server-written rows (history imports) can have no
    /// `ended_at`; they must never show up as an in-progress workout.
    var startedHere: Bool
    /// Created by the DEBUG demo mode. Never pushed (see `Outbox.collect`), wiped when a backend is configured.
    var isDemo: Bool = false

    init(id: UUID = UUID(), plannedSessionId: String? = nil, gymId: Int? = nil, kind: String = "other",
         name: String, startedAt: Date = .now, endedAt: Date? = nil, notes: String? = nil,
         updatedAt: Date = .now, deletedAt: Date? = nil, dirty: Bool = true) {
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
        self.dirty = dirty
        self.rejectedReason = nil
        self.hasBeenPushed = false
        self.startedHere = true
    }

    func touch(_ now: Date = .now) {
        updatedAt = now
        dirty = true
        rejectedReason = nil
    }

    var isActive: Bool { endedAt == nil && deletedAt == nil && startedHere }
}

@Model
final class LocalSet {
    @Attribute(.unique) var id: UUID
    var sessionId: UUID
    var variantId: Int
    var exerciseOrder: Int
    var setOrder: Int
    var setType: String
    var weightKg: Double?
    var reps: Int?
    var durationS: Double?
    var completedAt: Date?
    /// Exercise-level notes live on the first live set of the exercise (the API has no exercise entity).
    var notes: String?
    var updatedAt: Date
    var deletedAt: Date?
    var dirty: Bool
    var rejectedReason: String?
    var hasBeenPushed: Bool
    /// See `LocalSession.isDemo`.
    var isDemo: Bool = false

    // Local-only presentation data (never synced)
    var variantName: String
    var targetWeightKg: Double?
    var targetReps: Int?
    var targetDurationS: Double?
    var prevWeightKg: Double?
    var prevReps: Int?
    var prevDurationS: Double?

    init(id: UUID = UUID(), sessionId: UUID, variantId: Int, variantName: String = "", exerciseOrder: Int,
         setOrder: Int, setType: String = "working", weightKg: Double? = nil, reps: Int? = nil,
         durationS: Double? = nil, completedAt: Date? = nil, notes: String? = nil,
         updatedAt: Date = .now, deletedAt: Date? = nil, dirty: Bool = true) {
        self.id = id
        self.sessionId = sessionId
        self.variantId = variantId
        self.variantName = variantName
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
        self.dirty = dirty
        self.rejectedReason = nil
        self.hasBeenPushed = false
    }

    func touch(_ now: Date = .now) {
        updatedAt = now
        dirty = true
        rejectedReason = nil
    }

    var isCompleted: Bool { completedAt != nil }
    var isTimed: Bool { durationS != nil || targetDurationS != nil }
}

@Model
final class LocalBodyMeasurement {
    @Attribute(.unique) var id: UUID
    var measuredAt: Date
    var weightKg: Double
    var note: String?
    var updatedAt: Date
    var deletedAt: Date?
    var dirty: Bool
    var rejectedReason: String?
    var hasBeenPushed: Bool
    /// See `LocalSession.isDemo`.
    var isDemo: Bool = false

    init(id: UUID = UUID(), measuredAt: Date = .now, weightKg: Double, note: String? = nil,
         updatedAt: Date = .now, deletedAt: Date? = nil, dirty: Bool = true) {
        self.id = id
        self.measuredAt = measuredAt
        self.weightKg = weightKg
        self.note = note
        self.updatedAt = updatedAt
        self.deletedAt = deletedAt
        self.dirty = dirty
        self.rejectedReason = nil
        self.hasBeenPushed = false
    }

    func touch(_ now: Date = .now) {
        updatedAt = now
        dirty = true
        rejectedReason = nil
    }
}

// MARK: - Server-owned caches (read-only on the device)

@Model
final class CachedPlannedSession {
    @Attribute(.unique) var id: String
    /// "YYYY-MM-DD" (user's local date as computed by the backend)
    var date: String
    var kind: String
    var status: String
    var title: String
    var updatedAt: Date?
    var isPreview: Bool
    /// Re-encoded `PlanItemDTO` JSON; decode with `item`.
    var payload: Data

    init(id: String, date: String, kind: String, status: String, title: String,
         updatedAt: Date?, isPreview: Bool, payload: Data) {
        self.id = id
        self.date = date
        self.kind = kind
        self.status = status
        self.title = title
        self.updatedAt = updatedAt
        self.isPreview = isPreview
        self.payload = payload
    }
}

@Model
final class CachedVariant {
    @Attribute(.unique) var id: Int
    var exerciseId: Int
    var name: String
    var gymId: Int?
    var equipmentType: String
    var loadConvention: String
    var active: Bool
    /// Empty means "no ladder".
    var weightLadder: [Double]
    var repRangeLow: Int?
    var repRangeHigh: Int?
    /// Tombstoned variants stay resolvable for history but are hidden from add/swap pickers.
    var deletedAt: Date?

    init(id: Int, exerciseId: Int, name: String, gymId: Int?, equipmentType: String, loadConvention: String = "total",
         active: Bool = true, weightLadder: [Double] = [], repRangeLow: Int? = nil, repRangeHigh: Int? = nil,
         deletedAt: Date? = nil) {
        self.deletedAt = deletedAt
        self.id = id
        self.exerciseId = exerciseId
        self.name = name
        self.gymId = gymId
        self.equipmentType = equipmentType
        self.loadConvention = loadConvention
        self.active = active
        self.weightLadder = weightLadder
        self.repRangeLow = repRangeLow
        self.repRangeHigh = repRangeHigh
    }
}

@Model
final class CachedExercise {
    @Attribute(.unique) var id: Int
    var name: String
    var muscleGroup: String?
    var movementPattern: String?

    init(id: Int, name: String, muscleGroup: String? = nil, movementPattern: String? = nil) {
        self.id = id
        self.name = name
        self.muscleGroup = muscleGroup
        self.movementPattern = movementPattern
    }
}

@Model
final class CachedGym {
    @Attribute(.unique) var id: Int
    var name: String
    /// Retired gyms stay resolvable for old data but are hidden in pickers.
    var deletedAt: Date?

    init(id: Int, name: String, deletedAt: Date? = nil) {
        self.id = id
        self.name = name
        self.deletedAt = deletedAt
    }
}

/// Singleton row (key == "singleton") holding the sync cursor and small server-provided blobs.
@Model
final class SyncState {
    @Attribute(.unique) var key: String
    var deviceId: String
    var cursor: String?
    /// Last successful sync.
    var lastSyncAt: Date?
    /// Message of the last failed attempt; nil after a success.
    var lastError: String?
    /// When the last sync attempt (success or failure) finished.
    var lastAttemptAt: Date?
    /// Short result of the last successful sync ("Pushed 2, pulled 1 page").
    var lastSummary: String?
    /// Identifies the backend (URL + token) the cached data belongs to; see `BackendReset`.
    var backendFingerprint: String?
    /// True while the DEBUG demo data is loaded: sync is paused and nothing may be pushed.
    var demoActive: Bool = false
    var configData: Data?
    var readinessData: Data?
    var readinessDate: String?

    init(deviceId: String = UUID().uuidString.lowercased()) {
        self.key = "singleton"
        self.deviceId = deviceId
    }

    static func current(in context: ModelContext) -> SyncState {
        let descriptor = FetchDescriptor<SyncState>(predicate: #Predicate { $0.key == "singleton" })
        if let existing = try? context.fetch(descriptor).first { return existing }
        let state = SyncState()
        context.insert(state)
        try? context.save()
        return state
    }
}

// MARK: - Schema / container

enum Persistence {
    static let models: [any PersistentModel.Type] = [
        LocalSession.self, LocalSet.self, LocalBodyMeasurement.self,
        CachedPlannedSession.self, CachedVariant.self, CachedExercise.self, CachedGym.self, SyncState.self,
    ]

    static func makeContainer(inMemory: Bool = false) throws -> ModelContainer {
        let schema = Schema(models)
        let config = ModelConfiguration("FitnessBuddy", schema: schema, isStoredInMemoryOnly: inMemory)
        return try ModelContainer(for: schema, configurations: [config])
    }
}

// MARK: - Decoded views of cached payloads

extension CachedPlannedSession {
    var item: PlanItemDTO? { try? APICoding.makeDecoder().decode(PlanItemDTO.self, from: payload) }
}

extension SyncState {
    var config: ConfigDTO? { configData.flatMap { try? APICoding.makeDecoder().decode(ConfigDTO.self, from: $0) } }
    var readiness: ReadinessDTO? { readinessData.flatMap { try? APICoding.makeDecoder().decode(ReadinessDTO.self, from: $0) } }
}

import Foundation

// Wire models of the `/api/v1/stats/*` endpoints (+ `/runs`, `/runs/{id}/analysis`). Everything is optional or
// defaulted where the backend may send null / omit a key, unknown keys are ignored (Codable default) and every enum has
// an `unknown` fallback, so a small backend change never blanks a whole screen.
// Dates are `YYYY-MM-DD` strings (`DayKey` converts), weights kg, distances m, durations s, paces s/km.

/// Decodes a string-backed enum with a fallback case instead of failing.
public protocol TolerantEnum: RawRepresentable, Codable, Sendable where RawValue == String {
    static var fallback: Self { get }
}

extension TolerantEnum {
    public init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap { Self(rawValue: $0.lowercased()) } ?? Self.fallback
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

/// A whole number that also accepts a decimal, a bool or a list (its length), so a small backend change never blanks a screen.
public struct FlexCount: Codable, Sendable, Equatable {
    public var value: Int

    public init(_ value: Int) { self.value = value }
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = i }
        else if let d = try? c.decode(Double.self) { value = Int(d.rounded()) }
        else if let b = try? c.decode(Bool.self) { value = b ? 1 : 0 }
        else if let list = try? c.decode([FlexCount.Ignored].self) { value = list.count }
        else { value = 0 }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(value)
    }
    /// Decodes any JSON value without keeping it.
    public struct Ignored: Decodable, Sendable { public init(from decoder: Decoder) throws {} }
}

// MARK: - Readiness (shared by /plan/today and /stats/health)

public enum MetricStatus: String, TolerantEnum, Sendable {
    case ok, warn, bad, unknown
    public static var fallback: MetricStatus { .unknown }
}

public struct ReadinessMetricDTO: Codable, Sendable, Equatable, Identifiable {
    public var key: String
    public var label: String?
    public var value: Double?
    public var unit: String?
    public var baseline: Double?
    public var deltaPct: Double?
    public var status: MetricStatus?

    public var id: String { key }

    public init(key: String, label: String? = nil, value: Double? = nil, unit: String? = nil, baseline: Double? = nil, deltaPct: Double? = nil, status: MetricStatus? = nil) {
        self.key = key
        self.label = label
        self.value = value
        self.unit = unit
        self.baseline = baseline
        self.deltaPct = deltaPct
        self.status = status
    }
}

/// `readiness_today`: the headline, compact metrics and the long explanation lines (shown in the detail sheet only).
public struct ReadinessTodayDTO: Codable, Sendable, Equatable {
    /// "green" | "yellow" | "red" | nil
    public var level: String?
    /// "Ready" | "Take it easier" | "Recover" | "No data"
    public var headline: String?
    public var metrics: [ReadinessMetricDTO]
    public var details: [String]

    public init(level: String? = nil, headline: String? = nil, metrics: [ReadinessMetricDTO] = [], details: [String] = []) {
        self.level = level; self.headline = headline; self.metrics = metrics; self.details = details
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        level = try c.decodeIfPresent(String.self, forKey: .level)
        headline = try c.decodeIfPresent(String.self, forKey: .headline)
        metrics = try c.decodeIfPresent([ReadinessMetricDTO].self, forKey: .metrics) ?? []
        details = try c.decodeIfPresent([String].self, forKey: .details) ?? []
    }
}

// MARK: - Lifts

public struct LiftSummaryDTO: Codable, Sendable, Equatable, Identifiable, Hashable {
    public var variantId: Int
    public var name: String
    public var exerciseId: Int?
    public var muscleGroup: String?
    public var gymId: Int?
    public var gymName: String?
    public var active: Bool?
    public var sessions: Int?
    public var firstDate: String?
    public var lastDate: String?
    public var bestE1rm: Double?
    public var latestE1rm: Double?
    public var trend4wPct: Double?
    public var lastPrDate: String?
    public var stalled: Bool?
    /// Chest | Back | Shoulders | Arms | Legs | Core | Other (newer backends only).
    public var bodyRegion: String?
    /// Part of the current program (newer backends only).
    public var inProgram: Bool?

    // The snake-case decoder turns `trend_4w_pct` into `trend4WPct` and `best_e1rm` into `bestE1Rm` (it capitalises the letter after a digit), so spell the keys that way.
    public enum CodingKeys: String, CodingKey, Sendable {
        case variantId, name, exerciseId, muscleGroup, gymId, gymName, active, sessions, firstDate, lastDate
        case lastPrDate, stalled, bodyRegion, inProgram
        case bestE1rm = "bestE1Rm", latestE1rm = "latestE1Rm"
        case trend4wPct = "trend4WPct"
    }

    public var id: Int { variantId }
    public var isActive: Bool { active ?? true }
    public var isStalled: Bool { stalled ?? false }

    public init(variantId: Int, name: String, exerciseId: Int? = nil, muscleGroup: String? = nil, gymId: Int? = nil, gymName: String? = nil, active: Bool? = nil, sessions: Int? = nil, firstDate: String? = nil, lastDate: String? = nil, bestE1rm: Double? = nil, latestE1rm: Double? = nil, trend4wPct: Double? = nil, lastPrDate: String? = nil, stalled: Bool? = nil, bodyRegion: String? = nil, inProgram: Bool? = nil) {
        self.variantId = variantId
        self.name = name
        self.exerciseId = exerciseId
        self.muscleGroup = muscleGroup
        self.gymId = gymId
        self.gymName = gymName
        self.active = active
        self.sessions = sessions
        self.firstDate = firstDate
        self.lastDate = lastDate
        self.bestE1rm = bestE1rm
        self.latestE1rm = latestE1rm
        self.trend4wPct = trend4wPct
        self.lastPrDate = lastPrDate
        self.stalled = stalled
        self.bodyRegion = bodyRegion
        self.inProgram = inProgram
    }
}

public struct LiftsStatsDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var lifts: [LiftSummaryDTO]

    public init(generatedAt: Date? = nil, lifts: [LiftSummaryDTO] = []) { self.generatedAt = generatedAt; self.lifts = lifts }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        lifts = try c.decodeIfPresent([LiftSummaryDTO].self, forKey: .lifts) ?? []
    }
}

public struct TopSetDTO: Codable, Sendable, Equatable, Hashable {
    public var weightKg: Double?
    public var reps: Int?

    public init(weightKg: Double? = nil, reps: Int? = nil) {
        self.weightKg = weightKg
        self.reps = reps
    }
}

public struct LiftPointDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var sessionId: String?
    public var bestE1rm: Double?
    public var topSet: TopSetDTO?
    public var volumeKg: Double?
    public var sets: Int?

    public enum CodingKeys: String, CodingKey, Sendable {
        case date, sessionId, topSet, volumeKg, sets
        case bestE1rm = "bestE1Rm"  // see LiftSummaryDTO.CodingKeys
    }

    public var id: String { sessionId ?? date }

    public init(date: String, sessionId: String? = nil, bestE1rm: Double? = nil, topSet: TopSetDTO? = nil, volumeKg: Double? = nil, sets: Int? = nil) {
        self.date = date
        self.sessionId = sessionId
        self.bestE1rm = bestE1rm
        self.topSet = topSet
        self.volumeKg = volumeKg
        self.sets = sets
    }
}

public enum PRKind: String, TolerantEnum, Sendable {
    case e1rm, weight, reps, unknown
    public static var fallback: PRKind { .unknown }

    public var title: String {
        switch self {
        case .e1rm: "Estimated 1RM"
        case .weight: "Heaviest weight"
        case .reps: "Most reps"
        case .unknown: "Record"
        }
    }
}

public struct LiftPRDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var type: PRKind
    public var value: Double?
    public var weightKg: Double?
    public var reps: Int?

    public var id: String { "\(date)-\(type.rawValue)-\(value ?? 0)" }

    public init(date: String, type: PRKind, value: Double? = nil, weightKg: Double? = nil, reps: Int? = nil) {
        self.date = date
        self.type = type
        self.value = value
        self.weightKg = weightKg
        self.reps = reps
    }
}

public struct LiftDetailDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var lift: LiftSummaryDTO
    public var points: [LiftPointDTO]
    public var prs: [LiftPRDTO]

    public init(generatedAt: Date? = nil, lift: LiftSummaryDTO, points: [LiftPointDTO] = [], prs: [LiftPRDTO] = []) {
        self.generatedAt = generatedAt; self.lift = lift; self.points = points; self.prs = prs
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        lift = try c.decode(LiftSummaryDTO.self, forKey: .lift)
        points = try c.decodeIfPresent([LiftPointDTO].self, forKey: .points) ?? []
        prs = try c.decodeIfPresent([LiftPRDTO].self, forKey: .prs) ?? []
    }
}

// MARK: - Weekly volume

public struct WeekVolumeDTO: Codable, Sendable, Equatable, Identifiable {
    public var weekStart: String
    public var liftingVolumeKg: Double?
    public var liftingSets: Int?
    public var liftingSessions: Int?
    public var setsByMuscleGroup: [String: Int]?
    public var runningKm: Double?
    public var runningTimeS: Double?
    public var runs: Int?
    /// Lifting sessions of the week that contained a PR (newer backends only).
    public var prs: Int?
    /// Distinct lifts that set a PR that week.
    public var prLifts: FlexCount?
    public var plannedLiftingSessions: Int?

    public var id: String { weekStart }

    public init(weekStart: String, liftingVolumeKg: Double? = nil, liftingSets: Int? = nil, liftingSessions: Int? = nil, setsByMuscleGroup: [String: Int]? = nil, runningKm: Double? = nil, runningTimeS: Double? = nil, runs: Int? = nil, prs: Int? = nil, prLifts: FlexCount? = nil, plannedLiftingSessions: Int? = nil) {
        self.weekStart = weekStart
        self.liftingVolumeKg = liftingVolumeKg
        self.liftingSets = liftingSets
        self.liftingSessions = liftingSessions
        self.setsByMuscleGroup = setsByMuscleGroup
        self.runningKm = runningKm
        self.runningTimeS = runningTimeS
        self.runs = runs
        self.prs = prs
        self.prLifts = prLifts
        self.plannedLiftingSessions = plannedLiftingSessions
    }
}

public struct WeeklyVolumeDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var weeks: [WeekVolumeDTO]

    public init(generatedAt: Date? = nil, weeks: [WeekVolumeDTO] = []) { self.generatedAt = generatedAt; self.weeks = weeks }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        weeks = try c.decodeIfPresent([WeekVolumeDTO].self, forKey: .weeks) ?? []
    }
}

// MARK: - Running

public struct RunWeekDTO: Codable, Sendable, Equatable, Identifiable {
    public var weekStart: String
    public var km: Double?
    public var timeS: Double?
    public var runs: Int?
    /// Keys `z1`...`z5`, seconds.
    public var timeInZonesS: [String: Double]?
    /// One entry per run of the week (newer backends only).
    public var runsDetail: [RunDetailDTO]?
    public var runsByType: RunTypeCountsDTO?

    public var id: String { weekStart }

    public init(weekStart: String, km: Double? = nil, timeS: Double? = nil, runs: Int? = nil, timeInZonesS: [String: Double]? = nil, runsDetail: [RunDetailDTO]? = nil, runsByType: RunTypeCountsDTO? = nil) {
        self.weekStart = weekStart
        self.km = km
        self.timeS = timeS
        self.runs = runs
        self.timeInZonesS = timeInZonesS
        self.runsDetail = runsDetail
        self.runsByType = runsByType
    }
}

public enum RunType: String, TolerantEnum, CaseIterable, Identifiable, Sendable {
    case easy, intervals, long, other
    public static var fallback: RunType { .other }
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .easy: "Easy"
        case .intervals: "Intervals"
        case .long: "Long"
        case .other: "Other"
        }
    }
}

public struct RunDetailDTO: Codable, Sendable, Equatable, Identifiable {
    public var runId: String
    public var date: String?
    public var type: RunType?
    public var km: Double?

    public var id: String { runId }

    public init(runId: String, date: String? = nil, type: RunType? = nil, km: Double? = nil) {
        self.runId = runId
        self.date = date
        self.type = type
        self.km = km
    }
}

/// Runs (or km) per type of one week; the backend sends counts.
public struct RunTypeCountsDTO: Codable, Sendable, Equatable {
    public var easy: Double?
    public var intervals: Double?
    public var long: Double?
    public var other: Double?

    public func value(_ type: RunType) -> Double {
        switch type {
        case .easy: easy ?? 0
        case .intervals: intervals ?? 0
        case .long: long ?? 0
        case .other: other ?? 0
        }
    }

    public init(easy: Double? = nil, intervals: Double? = nil, long: Double? = nil, other: Double? = nil) {
        self.easy = easy
        self.intervals = intervals
        self.long = long
        self.other = other
    }
}

/// `kpis` of /stats/running. Everything optional: the view falls back to computing from the series.
public struct RunKpisDTO: Codable, Sendable, Equatable {
    public var vdotCurrent: Double?
    public var vdot4wAgo: Double?
    public var vdotChange: Double?
    public var efficiencyCurrent: Double?
    public var efficiency4wAgo: Double?
    public var efficiencyChangePct: Double?
    public var easyHrDriftFlags4w: FlexCount?
    public var runsThisWeek: Int?
    public var runsPlannedThisWeek: Int?
    public var kmThisWeek: Double?
    /// Newer, additive: why the current VDOT is what it is (`VdotSource`) and the secondary race-fit estimate.
    public var vdotSource: String?
    public var vdotFitCurrent: Double?

    // The snake-case decoder turns `vdot_4w_ago` into `vdot4WAgo` (it capitalises the letter after a digit).
    public enum CodingKeys: String, CodingKey, Sendable {
        case vdotCurrent, vdotChange, vdotSource, vdotFitCurrent, efficiencyCurrent, efficiencyChangePct, runsThisWeek, runsPlannedThisWeek, kmThisWeek
        case vdot4wAgo = "vdot4WAgo", efficiency4wAgo = "efficiency4WAgo", easyHrDriftFlags4w = "easyHrDriftFlags4W"
    }

    public init(vdotCurrent: Double? = nil, vdot4wAgo: Double? = nil, vdotChange: Double? = nil, efficiencyCurrent: Double? = nil, efficiency4wAgo: Double? = nil, efficiencyChangePct: Double? = nil, easyHrDriftFlags4w: FlexCount? = nil, runsThisWeek: Int? = nil, runsPlannedThisWeek: Int? = nil, kmThisWeek: Double? = nil, vdotSource: String? = nil, vdotFitCurrent: Double? = nil) {
        self.vdotCurrent = vdotCurrent
        self.vdot4wAgo = vdot4wAgo
        self.vdotChange = vdotChange
        self.efficiencyCurrent = efficiencyCurrent
        self.efficiency4wAgo = efficiency4wAgo
        self.efficiencyChangePct = efficiencyChangePct
        self.easyHrDriftFlags4w = easyHrDriftFlags4w
        self.runsThisWeek = runsThisWeek
        self.runsPlannedThisWeek = runsPlannedThisWeek
        self.kmThisWeek = kmThisWeek
        self.vdotSource = vdotSource
        self.vdotFitCurrent = vdotFitCurrent
    }
}

public struct EfficiencyPointDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var runId: String
    public var type: String?
    public var distanceM: Double?
    public var avgHr: Double?
    public var avgPaceSPerKm: Double?
    public var efficiency: Double?
    public var decouplingPct: Double?

    public var id: String { runId }

    public init(date: String, runId: String, type: String? = nil, distanceM: Double? = nil, avgHr: Double? = nil, avgPaceSPerKm: Double? = nil, efficiency: Double? = nil, decouplingPct: Double? = nil) {
        self.date = date
        self.runId = runId
        self.type = type
        self.distanceM = distanceM
        self.avgHr = avgHr
        self.avgPaceSPerKm = avgPaceSPerKm
        self.efficiency = efficiency
        self.decouplingPct = decouplingPct
    }
}

/// One point of `vdot` (the plan's VDOT, one per change in the engine's decision log) or `vdot_fit` (the
/// secondary race-fit estimate, `date` + `value` only). `source` / `delta` / `reason` are additive: see `VdotSource`.
public struct VdotPointDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var value: Double?
    public var source: String?
    public var delta: Double?
    public var reason: String?

    public var id: String { "\(date)-\(source ?? "")" }

    public init(date: String, value: Double? = nil, source: String? = nil, delta: Double? = nil, reason: String? = nil) {
        self.date = date
        self.value = value
        self.source = source
        self.delta = delta
        self.reason = reason
    }
}

public struct IntervalSessionDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var runId: String
    public var reps: Int?
    public var targetPaceSPerKm: Double?
    public var avgWorkPaceSPerKm: Double?
    public var avgWorkHr: Double?
    public var targetHrLow: Double?
    public var targetHrHigh: Double?
    public var hrInRange: Bool?
    public var recoveryOk: Bool?
    public var passed: Bool?

    public var id: String { runId }

    public init(date: String, runId: String, reps: Int? = nil, targetPaceSPerKm: Double? = nil, avgWorkPaceSPerKm: Double? = nil, avgWorkHr: Double? = nil, targetHrLow: Double? = nil, targetHrHigh: Double? = nil, hrInRange: Bool? = nil, recoveryOk: Bool? = nil, passed: Bool? = nil) {
        self.date = date
        self.runId = runId
        self.reps = reps
        self.targetPaceSPerKm = targetPaceSPerKm
        self.avgWorkPaceSPerKm = avgWorkPaceSPerKm
        self.avgWorkHr = avgWorkHr
        self.targetHrLow = targetHrLow
        self.targetHrHigh = targetHrHigh
        self.hrInRange = hrInRange
        self.recoveryOk = recoveryOk
        self.passed = passed
    }
}

public struct RunningStatsDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var weekly: [RunWeekDTO]
    public var efficiency: [EfficiencyPointDTO]
    /// The plan's VDOT as a step series (a point per change). Older backends send a per-run fit here instead.
    public var vdot: [VdotPointDTO]
    public var intervals: [IntervalSessionDTO]
    public var kpis: RunKpisDTO?
    /// Secondary: the per-run race-fit estimate (empty on older backends).
    public var vdotFit: [VdotPointDTO]

    public init(generatedAt: Date? = nil, weekly: [RunWeekDTO] = [], efficiency: [EfficiencyPointDTO] = [],
         vdot: [VdotPointDTO] = [], intervals: [IntervalSessionDTO] = [], kpis: RunKpisDTO? = nil,
         vdotFit: [VdotPointDTO] = []) {
        self.generatedAt = generatedAt; self.weekly = weekly; self.efficiency = efficiency
        self.vdot = vdot; self.intervals = intervals; self.kpis = kpis; self.vdotFit = vdotFit
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        weekly = try c.decodeIfPresent([RunWeekDTO].self, forKey: .weekly) ?? []
        efficiency = try c.decodeIfPresent([EfficiencyPointDTO].self, forKey: .efficiency) ?? []
        vdot = try c.decodeIfPresent([VdotPointDTO].self, forKey: .vdot) ?? []
        vdotFit = (try? c.decodeIfPresent([VdotPointDTO].self, forKey: .vdotFit)) ?? []
        intervals = try c.decodeIfPresent([IntervalSessionDTO].self, forKey: .intervals) ?? []
        // Newer, additive block: a shape change must not blank the charts.
        kpis = (try? c.decodeIfPresent(RunKpisDTO.self, forKey: .kpis)) ?? nil
    }
}

/// One row of `GET /api/v1/runs` (the existing Garmin endpoint; a list, no envelope).
public struct RunSummaryDTO: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var activityType: String?
    public var title: String?
    public var startedAtLocal: String?
    public var startedAtUtc: String?
    public var durationS: Double?
    public var distanceM: Double?
    public var avgHr: Double?
    public var maxHr: Double?
    public var avgPaceSPerKm: Double?
    public var trainingEffectAerobic: Double?

    /// `YYYY-MM-DD` of the local start (falls back to the UTC start).
    public var day: String? { (startedAtLocal ?? startedAtUtc).map { String($0.prefix(10)) } }

    public init(id: String, activityType: String? = nil, title: String? = nil, startedAtLocal: String? = nil, startedAtUtc: String? = nil, durationS: Double? = nil, distanceM: Double? = nil, avgHr: Double? = nil, maxHr: Double? = nil, avgPaceSPerKm: Double? = nil, trainingEffectAerobic: Double? = nil) {
        self.id = id
        self.activityType = activityType
        self.title = title
        self.startedAtLocal = startedAtLocal
        self.startedAtUtc = startedAtUtc
        self.durationS = durationS
        self.distanceM = distanceM
        self.avgHr = avgHr
        self.maxHr = maxHr
        self.avgPaceSPerKm = avgPaceSPerKm
        self.trainingEffectAerobic = trainingEffectAerobic
    }
}

public enum LapKind: String, TolerantEnum, Sendable {
    case warmup, work, recovery, cooldown, other
    public static var fallback: LapKind { .other }

    public var title: String {
        switch self {
        case .warmup: "Warm-up"
        case .work: "Work"
        case .recovery: "Recovery"
        case .cooldown: "Cool-down"
        case .other: "Lap"
        }
    }
}

public struct RunInfoDTO: Codable, Sendable, Equatable {
    public var id: String
    public var date: String?
    public var type: String?
    public var name: String?
    public var distanceM: Double?
    public var durationS: Double?
    public var avgHr: Double?
    public var maxHr: Double?
    public var avgPaceSPerKm: Double?
    public var trainingEffectAerobic: Double?
    public var trainingEffectAnaerobic: Double?

    public init(id: String, date: String? = nil, type: String? = nil, name: String? = nil, distanceM: Double? = nil, durationS: Double? = nil, avgHr: Double? = nil, maxHr: Double? = nil, avgPaceSPerKm: Double? = nil, trainingEffectAerobic: Double? = nil, trainingEffectAnaerobic: Double? = nil) {
        self.id = id
        self.date = date
        self.type = type
        self.name = name
        self.distanceM = distanceM
        self.durationS = durationS
        self.avgHr = avgHr
        self.maxHr = maxHr
        self.avgPaceSPerKm = avgPaceSPerKm
        self.trainingEffectAerobic = trainingEffectAerobic
        self.trainingEffectAnaerobic = trainingEffectAnaerobic
    }
}

public struct LapDTO: Codable, Sendable, Equatable, Identifiable {
    public var index: Int
    public var kind: LapKind
    public var distanceM: Double?
    public var durationS: Double?
    public var avgPaceSPerKm: Double?
    public var avgHr: Double?
    public var maxHr: Double?
    public var targetPaceSPerKm: Double?
    public var targetHrLow: Double?
    public var targetHrHigh: Double?
    public var paceOk: Bool?
    public var hrOk: Bool?

    public var id: Int { index }

    public init(index: Int, kind: LapKind, distanceM: Double? = nil, durationS: Double? = nil, avgPaceSPerKm: Double? = nil, avgHr: Double? = nil, maxHr: Double? = nil, targetPaceSPerKm: Double? = nil, targetHrLow: Double? = nil, targetHrHigh: Double? = nil, paceOk: Bool? = nil, hrOk: Bool? = nil) {
        self.index = index
        self.kind = kind
        self.distanceM = distanceM
        self.durationS = durationS
        self.avgPaceSPerKm = avgPaceSPerKm
        self.avgHr = avgHr
        self.maxHr = maxHr
        self.targetPaceSPerKm = targetPaceSPerKm
        self.targetHrLow = targetHrLow
        self.targetHrHigh = targetHrHigh
        self.paceOk = paceOk
        self.hrOk = hrOk
    }
}

public struct VerdictDTO: Codable, Sendable, Equatable {
    public var passed: Bool?
    public var summary: String?
    public var reasons: [String]

    public init(passed: Bool? = nil, summary: String? = nil, reasons: [String] = []) {
        self.passed = passed; self.summary = summary; self.reasons = reasons
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        passed = try c.decodeIfPresent(Bool.self, forKey: .passed)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        reasons = try c.decodeIfPresent([String].self, forKey: .reasons) ?? []
    }
}

public struct RunAnalysisDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var run: RunInfoDTO
    public var planned: PlanItemDTO?
    public var laps: [LapDTO]
    public var verdict: VerdictDTO?

    public init(generatedAt: Date? = nil, run: RunInfoDTO, planned: PlanItemDTO? = nil, laps: [LapDTO] = [], verdict: VerdictDTO? = nil) {
        self.generatedAt = generatedAt; self.run = run; self.planned = planned; self.laps = laps; self.verdict = verdict
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        run = try c.decode(RunInfoDTO.self, forKey: .run)
        // The plan item is only context: a shape change there must not hide the analysis.
        planned = (try? c.decodeIfPresent(PlanItemDTO.self, forKey: .planned)) ?? nil
        laps = try c.decodeIfPresent([LapDTO].self, forKey: .laps) ?? []
        verdict = try c.decodeIfPresent(VerdictDTO.self, forKey: .verdict)
    }
}

// MARK: - Health

public struct DayReadinessDTO: Codable, Sendable, Equatable {
    public var level: String?
    public var reason: String?

    public var readinessLevel: ReadinessLevel { ReadinessLevel(color: level) }

    public init(level: String? = nil, reason: String? = nil) {
        self.level = level
        self.reason = reason
    }
}

public struct HealthDayDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var hrvMs: Double?
    public var hrvBaselineMs: Double?
    public var rhr: Double?
    public var rhrBaseline: Double?
    public var sleepS: Double?
    public var sleepScore: Double?
    public var bodyBatteryHigh: Double?
    public var bodyBatteryLow: Double?
    public var stressAvg: Double?
    public var steps: Double?
    public var readiness: DayReadinessDTO?
    /// Trailing 7-day average of `steps` (newer backends only; `HealthTransforms.stepSeries` computes it otherwise).
    public var steps7dAvg: Double?

    // `steps_7d_avg` decodes to `steps7DAvg` (the snake-case decoder capitalises the letter after a digit).
    public enum CodingKeys: String, CodingKey, Sendable {
        case date, hrvMs, hrvBaselineMs, rhr, rhrBaseline, sleepS, sleepScore, bodyBatteryHigh, bodyBatteryLow
        case stressAvg, steps, readiness
        case steps7dAvg = "steps7DAvg"
    }

    public var id: String { date }

    public init(date: String, hrvMs: Double? = nil, hrvBaselineMs: Double? = nil, rhr: Double? = nil, rhrBaseline: Double? = nil, sleepS: Double? = nil, sleepScore: Double? = nil, bodyBatteryHigh: Double? = nil, bodyBatteryLow: Double? = nil, stressAvg: Double? = nil, steps: Double? = nil, readiness: DayReadinessDTO? = nil, steps7dAvg: Double? = nil) {
        self.date = date
        self.hrvMs = hrvMs
        self.hrvBaselineMs = hrvBaselineMs
        self.rhr = rhr
        self.rhrBaseline = rhrBaseline
        self.sleepS = sleepS
        self.sleepScore = sleepScore
        self.bodyBatteryHigh = bodyBatteryHigh
        self.bodyBatteryLow = bodyBatteryLow
        self.stressAvg = stressAvg
        self.steps = steps
        self.readiness = readiness
        self.steps7dAvg = steps7dAvg
    }
}

public struct BodyweightPointDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var weightKg: Double
    public var source: String?

    public var id: String { "\(date)-\(source ?? "")-\(weightKg)" }

    public init(date: String, weightKg: Double, source: String? = nil) {
        self.date = date
        self.weightKg = weightKg
        self.source = source
    }
}

public struct HealthSummaryDTO: Codable, Sendable, Equatable {
    public var hrv7d: Double?
    public var hrv28d: Double?
    public var rhr7d: Double?
    public var rhr28d: Double?
    public var sleep7dS: Double?
    public var sleep28dS: Double?
    public var bodyweightLatest: Double?
    public var bodyweightChange4wPct: Double?
    public var steps7dAvg: Double?
    public var days10kLast7: Int?

    // `hrv_7d` decodes to `hrv7D`, `sleep_28d_s` to `sleep28DS`, `bodyweight_change_4w_pct` to `bodyweightChange4WPct`
    // (the snake-case decoder capitalises the letter after a digit).
    public enum CodingKeys: String, CodingKey, Sendable {
        case hrv7d = "hrv7D", hrv28d = "hrv28D", rhr7d = "rhr7D", rhr28d = "rhr28D"
        case sleep7dS = "sleep7DS", sleep28dS = "sleep28DS"
        case bodyweightLatest
        case bodyweightChange4wPct = "bodyweightChange4WPct"
        case steps7dAvg = "steps7DAvg", days10kLast7 = "days10KLast7"
    }

    public init(hrv7d: Double? = nil, hrv28d: Double? = nil, rhr7d: Double? = nil, rhr28d: Double? = nil, sleep7dS: Double? = nil, sleep28dS: Double? = nil, bodyweightLatest: Double? = nil, bodyweightChange4wPct: Double? = nil, steps7dAvg: Double? = nil, days10kLast7: Int? = nil) {
        self.hrv7d = hrv7d
        self.hrv28d = hrv28d
        self.rhr7d = rhr7d
        self.rhr28d = rhr28d
        self.sleep7dS = sleep7dS
        self.sleep28dS = sleep28dS
        self.bodyweightLatest = bodyweightLatest
        self.bodyweightChange4wPct = bodyweightChange4wPct
        self.steps7dAvg = steps7dAvg
        self.days10kLast7 = days10kLast7
    }
}

public struct HealthStatsDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var days: [HealthDayDTO]
    public var bodyweight: [BodyweightPointDTO]
    public var summary: HealthSummaryDTO?
    public var readinessToday: ReadinessTodayDTO?

    public init(generatedAt: Date? = nil, days: [HealthDayDTO] = [], bodyweight: [BodyweightPointDTO] = [], summary: HealthSummaryDTO? = nil,
         readinessToday: ReadinessTodayDTO? = nil) {
        self.generatedAt = generatedAt; self.days = days; self.bodyweight = bodyweight; self.summary = summary
        self.readinessToday = readinessToday
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        days = try c.decodeIfPresent([HealthDayDTO].self, forKey: .days) ?? []
        bodyweight = try c.decodeIfPresent([BodyweightPointDTO].self, forKey: .bodyweight) ?? []
        summary = try c.decodeIfPresent(HealthSummaryDTO.self, forKey: .summary)
        readinessToday = (try? c.decodeIfPresent(ReadinessTodayDTO.self, forKey: .readinessToday)) ?? nil
    }
}

// MARK: - Correlations

public struct CorrelationPointDTO: Codable, Sendable, Equatable, Identifiable {
    public var date: String
    public var x: Double
    public var y: Double

    public var id: String { date }

    public init(date: String, x: Double, y: Double) { self.date = date; self.x = x; self.y = y }
}

public enum CorrelationCategory: String, TolerantEnum, CaseIterable, Identifiable, Sendable {
    case recovery, hybrid, unknown
    public static var fallback: CorrelationCategory { .unknown }
    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .recovery: "Recovery"
        case .hybrid: "Hybrid training"
        case .unknown: "Other"
        }
    }
}

public struct CorrelationDTO: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var title: String
    public var xLabel: String?
    public var yLabel: String?
    public var points: [CorrelationPointDTO]
    public var r: Double?
    public var n: Int?
    public var note: String?
    /// recovery | hybrid (newer backends only).
    public var category: CorrelationCategory?
    /// One plain-language sentence about the pattern (newer backends only).
    public var insight: String?

    public init(id: String, title: String, xLabel: String? = nil, yLabel: String? = nil, points: [CorrelationPointDTO] = [],
         r: Double? = nil, n: Int? = nil, note: String? = nil, category: CorrelationCategory? = nil, insight: String? = nil) {
        self.id = id; self.title = title; self.xLabel = xLabel; self.yLabel = yLabel
        self.points = points; self.r = r; self.n = n; self.note = note
        self.category = category; self.insight = insight
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? id
        xLabel = try c.decodeIfPresent(String.self, forKey: .xLabel)
        yLabel = try c.decodeIfPresent(String.self, forKey: .yLabel)
        points = try c.decodeIfPresent([CorrelationPointDTO].self, forKey: .points) ?? []
        r = try c.decodeIfPresent(Double.self, forKey: .r)
        n = try c.decodeIfPresent(Int.self, forKey: .n)
        note = try c.decodeIfPresent(String.self, forKey: .note)
        category = try c.decodeIfPresent(CorrelationCategory.self, forKey: .category)
        insight = try c.decodeIfPresent(String.self, forKey: .insight)
    }
}

public struct CorrelationsDTO: Codable, Sendable, Equatable {
    public var generatedAt: Date?
    public var items: [CorrelationDTO]

    public init(generatedAt: Date? = nil, items: [CorrelationDTO] = []) { self.generatedAt = generatedAt; self.items = items }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        items = try c.decodeIfPresent([CorrelationDTO].self, forKey: .items) ?? []
    }
}

import Foundation

// Wire models of the `/api/v1/stats/*` endpoints (+ `/runs`, `/runs/{id}/analysis`). Everything is optional or
// defaulted where the backend may send null / omit a key, unknown keys are ignored (Codable default) and every enum has
// an `unknown` fallback, so a small backend change never blanks a whole screen.
// Dates are `YYYY-MM-DD` strings (`DayKey` converts), weights kg, distances m, durations s, paces s/km.

/// Decodes a string-backed enum with a fallback case instead of failing.
protocol TolerantEnum: RawRepresentable, Codable, Sendable where RawValue == String {
    static var fallback: Self { get }
}

extension TolerantEnum {
    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap { Self(rawValue: $0.lowercased()) } ?? Self.fallback
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

/// A whole number that also accepts a decimal, a bool or a list (its length), so a small backend change never blanks a screen.
struct FlexCount: Codable, Sendable, Equatable {
    var value: Int

    init(_ value: Int) { self.value = value }
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = i }
        else if let d = try? c.decode(Double.self) { value = Int(d.rounded()) }
        else if let b = try? c.decode(Bool.self) { value = b ? 1 : 0 }
        else if let list = try? c.decode([FlexCount.Ignored].self) { value = list.count }
        else { value = 0 }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(value)
    }
    /// Decodes any JSON value without keeping it.
    struct Ignored: Decodable { init(from decoder: Decoder) throws {} }
}

// MARK: - Readiness (shared by /plan/today and /stats/health)

enum MetricStatus: String, TolerantEnum {
    case ok, warn, bad, unknown
    static var fallback: MetricStatus { .unknown }
}

struct ReadinessMetricDTO: Codable, Sendable, Equatable, Identifiable {
    var key: String
    var label: String?
    var value: Double?
    var unit: String?
    var baseline: Double?
    var deltaPct: Double?
    var status: MetricStatus?

    var id: String { key }
}

/// `readiness_today`: the headline, compact metrics and the long explanation lines (shown in the detail sheet only).
struct ReadinessTodayDTO: Codable, Sendable, Equatable {
    /// "green" | "yellow" | "red" | nil
    var level: String?
    /// "Ready" | "Take it easier" | "Recover" | "No data"
    var headline: String?
    var metrics: [ReadinessMetricDTO]
    var details: [String]

    init(level: String? = nil, headline: String? = nil, metrics: [ReadinessMetricDTO] = [], details: [String] = []) {
        self.level = level; self.headline = headline; self.metrics = metrics; self.details = details
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        level = try c.decodeIfPresent(String.self, forKey: .level)
        headline = try c.decodeIfPresent(String.self, forKey: .headline)
        metrics = try c.decodeIfPresent([ReadinessMetricDTO].self, forKey: .metrics) ?? []
        details = try c.decodeIfPresent([String].self, forKey: .details) ?? []
    }
}

// MARK: - Lifts

struct LiftSummaryDTO: Codable, Sendable, Equatable, Identifiable, Hashable {
    var variantId: Int
    var name: String
    var exerciseId: Int?
    var muscleGroup: String?
    var gymId: Int?
    var gymName: String?
    var active: Bool?
    var sessions: Int?
    var firstDate: String?
    var lastDate: String?
    var bestE1rm: Double?
    var latestE1rm: Double?
    var trend4wPct: Double?
    var lastPrDate: String?
    var stalled: Bool?
    /// Chest | Back | Shoulders | Arms | Legs | Core | Other (newer backends only).
    var bodyRegion: String?
    /// Part of the current program (newer backends only).
    var inProgram: Bool?

    // The snake-case decoder turns `trend_4w_pct` into `trend4WPct` and `best_e1rm` into `bestE1Rm` (it capitalises the letter after a digit), so spell the keys that way.
    enum CodingKeys: String, CodingKey {
        case variantId, name, exerciseId, muscleGroup, gymId, gymName, active, sessions, firstDate, lastDate
        case lastPrDate, stalled, bodyRegion, inProgram
        case bestE1rm = "bestE1Rm", latestE1rm = "latestE1Rm"
        case trend4wPct = "trend4WPct"
    }

    var id: Int { variantId }
    var isActive: Bool { active ?? true }
    var isStalled: Bool { stalled ?? false }
}

struct LiftsStatsDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var lifts: [LiftSummaryDTO]

    init(generatedAt: Date? = nil, lifts: [LiftSummaryDTO] = []) { self.generatedAt = generatedAt; self.lifts = lifts }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        lifts = try c.decodeIfPresent([LiftSummaryDTO].self, forKey: .lifts) ?? []
    }
}

struct TopSetDTO: Codable, Sendable, Equatable, Hashable {
    var weightKg: Double?
    var reps: Int?
}

struct LiftPointDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var sessionId: String?
    var bestE1rm: Double?
    var topSet: TopSetDTO?
    var volumeKg: Double?
    var sets: Int?

    enum CodingKeys: String, CodingKey {
        case date, sessionId, topSet, volumeKg, sets
        case bestE1rm = "bestE1Rm"  // see LiftSummaryDTO.CodingKeys
    }

    var id: String { sessionId ?? date }
}

enum PRKind: String, TolerantEnum {
    case e1rm, weight, reps, unknown
    static var fallback: PRKind { .unknown }

    var title: String {
        switch self {
        case .e1rm: "Estimated 1RM"
        case .weight: "Heaviest weight"
        case .reps: "Most reps"
        case .unknown: "Record"
        }
    }
}

struct LiftPRDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var type: PRKind
    var value: Double?
    var weightKg: Double?
    var reps: Int?

    var id: String { "\(date)-\(type.rawValue)-\(value ?? 0)" }
}

struct LiftDetailDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var lift: LiftSummaryDTO
    var points: [LiftPointDTO]
    var prs: [LiftPRDTO]

    init(generatedAt: Date? = nil, lift: LiftSummaryDTO, points: [LiftPointDTO] = [], prs: [LiftPRDTO] = []) {
        self.generatedAt = generatedAt; self.lift = lift; self.points = points; self.prs = prs
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        lift = try c.decode(LiftSummaryDTO.self, forKey: .lift)
        points = try c.decodeIfPresent([LiftPointDTO].self, forKey: .points) ?? []
        prs = try c.decodeIfPresent([LiftPRDTO].self, forKey: .prs) ?? []
    }
}

// MARK: - Weekly volume

struct WeekVolumeDTO: Codable, Sendable, Equatable, Identifiable {
    var weekStart: String
    var liftingVolumeKg: Double?
    var liftingSets: Int?
    var liftingSessions: Int?
    var setsByMuscleGroup: [String: Int]?
    var runningKm: Double?
    var runningTimeS: Double?
    var runs: Int?
    /// Lifting sessions of the week that contained a PR (newer backends only).
    var prs: Int?
    /// Distinct lifts that set a PR that week.
    var prLifts: FlexCount?
    var plannedLiftingSessions: Int?

    var id: String { weekStart }
}

struct WeeklyVolumeDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var weeks: [WeekVolumeDTO]

    init(generatedAt: Date? = nil, weeks: [WeekVolumeDTO] = []) { self.generatedAt = generatedAt; self.weeks = weeks }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        weeks = try c.decodeIfPresent([WeekVolumeDTO].self, forKey: .weeks) ?? []
    }
}

// MARK: - Running

struct RunWeekDTO: Codable, Sendable, Equatable, Identifiable {
    var weekStart: String
    var km: Double?
    var timeS: Double?
    var runs: Int?
    /// Keys `z1`...`z5`, seconds.
    var timeInZonesS: [String: Double]?
    /// One entry per run of the week (newer backends only).
    var runsDetail: [RunDetailDTO]?
    var runsByType: RunTypeCountsDTO?

    var id: String { weekStart }
}

enum RunType: String, TolerantEnum, CaseIterable, Identifiable {
    case easy, intervals, long, other
    static var fallback: RunType { .other }
    var id: String { rawValue }

    var title: String {
        switch self {
        case .easy: "Easy"
        case .intervals: "Intervals"
        case .long: "Long"
        case .other: "Other"
        }
    }
}

struct RunDetailDTO: Codable, Sendable, Equatable, Identifiable {
    var runId: String
    var date: String?
    var type: RunType?
    var km: Double?

    var id: String { runId }
}

/// Runs (or km) per type of one week; the backend sends counts.
struct RunTypeCountsDTO: Codable, Sendable, Equatable {
    var easy: Double?
    var intervals: Double?
    var long: Double?
    var other: Double?

    func value(_ type: RunType) -> Double {
        switch type {
        case .easy: easy ?? 0
        case .intervals: intervals ?? 0
        case .long: long ?? 0
        case .other: other ?? 0
        }
    }
}

/// `kpis` of /stats/running. Everything optional: the view falls back to computing from the series.
struct RunKpisDTO: Codable, Sendable, Equatable {
    var vdotCurrent: Double?
    var vdot4wAgo: Double?
    var vdotChange: Double?
    var efficiencyCurrent: Double?
    var efficiency4wAgo: Double?
    var efficiencyChangePct: Double?
    var easyHrDriftFlags4w: FlexCount?
    var runsThisWeek: Int?
    var runsPlannedThisWeek: Int?
    var kmThisWeek: Double?

    // The snake-case decoder turns `vdot_4w_ago` into `vdot4WAgo` (it capitalises the letter after a digit).
    enum CodingKeys: String, CodingKey {
        case vdotCurrent, vdotChange, efficiencyCurrent, efficiencyChangePct, runsThisWeek, runsPlannedThisWeek, kmThisWeek
        case vdot4wAgo = "vdot4WAgo", efficiency4wAgo = "efficiency4WAgo", easyHrDriftFlags4w = "easyHrDriftFlags4W"
    }
}

struct EfficiencyPointDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var runId: String
    var type: String?
    var distanceM: Double?
    var avgHr: Double?
    var avgPaceSPerKm: Double?
    var efficiency: Double?
    var decouplingPct: Double?

    var id: String { runId }
}

struct VdotPointDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var value: Double?

    var id: String { date }
}

struct IntervalSessionDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var runId: String
    var reps: Int?
    var targetPaceSPerKm: Double?
    var avgWorkPaceSPerKm: Double?
    var avgWorkHr: Double?
    var targetHrLow: Double?
    var targetHrHigh: Double?
    var hrInRange: Bool?
    var recoveryOk: Bool?
    var passed: Bool?

    var id: String { runId }
}

struct RunningStatsDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var weekly: [RunWeekDTO]
    var efficiency: [EfficiencyPointDTO]
    var vdot: [VdotPointDTO]
    var intervals: [IntervalSessionDTO]
    var kpis: RunKpisDTO?

    init(generatedAt: Date? = nil, weekly: [RunWeekDTO] = [], efficiency: [EfficiencyPointDTO] = [],
         vdot: [VdotPointDTO] = [], intervals: [IntervalSessionDTO] = [], kpis: RunKpisDTO? = nil) {
        self.generatedAt = generatedAt; self.weekly = weekly; self.efficiency = efficiency
        self.vdot = vdot; self.intervals = intervals; self.kpis = kpis
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        weekly = try c.decodeIfPresent([RunWeekDTO].self, forKey: .weekly) ?? []
        efficiency = try c.decodeIfPresent([EfficiencyPointDTO].self, forKey: .efficiency) ?? []
        vdot = try c.decodeIfPresent([VdotPointDTO].self, forKey: .vdot) ?? []
        intervals = try c.decodeIfPresent([IntervalSessionDTO].self, forKey: .intervals) ?? []
        // Newer, additive block: a shape change must not blank the charts.
        kpis = (try? c.decodeIfPresent(RunKpisDTO.self, forKey: .kpis)) ?? nil
    }
}

/// One row of `GET /api/v1/runs` (the existing Garmin endpoint; a list, no envelope).
struct RunSummaryDTO: Codable, Sendable, Equatable, Identifiable {
    var id: String
    var activityType: String?
    var title: String?
    var startedAtLocal: String?
    var startedAtUtc: String?
    var durationS: Double?
    var distanceM: Double?
    var avgHr: Double?
    var maxHr: Double?
    var avgPaceSPerKm: Double?
    var trainingEffectAerobic: Double?

    /// `YYYY-MM-DD` of the local start (falls back to the UTC start).
    var day: String? { (startedAtLocal ?? startedAtUtc).map { String($0.prefix(10)) } }
}

enum LapKind: String, TolerantEnum {
    case warmup, work, recovery, cooldown, other
    static var fallback: LapKind { .other }

    var title: String {
        switch self {
        case .warmup: "Warm-up"
        case .work: "Work"
        case .recovery: "Recovery"
        case .cooldown: "Cool-down"
        case .other: "Lap"
        }
    }
}

struct RunInfoDTO: Codable, Sendable, Equatable {
    var id: String
    var date: String?
    var type: String?
    var name: String?
    var distanceM: Double?
    var durationS: Double?
    var avgHr: Double?
    var maxHr: Double?
    var avgPaceSPerKm: Double?
    var trainingEffectAerobic: Double?
    var trainingEffectAnaerobic: Double?
}

struct LapDTO: Codable, Sendable, Equatable, Identifiable {
    var index: Int
    var kind: LapKind
    var distanceM: Double?
    var durationS: Double?
    var avgPaceSPerKm: Double?
    var avgHr: Double?
    var maxHr: Double?
    var targetPaceSPerKm: Double?
    var targetHrLow: Double?
    var targetHrHigh: Double?
    var paceOk: Bool?
    var hrOk: Bool?

    var id: Int { index }
}

struct VerdictDTO: Codable, Sendable, Equatable {
    var passed: Bool?
    var summary: String?
    var reasons: [String]

    init(passed: Bool? = nil, summary: String? = nil, reasons: [String] = []) {
        self.passed = passed; self.summary = summary; self.reasons = reasons
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        passed = try c.decodeIfPresent(Bool.self, forKey: .passed)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        reasons = try c.decodeIfPresent([String].self, forKey: .reasons) ?? []
    }
}

struct RunAnalysisDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var run: RunInfoDTO
    var planned: PlanItemDTO?
    var laps: [LapDTO]
    var verdict: VerdictDTO?

    init(generatedAt: Date? = nil, run: RunInfoDTO, planned: PlanItemDTO? = nil, laps: [LapDTO] = [], verdict: VerdictDTO? = nil) {
        self.generatedAt = generatedAt; self.run = run; self.planned = planned; self.laps = laps; self.verdict = verdict
    }
    init(from decoder: Decoder) throws {
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

struct DayReadinessDTO: Codable, Sendable, Equatable {
    var level: String?
    var reason: String?

    var readinessLevel: ReadinessLevel { ReadinessLevel(color: level) }
}

struct HealthDayDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var hrvMs: Double?
    var hrvBaselineMs: Double?
    var rhr: Double?
    var rhrBaseline: Double?
    var sleepS: Double?
    var sleepScore: Double?
    var bodyBatteryHigh: Double?
    var bodyBatteryLow: Double?
    var stressAvg: Double?
    var steps: Double?
    var readiness: DayReadinessDTO?
    /// Trailing 7-day average of `steps` (newer backends only; `HealthTransforms.stepSeries` computes it otherwise).
    var steps7dAvg: Double?

    // `steps_7d_avg` decodes to `steps7DAvg` (the snake-case decoder capitalises the letter after a digit).
    enum CodingKeys: String, CodingKey {
        case date, hrvMs, hrvBaselineMs, rhr, rhrBaseline, sleepS, sleepScore, bodyBatteryHigh, bodyBatteryLow
        case stressAvg, steps, readiness
        case steps7dAvg = "steps7DAvg"
    }

    var id: String { date }
}

struct BodyweightPointDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var weightKg: Double
    var source: String?

    var id: String { "\(date)-\(source ?? "")-\(weightKg)" }
}

struct HealthSummaryDTO: Codable, Sendable, Equatable {
    var hrv7d: Double?
    var hrv28d: Double?
    var rhr7d: Double?
    var rhr28d: Double?
    var sleep7dS: Double?
    var sleep28dS: Double?
    var bodyweightLatest: Double?
    var bodyweightChange4wPct: Double?
    var steps7dAvg: Double?
    var days10kLast7: Int?

    // `hrv_7d` decodes to `hrv7D`, `sleep_28d_s` to `sleep28DS`, `bodyweight_change_4w_pct` to `bodyweightChange4WPct`
    // (the snake-case decoder capitalises the letter after a digit).
    enum CodingKeys: String, CodingKey {
        case hrv7d = "hrv7D", hrv28d = "hrv28D", rhr7d = "rhr7D", rhr28d = "rhr28D"
        case sleep7dS = "sleep7DS", sleep28dS = "sleep28DS"
        case bodyweightLatest
        case bodyweightChange4wPct = "bodyweightChange4WPct"
        case steps7dAvg = "steps7DAvg", days10kLast7 = "days10KLast7"
    }
}

struct HealthStatsDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var days: [HealthDayDTO]
    var bodyweight: [BodyweightPointDTO]
    var summary: HealthSummaryDTO?
    var readinessToday: ReadinessTodayDTO?

    init(generatedAt: Date? = nil, days: [HealthDayDTO] = [], bodyweight: [BodyweightPointDTO] = [], summary: HealthSummaryDTO? = nil,
         readinessToday: ReadinessTodayDTO? = nil) {
        self.generatedAt = generatedAt; self.days = days; self.bodyweight = bodyweight; self.summary = summary
        self.readinessToday = readinessToday
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        days = try c.decodeIfPresent([HealthDayDTO].self, forKey: .days) ?? []
        bodyweight = try c.decodeIfPresent([BodyweightPointDTO].self, forKey: .bodyweight) ?? []
        summary = try c.decodeIfPresent(HealthSummaryDTO.self, forKey: .summary)
        readinessToday = (try? c.decodeIfPresent(ReadinessTodayDTO.self, forKey: .readinessToday)) ?? nil
    }
}

// MARK: - Correlations

struct CorrelationPointDTO: Codable, Sendable, Equatable, Identifiable {
    var date: String
    var x: Double
    var y: Double

    var id: String { date }
}

enum CorrelationCategory: String, TolerantEnum, CaseIterable, Identifiable {
    case recovery, hybrid, unknown
    static var fallback: CorrelationCategory { .unknown }
    var id: String { rawValue }

    var title: String {
        switch self {
        case .recovery: "Recovery"
        case .hybrid: "Hybrid training"
        case .unknown: "Other"
        }
    }
}

struct CorrelationDTO: Codable, Sendable, Equatable, Identifiable {
    var id: String
    var title: String
    var xLabel: String?
    var yLabel: String?
    var points: [CorrelationPointDTO]
    var r: Double?
    var n: Int?
    var note: String?
    /// recovery | hybrid (newer backends only).
    var category: CorrelationCategory?
    /// One plain-language sentence about the pattern (newer backends only).
    var insight: String?

    init(id: String, title: String, xLabel: String? = nil, yLabel: String? = nil, points: [CorrelationPointDTO] = [],
         r: Double? = nil, n: Int? = nil, note: String? = nil, category: CorrelationCategory? = nil, insight: String? = nil) {
        self.id = id; self.title = title; self.xLabel = xLabel; self.yLabel = yLabel
        self.points = points; self.r = r; self.n = n; self.note = note
        self.category = category; self.insight = insight
    }
    init(from decoder: Decoder) throws {
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

struct CorrelationsDTO: Codable, Sendable, Equatable {
    var generatedAt: Date?
    var items: [CorrelationDTO]

    init(generatedAt: Date? = nil, items: [CorrelationDTO] = []) { self.generatedAt = generatedAt; self.items = items }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        items = try c.decodeIfPresent([CorrelationDTO].self, forKey: .items) ?? []
    }
}

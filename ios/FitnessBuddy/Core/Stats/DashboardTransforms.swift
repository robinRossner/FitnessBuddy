import Foundation

// Pure view-model logic of the reworked dashboards (Today run card, Progress, Health, Insights).
// Every figure the newer backend sends is optional; the helpers fall back to computing it from the existing series.

// MARK: - Heart-rate zone chip

enum HRZoneChip {
    /// Easy runs are capped at 89 % of LTHR (engine default), the plan's `basis: "lthr"` upper bound.
    static let easyCeilingPct = 0.89
    /// Top of Z1...Z4 as a fraction of LTHR (engine `zones_pct_lthr`); everything above is Z5.
    static let zoneTopsPct = [0.85, 0.89, 0.94, 0.99]

    /// "Z1–Z2" when the plan names the zone; otherwise derived from the bpm range with LTHR-based bounds
    /// (`lthr` if known, else estimated from an LTHR-based easy ceiling); otherwise "127–156 bpm".
    static func label(for hr: HRRangeDTO?, lthr: Double? = nil) -> String? {
        if let zone = hr?.zone?.trimmingCharacters(in: .whitespaces), !zone.isEmpty { return normalized(zone) }
        guard let lo = hr?.low, let hi = hr?.high else { return nil }
        let reference = lthr ?? ((hr?.basis ?? "").lowercased() == "lthr" ? hi / easyCeilingPct : nil)
        if let reference, reference > 0 {
            let zl = zone(ofBpm: lo, lthr: reference), zh = zone(ofBpm: hi, lthr: reference)
            return zl == zh ? "Z\(zl)" : "Z\(zl)–Z\(zh)"
        }
        return bpmText(hr)
    }

    /// "127–156 bpm"
    static func bpmText(_ hr: HRRangeDTO?) -> String? {
        guard let lo = hr?.low, let hi = hr?.high else { return nil }
        return "\(Int(lo.rounded()))–\(Int(hi.rounded())) bpm"
    }

    static func zone(ofBpm bpm: Double, lthr: Double) -> Int {
        let pct = bpm / lthr
        for (i, top) in zoneTopsPct.enumerated() where pct <= top + 0.002 { return i + 1 }
        return 5
    }

    /// Highest zone number named in a label ("Z1–Z2" -> 2, "Z4–Z5a" -> 5); nil for a plain bpm range.
    static func highestZone(in label: String) -> Int? {
        var best: Int?
        var digits = ""
        var inZone = false
        for ch in label + " " {
            if inZone, ch.isNumber { digits.append(ch); continue }
            if inZone {
                if let n = Int(digits) { best = max(best ?? 0, n) }
                digits = ""
                inZone = false
            }
            if ch == "Z" || ch == "z" { inZone = true }
        }
        return best
    }

    /// "Z1-Z2" / "z4-z5a" -> "Z1–Z2" / "Z4–Z5a".
    static func normalized(_ zone: String) -> String {
        zone.split(separator: "-", omittingEmptySubsequences: true)
            .map { part -> String in
                let p = part.trimmingCharacters(in: .whitespaces)
                return p.hasPrefix("z") ? "Z" + p.dropFirst() : p
            }
            .joined(separator: "–")
    }
}

// MARK: - Lifting

enum BodyRegion: String, CaseIterable, Identifiable, Sendable {
    case chest = "Chest", back = "Back", shoulders = "Shoulders", arms = "Arms", legs = "Legs", core = "Core", other = "Other"
    var id: String { rawValue }

    init(raw: String?) {
        self = BodyRegion.allCases.first { $0.rawValue.lowercased() == raw?.lowercased() } ?? .other
    }

    /// Local mapping used when the backend sends no `body_region`. Every muscle group lands in exactly one region
    /// (rear delts are Shoulders, traps and lats are Back), so the groups never overlap.
    init(muscleGroup: String?) {
        switch (muscleGroup ?? "").lowercased().replacingOccurrences(of: " ", with: "_") {
        case "chest", "pecs": self = .chest
        case "back", "lats", "upper_back", "lower_back", "traps", "trapezius", "rhomboids": self = .back
        case "shoulders", "delts", "front_delts", "side_delts", "rear_delts", "rear_delt", "deltoids": self = .shoulders
        case "biceps", "triceps", "forearms", "arms", "brachialis": self = .arms
        case "quads", "quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors", "legs", "hip_flexors": self = .legs
        case "abs", "core", "obliques": self = .core
        default: self = .other
        }
    }
}

struct LiftRegionGroup: Identifiable, Equatable {
    var region: BodyRegion
    var lifts: [LiftSummaryDTO]
    var id: String { region.rawValue }
}

enum LiftProgram {
    /// Lifts in the current program. Older backends send no `in_program`: then active lifts trained in the last 8 weeks.
    static func inProgram(_ lifts: [LiftSummaryDTO], now: Date = .now, calendar: Calendar = .current) -> [LiftSummaryDTO] {
        if lifts.contains(where: { $0.inProgram != nil }) { return lifts.filter { $0.inProgram == true } }
        let key = DayKey.string(DayKey.daysAgo(56, from: now, calendar: calendar), calendar: calendar)
        return lifts.filter { $0.isActive && ($0.lastDate ?? "") >= key }
    }

    static func region(of lift: LiftSummaryDTO) -> BodyRegion {
        if let raw = lift.bodyRegion, !raw.isEmpty { return BodyRegion(raw: raw) }
        return BodyRegion(muscleGroup: lift.muscleGroup)
    }

    /// Regions in display order (Chest, Back, ...), lifts by name; empty regions are left out.
    static func grouped(_ lifts: [LiftSummaryDTO]) -> [LiftRegionGroup] {
        BodyRegion.allCases.compactMap { region in
            let rows = lifts.filter { self.region(of: $0) == region }
                .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            return rows.isEmpty ? nil : LiftRegionGroup(region: region, lifts: rows)
        }
    }

    static func isProgressing(_ lift: LiftSummaryDTO) -> Bool { !lift.isStalled && (lift.trend4wPct ?? 0) > 0 }
}

enum SessionRange: String, CaseIterable, Identifiable, Sendable {
    case eightWeeks = "8 weeks", sixMonths = "6 months", year = "1 year"
    var id: String { rawValue }

    /// Monday (`YYYY-MM-DD`) of the first week shown.
    func firstWeek(now: Date = .now, calendar: Calendar = .current) -> String {
        switch self {
        case .eightWeeks:
            // 8 weeks = this week + the 7 before it.
            let monday = DayKey.date(DayKey.weekStart(now, calendar: calendar), calendar: calendar) ?? now
            return DayKey.weekStart(DayKey.daysAgo(7 * 7, from: monday, calendar: calendar), calendar: calendar)
        case .sixMonths, .year:
            let months = self == .sixMonths ? 6 : 12
            let cutoff = calendar.date(byAdding: .month, value: -months, to: calendar.startOfDay(for: now)) ?? now
            return DayKey.weekStart(cutoff, calendar: calendar)
        }
    }
}

struct SessionsWeek: Equatable, Identifiable {
    var weekStart: String
    var done: Int
    var planned: Int?
    var prs: Int
    var id: String { weekStart }
    var date: Date { DayKey.date(weekStart) ?? .distantPast }
}

struct LiftingKpis: Equatable {
    var sessionsDone: Int
    var sessionsPlanned: Int?
    var prsLast30Days: Int
    var progressing: Int
    var stalled: Int
    var tracked: Int
    /// 0...100, nil without planned-session data.
    var consistencyPct: Double?
}

enum LiftingDashboard {
    /// One entry per calendar week of the range (gaps become zero weeks), oldest first, ending with the current week.
    /// `prs` comes from the backend; when no week has it, it is estimated from the lifts' latest PR dates.
    static func series(_ weeks: [WeekVolumeDTO], lifts: [LiftSummaryDTO] = [], range: SessionRange,
                       now: Date = .now, calendar: Calendar = .current) -> [SessionsWeek] {
        let byWeek = Dictionary(weeks.map { ($0.weekStart, $0) }, uniquingKeysWith: { _, b in b })
        let backendHasPRs = weeks.contains { $0.prs != nil }
        var estimated: [String: Int] = [:]
        if !backendHasPRs {
            for l in lifts { if let d = l.lastPrDate, let date = DayKey.date(d, calendar: calendar) {
                estimated[DayKey.weekStart(date, calendar: calendar), default: 0] += 1
            } }
        }
        let current = DayKey.weekStart(now, calendar: calendar)
        var out: [SessionsWeek] = []
        var key = range.firstWeek(now: now, calendar: calendar)
        var guardCount = 0
        while key <= current, guardCount < 120 {
            let w = byWeek[key]
            out.append(SessionsWeek(weekStart: key, done: w?.liftingSessions ?? 0, planned: w?.plannedLiftingSessions,
                                    prs: w?.prs ?? estimated[key] ?? 0))
            guard let start = DayKey.date(key, calendar: calendar), let next = calendar.date(byAdding: .day, value: 7, to: start) else { break }
            key = DayKey.string(next, calendar: calendar)
            guardCount += 1
        }
        return out
    }

    static func kpis(weeks: [WeekVolumeDTO], lifts: [LiftSummaryDTO], plannedThisWeekFallback: Int? = nil,
                     now: Date = .now, calendar: Calendar = .current) -> LiftingKpis {
        let current = DayKey.weekStart(now, calendar: calendar)
        let thisWeek = weeks.first { $0.weekStart == current }
        let program = LiftProgram.inProgram(lifts, now: now, calendar: calendar)

        // Consistency: planned sessions done over the 8 completed weeks before this one.
        let first = DayKey.weekStart(DayKey.daysAgo(8 * 7, from: now, calendar: calendar), calendar: calendar)
        var planned = 0, done = 0
        for w in weeks where w.weekStart >= first && w.weekStart < current {
            guard let p = w.plannedLiftingSessions, p > 0 else { continue }
            planned += p
            done += min(w.liftingSessions ?? 0, p)
        }
        return LiftingKpis(
            sessionsDone: thisWeek?.liftingSessions ?? 0,
            sessionsPlanned: thisWeek?.plannedLiftingSessions ?? plannedThisWeekFallback,
            prsLast30Days: LiftListing.recentPRCount(lifts, days: 30, now: now, calendar: calendar),
            progressing: program.filter(LiftProgram.isProgressing).count,
            stalled: program.filter(\.isStalled).count,
            tracked: program.count,
            consistencyPct: planned > 0 ? Double(done) / Double(planned) * 100 : nil)
    }
}

// MARK: - Running

struct RunSegment: Equatable, Identifiable {
    var weekStart: String
    var runId: String
    var date: String?
    var type: RunType
    var km: Double
    /// Cumulative km below / including this run (the stack is drawn chronologically, not by type).
    var kmStart: Double
    var kmEnd: Double
    /// False for the single "other" segment standing in for a week the backend sent no per-run detail for.
    var isRun: Bool
    var id: String { "\(weekStart)-\(runId)" }
}

struct RunWeekStack: Equatable, Identifiable {
    var weekStart: String
    var segments: [RunSegment]
    var totalKm: Double
    var runCount: Int
    var id: String { weekStart }
    var date: Date { DayKey.date(weekStart) ?? .distantPast }
}

struct RunKpiValues: Equatable {
    var vdotCurrent: Double?
    var vdotPrevious: Double?
    var vdotChange: Double?
    var efficiencyCurrent: Double?
    var efficiencyChangePct: Double?
    var driftFlags: Int
    var runsThisWeek: Int
    var runsPlanned: Int?
    var kmThisWeek: Double
}

enum RunStackTransforms {
    /// One stack per week, oldest first: each run is a segment (chronological, cumulative km), typed by `runs_detail`.
    /// Weeks without per-run detail get one gray segment with the week's distance.
    static func stacks(_ weeks: [RunWeekDTO], last: Int? = nil) -> [RunWeekStack] {
        var out = RunTransforms.weeksWithRuns(weeks).map { week -> RunWeekStack in
            var segments: [RunSegment] = []
            if let detail = week.runsDetail, !detail.isEmpty {
                var cum = 0.0
                for r in detail.sorted(by: { ($0.date ?? "", $0.runId) < ($1.date ?? "", $1.runId) }) {
                    let km = max(0, r.km ?? 0)
                    segments.append(RunSegment(weekStart: week.weekStart, runId: r.runId, date: r.date, type: r.type ?? .other, km: km,
                                               kmStart: cum, kmEnd: cum + km, isRun: true))
                    cum += km
                }
            } else if let km = week.km, km > 0 {
                segments = [RunSegment(weekStart: week.weekStart, runId: "week-\(week.weekStart)", date: nil, type: .other, km: km,
                                       kmStart: 0, kmEnd: km, isRun: false)]
            }
            let total = segments.last?.kmEnd ?? 0
            let detailed = week.runsDetail.map { !$0.isEmpty } ?? false
            return RunWeekStack(weekStart: week.weekStart, segments: segments, totalKm: total,
                                runCount: detailed ? segments.count : (week.runs ?? segments.count))
        }
        if let last { out = Array(out.suffix(last)) }
        return out
    }

    /// The tiles on top of the running page. Backend `kpis` win; gaps are computed from the series.
    static func kpis(_ stats: RunningStatsDTO, plannedRunsThisWeek: Int? = nil, now: Date = .now,
                     calendar: Calendar = .current) -> RunKpiValues {
        let k = stats.kpis
        let cutoff = DayKey.string(DayKey.daysAgo(28, from: now, calendar: calendar), calendar: calendar)

        let vdot = stats.vdot.compactMap { p -> (String, Double)? in p.value.map { (p.date, $0) } }.sorted { $0.0 < $1.0 }
        // When the backend sends `kpis`, a null means "not enough data" (e.g. VDOT still at its seed): do not invent a value.
        let vdotNow = k != nil ? k?.vdotCurrent : vdot.last?.1
        let vdotThen = k != nil ? k?.vdot4wAgo : vdot.last { $0.0 <= cutoff }?.1
        let vdotChange = k != nil ? k?.vdotChange : ((vdotNow != nil && vdotThen != nil) ? vdotNow! - vdotThen! : nil)

        let eff = stats.efficiency.compactMap { p -> (String, Double)? in p.efficiency.map { (p.date, $0) } }.sorted { $0.0 < $1.0 }
        let effNow = k != nil ? k?.efficiencyCurrent : eff.last?.1
        let effThen = k != nil ? k?.efficiency4wAgo : eff.last { $0.0 <= cutoff }?.1
        var effChange = k?.efficiencyChangePct
        if k == nil, let n = effNow, let t = effThen, t != 0 { effChange = (n - t) / t * 100 }

        let flags = k?.easyHrDriftFlags4w?.value
            ?? stats.efficiency.filter { $0.date >= cutoff && ($0.decouplingPct ?? 0) > 5 }.count

        let week = stats.weekly.first { $0.weekStart == DayKey.weekStart(now, calendar: calendar) }
        return RunKpiValues(vdotCurrent: vdotNow, vdotPrevious: vdotThen, vdotChange: vdotChange,
                            efficiencyCurrent: effNow, efficiencyChangePct: effChange, driftFlags: flags,
                            runsThisWeek: k?.runsThisWeek ?? week?.runs ?? 0,
                            runsPlanned: k?.runsPlannedThisWeek ?? plannedRunsThisWeek,
                            kmThisWeek: k?.kmThisWeek ?? week?.km ?? 0)
    }

    /// Easy / long runs of the last 28 days whose pace-vs-heart-rate decoupling is above 5 %.
    static func driftRuns(_ points: [EfficiencyPointDTO], now: Date = .now, calendar: Calendar = .current) -> [EfficiencyPointDTO] {
        let cutoff = DayKey.string(DayKey.daysAgo(28, from: now, calendar: calendar), calendar: calendar)
        return points.filter { $0.date >= cutoff && ($0.decouplingPct ?? 0) > 5 }.sorted { $0.date > $1.date }
    }
}

// MARK: - Health: steps

struct StepPoint: Equatable, Identifiable {
    var date: String
    var steps: Double?
    /// Trailing 7-day average (the backend's `steps_7d_avg`, else computed from the daily steps).
    var avg7: Double?
    var id: String { date }
}

extension HealthTransforms {
    /// All days sorted, each with its 7-day average. The backend value wins; otherwise the mean of the steps recorded
    /// in the 7 calendar days ending that day (nil if there are none).
    static func stepSeries(_ days: [HealthDayDTO], calendar: Calendar = .current) -> [StepPoint] {
        let sorted = days.sorted { $0.date < $1.date }
        let byDay = Dictionary(sorted.map { ($0.date, $0.steps) }, uniquingKeysWith: { _, b in b })
        return sorted.map { d in
            if let avg = d.steps7dAvg { return StepPoint(date: d.date, steps: d.steps, avg7: avg) }
            guard let date = DayKey.date(d.date, calendar: calendar) else { return StepPoint(date: d.date, steps: d.steps, avg7: nil) }
            let values = (0..<7).compactMap { off -> Double? in
                byDay[DayKey.string(DayKey.daysAgo(off, from: date, calendar: calendar), calendar: calendar)] ?? nil
            }
            return StepPoint(date: d.date, steps: d.steps, avg7: values.isEmpty ? nil : values.reduce(0, +) / Double(values.count))
        }
    }

    /// Days with at least `goal` steps among the 7 days ending today (backend `days_10k_last_7` wins).
    static func daysAtGoal(_ days: [HealthDayDTO], summary: HealthSummaryDTO?, goal: Double = 10_000, now: Date = .now,
                           calendar: Calendar = .current) -> Int {
        if let n = summary?.days10kLast7 { return n }
        let first = DayKey.string(DayKey.daysAgo(6, from: now, calendar: calendar), calendar: calendar)
        let last = DayKey.string(DayKey.daysAgo(0, from: now, calendar: calendar), calendar: calendar)
        return days.filter { $0.date >= first && $0.date <= last && ($0.steps ?? 0) >= goal }.count
    }

    /// Latest 7-day step average (summary value, else the series' last).
    static func latestStepsAvg(_ series: [StepPoint], summary: HealthSummaryDTO?) -> Double? {
        summary?.steps7dAvg ?? series.last(where: { $0.avg7 != nil })?.avg7
    }
}

// MARK: - Insights

struct InsightGroup: Identifiable, Equatable {
    var category: CorrelationCategory
    var items: [CorrelationDTO]
    var id: String { category.rawValue }
}

enum InsightTransforms {
    /// "Sleep score vs readiness" was dropped by the backend (readiness already contains sleep); old caches may still hold it.
    static let droppedIds: Set<String> = ["sleep_score_vs_readiness"]

    static func category(of item: CorrelationDTO) -> CorrelationCategory {
        if let c = item.category, c != .unknown { return c }
        let id = item.id.lowercased()
        return id.contains("run") && id.contains("lift") ? .hybrid : .recovery
    }

    static func hasEnoughData(_ item: CorrelationDTO) -> Bool { item.r != nil && item.points.count >= 3 }

    /// Recovery first, then hybrid; items with enough data before the collapsed ones.
    static func grouped(_ items: [CorrelationDTO]) -> [InsightGroup] {
        let kept = items.filter { !droppedIds.contains($0.id) }
        return [CorrelationCategory.recovery, .hybrid, .unknown].compactMap { cat in
            let rows = kept.filter { category(of: $0) == cat }
            guard !rows.isEmpty else { return nil }
            return InsightGroup(category: cat, items: rows.filter(hasEnoughData) + rows.filter { !hasEnoughData($0) })
        }
    }
}

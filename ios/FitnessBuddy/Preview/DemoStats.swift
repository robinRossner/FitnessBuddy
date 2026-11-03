#if DEBUG
import FitnessCore
import Foundation

/// DEBUG-only synthetic answers for the stats endpoints, used while the demo data is loaded (no backend needed).
/// Deterministic per variant / run id; dates are relative to today so the charts always look current.
enum DemoStats {
    static let transport: any APITransport = ClosureTransport { _, path, _, _ in
        guard let data = json(for: path) else { throw APIError.http(404, nil) }
        return data
    }

    // Small deterministic noise in -1...1.
    private static func noise(_ seed: Int, _ i: Int) -> Double {
        var x = UInt64(truncatingIfNeeded: seed &* 7919 &+ i &* 104_729 &+ 12_345)
        x = x &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return Double((x >> 33) % 2001) / 1000 - 1
    }

    private static func day(_ offset: Int) -> String {
        DayKey.string(DayKey.daysAgo(offset, from: AppClock.instant))
    }

    /// Keys whose snake_case spelling the generic converter gets wrong (it does not undo the capital after a digit).
    private static let quirkKeys = [
        "hrv7D": "hrv_7d", "hrv28D": "hrv_28d", "rhr7D": "rhr_7d", "rhr28D": "rhr_28d", "sleep7DS": "sleep_7d_s",
        "sleep28DS": "sleep_28d_s", "bodyweightChange4WPct": "bodyweight_change_4w_pct", "trend4WPct": "trend_4w_pct",
        "bestE1Rm": "best_e1rm", "latestE1Rm": "latest_e1rm", "steps7DAvg": "steps_7d_avg", "days10KLast7": "days_10k_last_7",
        "vdot4WAgo": "vdot_4w_ago", "efficiency4WAgo": "efficiency_4w_ago", "easyHrDriftFlags4W": "easy_hr_drift_flags_4w",
    ]

    private static func encode<T: Encodable>(_ v: T) -> Data? {
        let e = APICoding.makeEncoder()
        e.keyEncodingStrategy = .custom { keys in
            let raw = keys.last!.stringValue
            if let fixed = quirkKeys[raw] { return AnyKey(fixed) }
            var out = ""
            for ch in raw { if ch.isUppercase { out += "_" + ch.lowercased() } else { out.append(ch) } }
            return AnyKey(out)
        }
        return try? e.encode(v)
    }

    private struct AnyKey: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init(_ s: String) { stringValue = s }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    static func json(for path: String) -> Data? {
        if path.hasSuffix("/stats/lifts") { return encode(lifts()) }
        if let id = Int(path.split(separator: "/").last ?? ""), path.contains("/stats/lifts/") { return encode(liftDetail(id)) }
        if path.hasSuffix("/stats/volume/weekly") { return encode(volume()) }
        if path.hasSuffix("/stats/running") { return encode(running()) }
        if path.hasSuffix("/analysis") {
            let parts = path.split(separator: "/")
            return encode(analysis(String(parts[parts.count - 2])))
        }
        if path.hasSuffix("/api/v1/runs") { return encode(recentRuns()) }
        if path.hasSuffix("/stats/health") { return encode(health()) }
        if path.hasSuffix("/stats/correlations") { return encode(correlations()) }
        return nil
    }

    // MARK: Lifts

    private static let catalog: [(id: Int, name: String, group: String, base: Double, gym: String)] = [
        (101, "Bench Press (Barbell)", "chest", 82, "Gym 3"), (102, "Overhead Press (Dumbbell)", "shoulders", 52, "Gym 3"),
        (103, "Squat (Smith Machine)", "legs", 118, "Gym 3"), (104, "Romanian Deadlift", "legs", 128, "Gym 3"),
        (105, "Lat Pulldown", "back", 74, "Gym 3"), (106, "Seated Row", "back", 70, "Gym 3"),
        (107, "Biceps Curl (Dumbbell)", "arms", 27, "Gym 3"), (108, "Incline Press (Machine)", "chest", 66, "Gym 3"),
    ]

    private static func series(_ idx: Int) -> [LiftPointDTO] {
        let c = catalog[idx]
        // Weekly-ish sessions over ~2.4 years (the Strong import starts in 2023), slow growth with a plateau for index 3.
        let total = 125
        return (0..<total).map { i in
            let off = (total - 1 - i) * 7 + Int(noise(c.id, i) * 1.5) + 2
            let progress = Double(i) / Double(total)
            let stall = idx == 3 && i > total - 8
            let e1rm = c.base * (0.8 + 0.28 * min(progress, stall ? 0.93 : 1)) + noise(c.id, i + 500) * c.base * 0.025
            let reps = 6 + Int((noise(c.id, i + 900) + 1) * 2)
            let weight = (e1rm / (1 + Double(reps) / 30) / 2.5).rounded() * 2.5
            return LiftPointDTO(date: day(off), sessionId: "s-\(c.id)-\(i)", bestE1rm: (e1rm * 10).rounded() / 10,
                                topSet: TopSetDTO(weightKg: weight, reps: reps), volumeKg: weight * Double(reps) * 3.4, sets: 3 + (i % 2))
        }
    }

    private static func summary(_ idx: Int) -> LiftSummaryDTO {
        let c = catalog[idx]
        let pts = series(idx)
        let best = pts.compactMap(\.bestE1rm).max()
        let latest = pts.last?.bestE1rm
        let monthAgo = pts.last { $0.date <= day(28) }?.bestE1rm
        let trend = (latest != nil && monthAgo != nil) ? (latest! - monthAgo!) / monthAgo! * 100 : nil
        return LiftSummaryDTO(variantId: c.id, name: c.name, exerciseId: c.id, muscleGroup: c.group, gymId: 3, gymName: c.gym,
                              active: idx != 7, sessions: pts.count, firstDate: pts.first?.date, lastDate: pts.last?.date,
                              bestE1rm: best, latestE1rm: latest, trend4wPct: trend.map { ($0 * 10).rounded() / 10 },
                              lastPrDate: idx == 0 || idx == 4 ? day(9) : day(60), stalled: idx == 3,
                              bodyRegion: idx % 2 == 0 ? BodyRegion(muscleGroup: c.group).rawValue : nil, inProgram: idx != 7)
    }

    static func lifts() -> LiftsStatsDTO { LiftsStatsDTO(generatedAt: AppClock.instant, lifts: catalog.indices.map(summary)) }

    static func liftDetail(_ id: Int) -> LiftDetailDTO {
        let idx = catalog.firstIndex { $0.id == id } ?? 0
        let pts = series(idx)
        var best = 0.0
        var prs: [LiftPRDTO] = []
        for p in pts {
            if let e = p.bestE1rm, e > best {
                best = e
                prs.append(LiftPRDTO(date: p.date, type: .e1rm, value: e, weightKg: p.topSet?.weightKg, reps: p.topSet?.reps))
            }
        }
        return LiftDetailDTO(generatedAt: AppClock.instant, lift: summary(idx), points: pts, prs: Array(prs.suffix(8)))
    }

    static func volume() -> WeeklyVolumeDTO {
        let weeks = (0..<53).reversed().map { w -> WeekVolumeDTO in
            let start = DayKey.weekStart(DayKey.daysAgo(w * 7, from: AppClock.instant))
            let sets = 30 + Int(noise(3, w) * 8) + (w == 0 ? -14 : 0)
            let sessions = w == 0 ? 2 : (noise(5, w) < -0.7 ? 2 : 3)
            let prs = noise(6, w) > 0.55 ? 1 + (noise(7, w) > 0.8 ? 1 : 0) : 0
            return WeekVolumeDTO(weekStart: start, liftingVolumeKg: Double(sets) * 410 + noise(4, w) * 800, liftingSets: sets,
                                 liftingSessions: sessions, setsByMuscleGroup: ["chest": sets / 4, "back": sets / 4, "legs": sets / 3, "shoulders": sets / 8, "arms": sets / 8],
                                 runningKm: nil, runningTimeS: nil, runs: nil, prs: prs, prLifts: FlexCount(prs), plannedLiftingSessions: 3)
        }
        return WeeklyVolumeDTO(generatedAt: AppClock.instant, weeks: weeks)
    }

    // MARK: Running

    static func running() -> RunningStatsDTO {
        let log = DemoRuns.log()
        let weekly = (0...DemoRuns.weeksBack).reversed().map { w -> RunWeekDTO in
            let runs = log.filter { $0.week == w }.sorted { $0.date < $1.date }
            let t = runs.map(\.durationS).reduce(0, +)
            let zones = ["z1": t * 0.08, "z2": t * 0.55, "z3": t * 0.2, "z4": t * 0.13, "z5": t * 0.04]
            let detail = runs.map { RunDetailDTO(runId: $0.id, date: $0.date, type: $0.type, km: ($0.km * 10).rounded() / 10) }
            let km = runs.map(\.km).reduce(0, +)
            let counts = RunTypeCountsDTO(easy: Double(runs.filter { $0.type == .easy }.count),
                                          intervals: Double(runs.filter { $0.type == .intervals }.count),
                                          long: Double(runs.filter { $0.type == .long }.count), other: 0)
            return RunWeekDTO(weekStart: DemoRuns.weekStart(w), km: (km * 10).rounded() / 10, timeS: t, runs: runs.count,
                              timeInZonesS: zones, runsDetail: detail, runsByType: counts)
        }
        // Easy runs, oldest first (the backend names the type "easy"; the Easy Pace tile and the efficiency chart read it).
        var eff: [EfficiencyPointDTO] = []
        for (i, r) in log.filter({ $0.type == .easy }).reversed().enumerated() {
            let speed = 60_000 / r.paceSPerKm  // m/min
            eff.append(EfficiencyPointDTO(date: r.date, runId: r.id, type: "easy", distanceM: r.distanceM, avgHr: r.avgHr,
                                          avgPaceSPerKm: (r.paceSPerKm * 10).rounded() / 10, efficiency: (speed / r.avgHr * 1000).rounded() / 1000,
                                          decouplingPct: ((3.1 - Double(i) * 0.1 + noise(9, i) * 0.3) * 10).rounded() / 10))
        }
        let vdot = [VdotPointDTO(date: day(62), value: 49.0, source: "initial", delta: 0, reason: "initial VDOT 49.0 from the pace model's fit"),
                    VdotPointDTO(date: day(41), value: 49.6, source: "green_sessions", delta: 0.6, reason: "VDOT 49.0 -> 49.6: 2 quality sessions passed easily"),
                    VdotPointDTO(date: day(20), value: 50.6, source: "interval_reps", delta: 1.0, reason: "VDOT 49.6 -> 50.6 measured from interval reps")]
        let vdotFit = [VdotPointDTO(date: day(62), value: 48.6), VdotPointDTO(date: day(41), value: 49.2), VdotPointDTO(date: day(20), value: 49.5), VdotPointDTO(date: day(6), value: 49.7)]
        let intervals = log.filter { $0.type == .intervals }.reversed().map { r in
            IntervalSessionDTO(date: r.date, runId: r.id, reps: r.reps, targetPaceSPerKm: r.targetPace,
                               avgWorkPaceSPerKm: (r.avgWorkPace * 10).rounded() / 10, avgWorkHr: r.avgWorkHr.rounded(),
                               targetHrLow: DemoRuns.targetHrLow, targetHrHigh: DemoRuns.targetHrHigh,
                               hrInRange: !r.hrOk.contains(false), recoveryOk: r.recoveryOk, passed: r.passed)
        }
        let latest = eff.last?.efficiency, before = eff.dropLast(4).last?.efficiency
        let change = (latest != nil && before != nil) ? ((latest! - before!) / before! * 1000).rounded() / 10 : nil
        let thisWeek = weekly.last
        let kpis = RunKpisDTO(vdotCurrent: 50.6, vdot4wAgo: 49.8, vdotChange: 0.8, efficiencyCurrent: latest, efficiency4wAgo: before,
                              efficiencyChangePct: change, easyHrDriftFlags4w: FlexCount(1), runsThisWeek: thisWeek?.runs ?? 0,
                              runsPlannedThisWeek: 3, kmThisWeek: thisWeek?.km, vdotSource: "interval_reps", vdotFitCurrent: 49.7)
        return RunningStatsDTO(generatedAt: AppClock.instant, weekly: weekly, efficiency: eff, vdot: vdot, intervals: Array(intervals), kpis: kpis, vdotFit: vdotFit)
    }

    static func recentRuns() -> [RunSummaryDTO] {
        DemoRuns.log().prefix(18).map { r in
            RunSummaryDTO(id: r.id, activityType: "running", title: r.title, startedAtLocal: "\(r.date)T\(r.startTime)", startedAtUtc: nil,
                          durationS: r.durationS, distanceM: r.distanceM, avgHr: r.avgHr, maxHr: r.maxHr, avgPaceSPerKm: r.paceSPerKm,
                          trainingEffectAerobic: r.aerobicEffect)
        }
    }

    static func analysis(_ id: String) -> RunAnalysisDTO { DemoRuns.analysis(id) }

    // MARK: Health

    static func health() -> HealthStatsDTO {
        // Today's values are the readiness card's (HRV 57 ms, resting HR 54, sleep 6.8 h, yellow): every chart ends on them.
        let todayHrv = 57.0, todayRhr = 54.0, todaySleepH = 6.8
        let days = (0..<90).reversed().map { off -> HealthDayDTO in
            let i = 90 - off
            let isToday = off == 0
            let hrv = isToday ? todayHrv : 62 + noise(11, i) * 9 + (off < 5 ? -8 : 0)
            let rhr = isToday ? todayRhr : (51 + noise(15, i) * 3 + (off < 5 ? 3 : 0)).rounded()
            let sleep = isToday ? todaySleepH : 7.1 + noise(12, i) * 1.0
            let level = off < 3 ? "yellow" : (noise(13, i) < -0.85 ? "red" : (noise(13, i) < -0.45 ? "yellow" : "green"))
            let high = 82 + noise(14, i) * 14
            return HealthDayDTO(date: day(off), hrvMs: (hrv * 10).rounded() / 10, hrvBaselineMs: 63, rhr: rhr,
                                rhrBaseline: 51, sleepS: sleep * 3600, sleepScore: (78 + noise(16, i) * 14).rounded(), bodyBatteryHigh: high.rounded(),
                                bodyBatteryLow: (high - 55 + noise(17, i) * 12).rounded(), stressAvg: 28, steps: (8500 + noise(18, i) * 3500).rounded(),
                                readiness: DayReadinessDTO(level: level, reason: level == "green" ? "HRV, resting heart rate and sleep are all normal." : "HRV is 12 % below baseline."),
                                steps7dAvg: nil)
        }
        func mean(_ values: [Double?], last n: Int) -> Double? {
            let v = values.suffix(n).compactMap { $0 }
            return v.isEmpty ? nil : v.reduce(0, +) / Double(v.count)
        }
        func tenth(_ v: Double?) -> Double? { v.map { ($0 * 10).rounded() / 10 } }
        // Roughly weekly weigh-ins (a day early or late), slowly down to the 78.4 kg of yesterday's weigh-in.
        let offsets: [Int] = [85, 78, 71, 64, 57, 50, 43, 36, 29, 22, 15, 8, 1]
        var weights: [BodyweightPointDTO] = []
        for (i, off) in offsets.enumerated() {
            let last = i == offsets.count - 1
            let shift: Int = last ? 0 : Int((noise(19, i) * 1.4).rounded())
            let kg: Double = last ? 78.4 : 78.4 + Double(off) * 0.026 + noise(20, i) * 0.25
            weights.append(BodyweightPointDTO(date: day(off + shift), weightKg: (kg * 10).rounded() / 10, source: "garmin"))
        }
        let latestKg = weights.last?.weightKg
        let monthAgoKg = weights[weights.count - 5].weightKg
        let change4w: Double? = latestKg.map { ((($0 - monthAgoKg) / monthAgoKg) * 1000).rounded() / 10 }
        let summary = HealthSummaryDTO(hrv7d: tenth(mean(days.map(\.hrvMs), last: 7)), hrv28d: tenth(mean(days.map(\.hrvMs), last: 28)),
                                       rhr7d: tenth(mean(days.map(\.rhr), last: 7)), rhr28d: tenth(mean(days.map(\.rhr), last: 28)),
                                       sleep7dS: mean(days.map(\.sleepS), last: 7), sleep28dS: mean(days.map(\.sleepS), last: 28),
                                       bodyweightLatest: latestKg, bodyweightChange4wPct: change4w)
        let readiness = ReadinessTodayDTO(
            level: "yellow", headline: "Take it easier",
            metrics: [ReadinessMetricDTO(key: "hrv", label: "HRV", value: 57, unit: "ms", baseline: 63, deltaPct: -9.5, status: .warn),
                      ReadinessMetricDTO(key: "rhr", label: "Resting HR", value: 54, unit: "bpm", baseline: 51, deltaPct: 5.9, status: .warn),
                      ReadinessMetricDTO(key: "sleep", label: "Sleep", value: 6.8, unit: "h", baseline: 7.2, deltaPct: -5.6, status: .ok)],
            details: ["HRV is 10 % below your baseline.", "Resting heart rate is 3 bpm above baseline.", "Sleep was slightly short."])
        return HealthStatsDTO(generatedAt: AppClock.instant, days: days, bodyweight: weights.reversed(), summary: summary, readinessToday: readiness)
    }

    static func correlations() -> CorrelationsDTO {
        let pts = (0..<40).map { i -> CorrelationPointDTO in
            let sleep = 6.0 + (noise(21, i) + 1) * 1.6
            return CorrelationPointDTO(date: day(i * 2), x: (sleep * 10).rounded() / 10, y: (48 + sleep * 2.4 + noise(22, i) * 5).rounded())
        }
        let few = (0..<2).map { CorrelationPointDTO(date: day($0), x: 1, y: 2) }
        return CorrelationsDTO(generatedAt: AppClock.instant, items: [
            CorrelationDTO(id: "sleep_hrv", title: "Sleep vs next-morning HRV", xLabel: "Sleep (h)", yLabel: "HRV (ms)", points: pts, r: 0.52, n: 40,
                           note: "On nights with more sleep your HRV tends to be higher the next morning.", category: .recovery,
                           insight: "More sleep tends to go with a higher HRV the next morning (r=0.52, n=40)"),
            CorrelationDTO(id: "volume_rhr", title: "Weekly lifting volume vs resting HR", xLabel: "Sets", yLabel: "RHR (bpm)", points: few, r: nil, n: 2,
                           note: "Needs at least three weeks of data.", category: .hybrid,
                           insight: "Needs at least three weeks of data."),
        ])
    }
}
#endif

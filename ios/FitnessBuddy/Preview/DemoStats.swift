#if DEBUG
import Foundation

/// DEBUG-only synthetic answers for the stats endpoints, used while the demo data is loaded (no backend needed).
/// Deterministic per variant / run id; dates are relative to today so the charts always look current.
enum DemoStats {
    static let transport: StatsRepository.Transport = { path, _ in
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
        DayKey.string(DayKey.daysAgo(offset, from: .now))
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

    static func lifts() -> LiftsStatsDTO { LiftsStatsDTO(generatedAt: .now, lifts: catalog.indices.map(summary)) }

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
        return LiftDetailDTO(generatedAt: .now, lift: summary(idx), points: pts, prs: Array(prs.suffix(8)))
    }

    static func volume() -> WeeklyVolumeDTO {
        let weeks = (0..<53).reversed().map { w -> WeekVolumeDTO in
            let start = DayKey.weekStart(DayKey.daysAgo(w * 7, from: .now))
            let sets = 30 + Int(noise(3, w) * 8) + (w == 0 ? -14 : 0)
            let sessions = w == 0 ? 2 : (noise(5, w) < -0.7 ? 2 : 3)
            let prs = noise(6, w) > 0.55 ? 1 + (noise(7, w) > 0.8 ? 1 : 0) : 0
            return WeekVolumeDTO(weekStart: start, liftingVolumeKg: Double(sets) * 410 + noise(4, w) * 800, liftingSets: sets,
                                 liftingSessions: sessions, setsByMuscleGroup: ["chest": sets / 4, "back": sets / 4, "legs": sets / 3, "shoulders": sets / 8, "arms": sets / 8],
                                 runningKm: nil, runningTimeS: nil, runs: nil, prs: prs, prLifts: FlexCount(prs), plannedLiftingSessions: 3)
        }
        return WeeklyVolumeDTO(generatedAt: .now, weeks: weeks)
    }

    // MARK: Running

    static func running() -> RunningStatsDTO {
        let weekly = (0..<9).reversed().map { w -> RunWeekDTO in
            let km = 18 + Double(8 - w) * 2.2 + noise(8, w) * 3
            let t = km * 340
            let zones = ["z1": t * 0.08, "z2": t * 0.55, "z3": t * 0.2, "z4": t * 0.13, "z5": t * 0.04]
            let start = DayKey.weekStart(DayKey.daysAgo(w * 7, from: .now))
            let startDate = DayKey.date(start) ?? .now
            let easy1 = (km * 0.28 * 10).rounded() / 10, interval = (km * 0.24 * 10).rounded() / 10
            let long = ((km - easy1 - interval) * 10).rounded() / 10
            let when = { (offset: Int) in DayKey.string(Calendar.current.date(byAdding: .day, value: offset, to: startDate) ?? startDate) }
            var detail = [RunDetailDTO(runId: "run-e\(w)", date: when(1), type: .easy, km: easy1),
                          RunDetailDTO(runId: "run-i\(w)", date: when(3), type: .intervals, km: interval),
                          RunDetailDTO(runId: "run-l\(w)", date: when(6), type: .long, km: long)]
            if w == 0 { detail = Array(detail.prefix(2)) }
            return RunWeekDTO(weekStart: start, km: (km * 10).rounded() / 10, timeS: t,
                              runs: detail.count, timeInZonesS: zones, runsDetail: detail,
                              runsByType: RunTypeCountsDTO(easy: 1, intervals: 1, long: w == 0 ? 0 : 1, other: 0))
        }
        var eff: [EfficiencyPointDTO] = []
        for i in 0..<14 {
            let fi = Double(i)
            let efficiency: Double = 1.18 + fi * 0.011 + noise(9, i) * 0.012
            let hr: Double = 146 - fi * 0.4
            let pace: Double = 345 - fi * 1.2
            eff.append(EfficiencyPointDTO(date: day(60 - i * 4), runId: "run-e\(i)", type: "easy_run", distanceM: 7000 + fi * 150,
                                          avgHr: hr, avgPaceSPerKm: pace, efficiency: efficiency, decouplingPct: 3.1 - fi * 0.1))
        }
        let vdot = [VdotPointDTO(date: day(62), value: 49.0, source: "initial", delta: 0, reason: "initial VDOT 49.0 from the pace model's fit"),
                    VdotPointDTO(date: day(41), value: 49.6, source: "green_sessions", delta: 0.6, reason: "VDOT 49.0 -> 49.6: 2 quality sessions passed easily"),
                    VdotPointDTO(date: day(20), value: 50.6, source: "interval_reps", delta: 1.0, reason: "VDOT 49.6 -> 50.6 measured from interval reps")]
        let vdotFit = [VdotPointDTO(date: day(62), value: 48.6), VdotPointDTO(date: day(41), value: 49.2), VdotPointDTO(date: day(20), value: 49.5), VdotPointDTO(date: day(6), value: 49.7)]
        let intervals = (0..<5).map { i -> IntervalSessionDTO in
            IntervalSessionDTO(date: day(5 + i * 14), runId: "run-i\(i)", reps: 5 + i % 2, targetPaceSPerKm: 262, avgWorkPaceSPerKm: 262 + (i % 3 == 1 ? 8 : -2),
                               avgWorkHr: 168 + Double(i), targetHrLow: 164, targetHrHigh: 174, hrInRange: i != 3, recoveryOk: i != 2, passed: i == 3 ? false : (i % 3 == 1 ? false : true))
        }
        let kpis = RunKpisDTO(vdotCurrent: 50.6, vdot4wAgo: 49.8, vdotChange: 0.8, efficiencyCurrent: eff.last?.efficiency, efficiency4wAgo: 1.24,
                              efficiencyChangePct: 4.6, easyHrDriftFlags4w: FlexCount(1), runsThisWeek: 2, runsPlannedThisWeek: 3,
                              kmThisWeek: weekly.last?.km, vdotSource: "interval_reps", vdotFitCurrent: 49.7)
        return RunningStatsDTO(generatedAt: .now, weekly: weekly, efficiency: eff, vdot: vdot, intervals: intervals, kpis: kpis, vdotFit: vdotFit)
    }

    static func recentRuns() -> [RunSummaryDTO] {
        // The sample week's Wednesday easy run, so Today's "This week's running" shows a matched run.
        let wed = RunSummaryDTO(id: "run-demo-wed", activityType: "running", title: "Easy run", startedAtLocal: "\(AppClock.weekDays(containing: Date())[2])T07:05:00",
                                startedAtUtc: nil, durationS: 2900, distanceM: 8400, avgHr: 142, maxHr: 158, avgPaceSPerKm: 345, trainingEffectAerobic: 2.8)
        return [wed] + (0..<10).map { i in
            let isInterval = i % 3 == 1
            return RunSummaryDTO(id: isInterval ? "run-i\(i / 3)" : "run-e\(i)", activityType: "running", title: isInterval ? "Intervals 5×1000" : (i % 3 == 2 ? "Long run" : "Easy run"),
                                 startedAtLocal: "\(day(2 + i * 3))T07:15:00", startedAtUtc: nil, durationS: 2700 + Double(i) * 120, distanceM: 8000 + Double(i % 4) * 1500,
                                 avgHr: 148, maxHr: 171, avgPaceSPerKm: 335 - Double(i), trainingEffectAerobic: 3.2)
        }
    }

    static func analysis(_ id: String) -> RunAnalysisDTO {
        let run = RunInfoDTO(id: id, date: day(5), type: "intervals", name: "Intervals 5×1000", distanceM: 10_400, durationS: 3_150, avgHr: 158, maxHr: 178,
                             avgPaceSPerKm: 303, trainingEffectAerobic: 4.1, trainingEffectAnaerobic: 2.3)
        var laps: [LapDTO] = [LapDTO(index: 1, kind: .warmup, distanceM: 2000, durationS: 700, avgPaceSPerKm: 350, avgHr: 136, maxHr: 148)]
        var idx = 2
        for r in 0..<5 {
            let pace = 262.0 + [-2, 1, 4, 9, 3][r]
            laps.append(LapDTO(index: idx, kind: .work, distanceM: 1000, durationS: pace, avgPaceSPerKm: pace, avgHr: 165 + Double(r) * 2, maxHr: 172 + Double(r) * 2,
                               targetPaceSPerKm: 262, targetHrLow: 164, targetHrHigh: 174, paceOk: abs(pace - 262) <= 5, hrOk: r < 4))
            idx += 1
            if r < 4 {
                laps.append(LapDTO(index: idx, kind: .recovery, distanceM: 400, durationS: 150, avgPaceSPerKm: 375, avgHr: 148 - Double(r), maxHr: 168))
                idx += 1
            }
        }
        laps.append(LapDTO(index: idx, kind: .cooldown, distanceM: 1500, durationS: 520, avgPaceSPerKm: 347, avgHr: 140, maxHr: 150))
        let verdict = VerdictDTO(passed: false, summary: "4 of 5 work reps on pace, but the last rep slowed down and heart rate rose above the range.",
                                 reasons: ["Rep 4 was 9 s/km slower than the 4:22 target.", "Heart rate of rep 5 (171 bpm) was above the 160 to 170 range."])
        return RunAnalysisDTO(generatedAt: .now, run: run, planned: nil, laps: laps, verdict: verdict)
    }

    // MARK: Health

    static func health() -> HealthStatsDTO {
        let days = (0..<90).reversed().map { off -> HealthDayDTO in
            let i = 90 - off
            let hrv = 62 + noise(11, i) * 9 + (off < 5 ? -8 : 0)
            let sleep = 7.1 + noise(12, i) * 1.0
            let level = off < 3 && off > 0 ? "yellow" : (noise(13, i) < -0.85 ? "red" : (noise(13, i) < -0.45 ? "yellow" : "green"))
            let high = 82 + noise(14, i) * 14
            return HealthDayDTO(date: day(off), hrvMs: (hrv * 10).rounded() / 10, hrvBaselineMs: 63, rhr: (51 + noise(15, i) * 3 + (off < 5 ? 3 : 0)).rounded(),
                                rhrBaseline: 51, sleepS: sleep * 3600, sleepScore: (78 + noise(16, i) * 14).rounded(), bodyBatteryHigh: high.rounded(),
                                bodyBatteryLow: (high - 55 + noise(17, i) * 12).rounded(), stressAvg: 28, steps: (8500 + noise(18, i) * 3500).rounded(),
                                readiness: DayReadinessDTO(level: level, reason: level == "green" ? "HRV, resting heart rate and sleep are all normal." : "HRV is 12 % below baseline."),
                                steps7dAvg: nil)
        }
        let weights = (0..<8).map { i in BodyweightPointDTO(date: day(84 - i * 11), weightKg: ((80.6 - Double(i) * 0.12 + noise(19, i) * 0.3) * 10).rounded() / 10, source: "garmin") }
        let summary = HealthSummaryDTO(hrv7d: 57, hrv28d: 62, rhr7d: 53, rhr28d: 51.5, sleep7dS: 6.8 * 3600, sleep28dS: 7.2 * 3600,
                                       bodyweightLatest: weights.last?.weightKg, bodyweightChange4wPct: -0.6)
        let readiness = ReadinessTodayDTO(
            level: "yellow", headline: "Take it easier",
            metrics: [ReadinessMetricDTO(key: "hrv", label: "HRV", value: 57, unit: "ms", baseline: 63, deltaPct: -9.5, status: .warn),
                      ReadinessMetricDTO(key: "rhr", label: "Resting HR", value: 54, unit: "bpm", baseline: 51, deltaPct: 5.9, status: .warn),
                      ReadinessMetricDTO(key: "sleep", label: "Sleep", value: 6.8, unit: "h", baseline: 7.2, deltaPct: -5.6, status: .ok)],
            details: ["HRV is 10 % below your baseline.", "Resting heart rate is 3 bpm above baseline.", "Sleep was slightly short."])
        return HealthStatsDTO(generatedAt: .now, days: days, bodyweight: weights.reversed(), summary: summary, readinessToday: readiness)
    }

    static func correlations() -> CorrelationsDTO {
        let pts = (0..<40).map { i -> CorrelationPointDTO in
            let sleep = 6.0 + (noise(21, i) + 1) * 1.6
            return CorrelationPointDTO(date: day(i * 2), x: (sleep * 10).rounded() / 10, y: (48 + sleep * 2.4 + noise(22, i) * 5).rounded())
        }
        let few = (0..<2).map { CorrelationPointDTO(date: day($0), x: 1, y: 2) }
        return CorrelationsDTO(generatedAt: .now, items: [
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

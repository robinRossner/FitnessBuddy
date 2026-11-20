#if DEBUG
import FitnessCore
import Foundation

/// DEBUG-only: the demo's completed runs, one list that the weekly stats, the recent-runs list, the efficiency and interval
/// series and the run analysis all read, so a run has the same date, distance and pace everywhere. The plan's rhythm: an easy run on
/// Wednesday, intervals on Friday, a long run on Sunday. Dates are relative to the clock (`AppClock`); runs of the current week
/// count only when they lie before today (the run planned today has not happened yet).
enum DemoRuns {
    /// Interval target pace of the plan ("5x1000 m @ 4:10"), s/km; older sessions had a slower target (VDOT grew).
    static let planTargetPace = 250.0
    static let targetHrLow = 166.0, targetHrHigh = 179.0
    static let easyHrCeiling = 152.0
    static let weeksBack = 8

    struct Run {
        let id: String
        let type: RunType
        /// Weeks before the current one (0: this week).
        let week: Int
        let date: String
        let distanceM: Double
        let durationS: Double
        let avgHr: Double
        let maxHr: Double
        let reps: Int
        let workPaces: [Double]
        let workHr: [Double]
        let targetPace: Double
        let recoveryOk: Bool

        var km: Double { distanceM / 1000 }
        var paceSPerKm: Double { durationS / km }
        var title: String {
            switch type {
            case .intervals: "Intervals \(reps)×1000"
            case .long: "Long run"
            default: "Easy run"
            }
        }
        var startTime: String { type == .intervals ? "06:45:00" : (type == .long ? "08:30:00" : "07:05:00") }
        var aerobicEffect: Double { type == .intervals ? 4.1 : (type == .long ? 3.4 : 2.8) }
        var avgWorkPace: Double { workPaces.isEmpty ? 0 : workPaces.reduce(0, +) / Double(workPaces.count) }
        var avgWorkHr: Double { workHr.isEmpty ? 0 : workHr.reduce(0, +) / Double(workHr.count) }
        var paceOk: [Bool] { workPaces.map { abs($0 - targetPace) <= 4 } }
        var hrOk: [Bool] { workHr.map { $0 >= DemoRuns.targetHrLow && $0 <= DemoRuns.targetHrHigh } }
        var passed: Bool { !paceOk.contains(false) && !hrOk.contains(false) && recoveryOk }
    }

    // Small deterministic noise in -1...1 (same generator as `DemoStats`).
    private static func noise(_ seed: Int, _ i: Int) -> Double {
        var x = UInt64(truncatingIfNeeded: seed &* 7919 &+ i &* 104_729 &+ 12_345)
        x = x &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        return Double((x >> 33) % 2001) / 1000 - 1
    }

    private static func round1(_ v: Double) -> Double { (v * 10).rounded() / 10 }

    /// The Monday of the week `w` weeks back, as a day string.
    static func weekStart(_ w: Int) -> String {
        DayKey.weekStart(DayKey.daysAgo(w * 7, from: AppClock.instant))
    }

    private static func date(week w: Int, plus days: Int) -> String {
        let monday = DayKey.date(weekStart(w)) ?? AppClock.instant
        return DayKey.string(Calendar.current.date(byAdding: .day, value: days, to: monday) ?? monday)
    }

    private static func make(_ type: RunType, week w: Int) -> Run {
        let k = Double(weeksBack - w)  // 0 (oldest) ... 7 (last week)
        let id = "run-\(type == .easy ? "e" : (type == .intervals ? "i" : "l"))" + (w == 0 ? "-now" : "\(w - 1)")
        switch type {
        case .intervals:
            // Last week's session is the one the analysis screen shows: rep 4 slow, rep 5 above the HR range.
            let target = planTargetPace + Double((w - 1) * 2)
            let reps = w == 1 || w > 4 ? 5 : 6
            let offsets: [Double] = w == 1 ? [-2, 1, 4, 9, 3] : (w % 3 == 2 ? [6, 8, 8, 9, 9, 10] : [-2, 0, 1, 2, 2, 3])
            var paces: [Double] = []
            var hr: [Double] = []
            for r in 0..<reps {
                paces.append(target + offsets[r])
                hr.append(w == 1 ? 168 + 3 * Double(r) : (167 + 2.2 * Double(r)).rounded())
            }
            let laps = intervalLaps(reps: reps, workPaces: paces, workHr: hr, target: target)
            var dist = 0.0, dur = 0.0, hrTime = 0.0, maxHr = 0.0
            for lap in laps {
                let d = lap.durationS ?? 0
                dist += lap.distanceM ?? 0
                dur += d
                hrTime += (lap.avgHr ?? 0) * d
                maxHr = max(maxHr, lap.maxHr ?? 0)
            }
            let avgHr: Double = hrTime / dur
            return Run(id: id, type: type, week: w, date: date(week: w, plus: 4), distanceM: dist, durationS: dur,
                       avgHr: avgHr.rounded(), maxHr: maxHr, reps: reps, workPaces: paces, workHr: hr,
                       targetPace: target, recoveryOk: w != 4)
        case .long:
            let km = round1(9 + k * 0.3 + noise(41, w) * 0.4)
            let pace = (356 - k * 1.2 + noise(42, w) * 3).rounded()
            return Run(id: id, type: type, week: w, date: date(week: w, plus: 6), distanceM: km * 1000, durationS: km * pace,
                       avgHr: (146 + noise(43, w) * 2).rounded(), maxHr: 162, reps: 0, workPaces: [], workHr: [], targetPace: 0, recoveryOk: true)
        default:
            let km = round1(7 + k * 0.15 + noise(31, w) * 0.3)
            let pace = (352 - k * 1.2 + noise(32, w) * 3).rounded()
            return Run(id: id, type: .easy, week: w, date: date(week: w, plus: 2), distanceM: km * 1000, durationS: km * pace,
                       avgHr: (144 - k * 0.3 + noise(33, w) * 2).rounded(), maxHr: 158, reps: 0, workPaces: [], workHr: [], targetPace: 0,
                       recoveryOk: true)
        }
    }

    /// All completed runs, newest first.
    static func log() -> [Run] {
        let today = AppClock.todayString
        var runs: [Run] = []
        for w in 0...weeksBack {
            for type in [RunType.long, .intervals, .easy] {
                let run = make(type, week: w)
                if run.date < today { runs.append(run) }
            }
        }
        return runs.sorted { $0.date > $1.date }
    }

    static func run(_ id: String) -> Run? { log().first { $0.id == id } }

    // MARK: Laps and verdict

    /// Warm-up 1 km, `reps` x 1 km work with a 400 m jog between, cool-down 0.5 km (the plan's structure).
    static func intervalLaps(reps: Int, workPaces: [Double], workHr: [Double], target: Double) -> [LapDTO] {
        var laps = [LapDTO(index: 1, kind: .warmup, distanceM: 1000, durationS: 350, avgPaceSPerKm: 350, avgHr: 136, maxHr: 148)]
        var idx = 2
        for r in 0..<reps {
            let pace = workPaces[r]
            laps.append(LapDTO(index: idx, kind: .work, distanceM: 1000, durationS: pace, avgPaceSPerKm: pace, avgHr: workHr[r],
                               maxHr: workHr[r] + 6, targetPaceSPerKm: target, targetHrLow: targetHrLow, targetHrHigh: targetHrHigh,
                               paceOk: abs(pace - target) <= 4, hrOk: workHr[r] >= targetHrLow && workHr[r] <= targetHrHigh))
            idx += 1
            if r < reps - 1 {
                laps.append(LapDTO(index: idx, kind: .recovery, distanceM: 400, durationS: 150, avgPaceSPerKm: 375, avgHr: 148 - Double(r), maxHr: 168))
                idx += 1
            }
        }
        laps.append(LapDTO(index: idx, kind: .cooldown, distanceM: 500, durationS: 174, avgPaceSPerKm: 347, avgHr: 140, maxHr: 150))
        return laps
    }

    /// One lap per kilometre (the last one partial) around the run's average pace and heart rate.
    private static func kmLaps(_ run: Run) -> [LapDTO] {
        var laps: [LapDTO] = []
        var left = run.distanceM, idx = 1
        while left > 1 {
            let d = min(1000, left)
            let pace = (run.paceSPerKm + noise(51, idx) * 4 + Double(idx) * 0.3).rounded()
            let hr = (run.avgHr - 4 + Double(idx) * 0.7).rounded()
            laps.append(LapDTO(index: idx, kind: .other, distanceM: d, durationS: pace * d / 1000, avgPaceSPerKm: pace, avgHr: hr, maxHr: hr + 5,
                               targetHrLow: 124, targetHrHigh: easyHrCeiling, hrOk: hr <= easyHrCeiling))
            left -= d
            idx += 1
        }
        return laps
    }

    static func analysis(_ id: String) -> RunAnalysisDTO {
        let run = DemoRuns.run(id) ?? DemoRuns.run("run-i0")!
        let info = RunInfoDTO(id: run.id, date: run.date, type: run.type.rawValue, name: run.title, distanceM: run.distanceM,
                              durationS: run.durationS, avgHr: run.avgHr, maxHr: run.maxHr, avgPaceSPerKm: run.paceSPerKm,
                              trainingEffectAerobic: run.aerobicEffect, trainingEffectAnaerobic: run.type == .intervals ? 2.3 : 0.1)
        if run.type != .intervals {
            let laps = kmLaps(run)
            let calm = laps.filter { $0.hrOk == true }.count
            let share = laps.isEmpty ? 100 : calm * 100 / laps.count
            let verdict = VerdictDTO(passed: share >= 80, summary: "\(run.type == .long ? "Long" : "Easy") run: avg HR \(Int(run.avgHr)), \(share)% at or below \(Int(easyHrCeiling)): "
                                     + (share >= 80 ? "passed." : "not passed."),
                                     reasons: share >= 80 ? [] : ["only \(share)% of the time at/below \(Int(easyHrCeiling)) (need 80%)"])
            return RunAnalysisDTO(generatedAt: AppClock.instant, run: info, planned: nil, laps: laps, verdict: verdict)
        }
        let laps = intervalLaps(reps: run.reps, workPaces: run.workPaces, workHr: run.workHr, target: run.targetPace)
        let work = laps.filter { $0.kind == .work }
        let onPace = work.filter { $0.paceOk == true }.count
        var reasons: [String] = []
        for (i, lap) in work.enumerated() where lap.paceOk != true {
            let delta = Int(((lap.avgPaceSPerKm ?? 0) - run.targetPace).rounded())
            reasons.append("Rep \(i + 1) was \(abs(delta)) s/km \(delta > 0 ? "slower" : "faster") than the \(Fmt.pace(run.targetPace)) target.")
        }
        for (i, lap) in work.enumerated() where lap.hrOk != true {
            reasons.append("Heart rate of rep \(i + 1) (\(Int(lap.avgHr ?? 0)) bpm) was above the \(Int(targetHrLow)) to \(Int(targetHrHigh)) range.")
        }
        if !run.recoveryOk { reasons.append("Heart rate dropped by less than 12 bpm in the recovery jogs.") }
        if reasons.isEmpty { reasons = ["All reps on pace and heart rate in range."] }
        let hrBad = work.filter { $0.hrOk != true }.count
        var summary = "\(onPace) of \(run.reps) work reps on pace"
        if hrBad > 0 { summary += ", heart rate above the range in \(hrBad) \(hrBad == 1 ? "rep" : "reps")" }
        summary += run.passed ? ": passed." : ": not passed."
        return RunAnalysisDTO(generatedAt: AppClock.instant, run: info, planned: nil, laps: laps,
                              verdict: VerdictDTO(passed: run.passed, summary: summary, reasons: reasons))
    }
}
#endif

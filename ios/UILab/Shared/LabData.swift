import SwiftUI

/// Hardcoded placeholder data shared by the three UI experiments. No backend, no persistence.
enum LabData {
    static let today: Date = {
        var c = DateComponents()
        c.year = 2026; c.month = 10; c.day = 28; c.hour = 6; c.minute = 34
        return Calendar.current.date(from: c) ?? .now
    }()

    static func daysAgo(_ n: Int) -> Date {
        Calendar.current.date(byAdding: .day, value: -n, to: today) ?? today
    }

    /// Deterministic pseudo-random number in [0, 1) from an index and a seed (integer hash, no state).
    static func noise(_ i: Int, _ seed: Int) -> Double {
        var z = UInt64(truncatingIfNeeded: i &* 0x9E37_79B1 &+ seed &* 0x85EB_CA6B &+ 0x1234_567)
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        z = z ^ (z >> 31)
        return Double(z >> 11) / Double(UInt64(1) << 53)
    }

    /// Deterministic, irregular series (smoothed random walk around `base` plus the odd outlier).
    /// Index 0 is the oldest value, so a longer series ends with the same shape as a shorter one only
    /// when both are cut with `suffix`. Different seeds give clearly different shapes.
    static func wave(count: Int, base: Double, amp: Double, seed: Double) -> [Double] {
        let s = Int(seed * 1000)
        var x = 0.0
        return (0..<count).map { i in
            let n = (noise(i, s) - 0.5) * 2
            x = 0.6 * x + 0.8 * n
            var spike = 0.0
            let roll = noise(i, s &+ 7919)
            if roll > 0.93 { spike = (noise(i, s &+ 104_729) > 0.5 ? 1.4 : -1.4) }
            return base + amp * (1.3 * x + spike)
        }
    }

    static func startOfDay(_ n: Int) -> Date { Calendar.current.startOfDay(for: daysAgo(n)) }

    /// One realistic value per day for a metric, oldest first, last element = today.
    /// Counts are integers (0/1 lifting days), distance is mostly 0 with run days of 3-9 km.
    static func daily(for m: LabMetric, total: Int = 366) -> [Double] {
        let cal = Calendar.current
        var values: [Double]
        switch m.kind {
        case .average:
            values = wave(count: total, base: m.base, amp: m.amp, seed: Double(m.seed % 97) / 10)
            values = values.map { v in
                var v = max(v, m.floor)
                if m.step > 0 { v = (v / m.step).rounded() * m.step }
                return v
            }
            if let latest = m.latest { values[total - 1] = latest }
        case .sum, .count:
            values = (0..<total).map { i in
                let ago = total - 1 - i
                let weekday = cal.component(.weekday, from: startOfDay(ago)) // 1 = Sunday
                let skipped = noise(i, m.seed) < 0.14
                if m.kind == .count {
                    guard [2, 3, 5].contains(weekday) else { return 0 }
                    let recentDone = ago <= 2 && [2, 3].contains(weekday)
                    return (recentDone || !skipped) ? 1 : 0
                }
                let plan: Double
                switch weekday {
                case 4: plan = 3
                case 6: plan = 7
                case 1: plan = 8.5
                default: return 0
                }
                if ago == 0 || skipped { return 0 }
                return ((plan * (0.9 + 0.22 * noise(i, m.seed &+ 31))) * 10).rounded() / 10
            }
        }
        return values
    }

    // MARK: Readiness

    static let readinessTitle = "Ready"
    static let readinessSentence = "Resting heart rate, HRV and sleep are all in your normal range."
    static let measuredAt = "6:12"

    // MARK: Today

    static let todayRun = LabRun(day: "Wed", title: "Easy Run", symbol: "figure.run",
                                 km: 3, detail: nil, zone: "Zone 1–2", pace: "5:24–6:28", isToday: true)

    static let weekRuns: [LabRun] = [
        todayRun,
        LabRun(day: "Fri", title: "Intervals", symbol: "figure.run", km: 7,
               detail: "3 × 1000 m", zone: "Zone 4", pace: "4:20–4:30", isToday: false),
        LabRun(day: "Sun", title: "Long Run", symbol: "figure.run", km: 8.5,
               detail: nil, zone: "Zone 1–2", pace: "5:50–6:45", isToday: false),
    ]

    static let week: [LabDay] = [
        LabDay(letter: "M", name: "Mon", title: "Upper", kind: .lift, done: true, isToday: false),
        LabDay(letter: "T", name: "Tue", title: "Lower", kind: .lift, done: true, isToday: false),
        LabDay(letter: "W", name: "Wed", title: "Easy Run · 3 km", kind: .run, done: false, isToday: true),
        LabDay(letter: "T", name: "Thu", title: "Upper", kind: .lift, done: false, isToday: false),
        LabDay(letter: "F", name: "Fri", title: "Intervals · 7 km", kind: .run, done: false, isToday: false),
        LabDay(letter: "S", name: "Sat", title: "Rest", kind: .rest, done: false, isToday: false),
        LabDay(letter: "S", name: "Sun", title: "Long Run · \(8.5.oneDecimal) km", kind: .run, done: false, isToday: false),
    ]

    static let nextWorkout = "Upper"
    static let nextWorkoutDay = "Thursday"

    // MARK: Workouts

    static let recentWorkouts: [LabWorkout] = [
        workout("Lower", 1, 66, [
            ex("Back Squat", 100, [5, 5, 5, 5]),
            ex("Romanian Deadlift", 80, [8, 8, 8]),
            ex("Leg Press", 110, [10, 10, 10], pr: 2),
            ex("Calf Raise", 40, [10, 10, 10]),
        ]),
        workout("Upper", 2, 67, [
            ex("Bench Press", 82.5, [5, 5, 5, 5]),
            ex("Barbell Row", 70, [8, 8, 8, 8]),
            ex("Overhead Press", 50, [6, 6, 5]),
            ex("Lat Pulldown", 60, [10, 10, 10]),
            ex("Dumbbell Curl", 14, [10, 10, 10]),
        ]),
        workout("Upper", 5, 67, [
            ex("Bench Press", 80, [5, 5, 5, 5]),
            ex("Barbell Row", 70, [8, 8, 8, 8]),
            ex("Overhead Press", 50, [6, 5, 5]),
            ex("Lat Pulldown", 60, [10, 10, 10]),
            ex("Dumbbell Curl", 14, [10, 10, 10]),
        ]),
        workout("Lower", 7, 58, [
            ex("Back Squat", 100, [5, 5, 5, 5]),
            ex("Romanian Deadlift", 80, [8, 8, 8]),
            ex("Leg Press", 110, [10, 10, 10]),
            ex("Calf Raise", 40, [8, 8, 8]),
        ]),
        workout("Upper", 12, 76, [
            ex("Bench Press", 80, [5, 5, 5, 4]),
            ex("Barbell Row", 70, [8, 8, 8, 8], pr: 3),
            ex("Overhead Press", 50, [5, 5, 5]),
            ex("Lat Pulldown", 55, [10, 10, 10]),
            ex("Dumbbell Curl", 14, [10, 10, 8]),
        ]),
        workout("Upper", 15, 72, [
            ex("Bench Press", 77.5, [5, 5, 5, 5]),
            ex("Barbell Row", 67.5, [8, 8, 8, 8]),
            ex("Overhead Press", 50, [5, 5, 4]),
            ex("Lat Pulldown", 55, [10, 10, 10]),
            ex("Dumbbell Curl", 14, [10, 10, 8]),
        ]),
        workout("Lower", 16, 57, [
            ex("Back Squat", 97.5, [5, 5, 5, 5]),
            ex("Romanian Deadlift", 80, [8, 8, 8]),
            ex("Leg Press", 105, [10, 10, 10]),
            ex("Calf Raise", 40, [8, 8, 8]),
        ]),
    ]

    /// Builds a workout whose volume and PR count come from its exercises.
    private static func workout(_ title: String, _ ago: Int, _ minutes: Int, _ exercises: [LabExercise]) -> LabWorkout {
        LabWorkout(title: title, date: daysAgo(ago), minutes: minutes,
                   volumeKg: Int(exercises.reduce(0) { $0 + $1.volumeKg }),
                   prs: exercises.filter(\.hasPR).count, exercises: exercises)
    }

    /// `pr` is the 1-based set number that set a personal record.
    private static func ex(_ name: String, _ kg: Double, _ reps: [Int], pr: Int? = nil) -> LabExercise {
        LabExercise(name: name, sets: reps.enumerated().map { LabSet(kg: kg, reps: $0.element, isPR: $0.offset + 1 == pr) })
    }

    // MARK: Plan

    /// Template per workout day; `weekday` is the usual day.
    static let workoutTemplates: [LabWorkoutTemplate] = [
        LabWorkoutTemplate(name: "Upper", weekday: "Thursday", minutes: 65, exercises: [
            LabPlannedExercise(name: "Bench Press", sets: 4, reps: 5, kg: 85, note: "+2.5 kg"),
            LabPlannedExercise(name: "Barbell Row", sets: 4, reps: 8, kg: 70),
            LabPlannedExercise(name: "Overhead Press", sets: 3, reps: 6, kg: 50),
            LabPlannedExercise(name: "Lat Pulldown", sets: 3, reps: 10, kg: 60),
            LabPlannedExercise(name: "Dumbbell Curl", sets: 3, reps: 10, kg: 14),
        ]),
        LabWorkoutTemplate(name: "Lower", weekday: "Tuesday", minutes: 60, exercises: [
            LabPlannedExercise(name: "Back Squat", sets: 4, reps: 5, kg: 100, note: "Hold"),
            LabPlannedExercise(name: "Romanian Deadlift", sets: 3, reps: 8, kg: 82.5),
            LabPlannedExercise(name: "Leg Press", sets: 3, reps: 10, kg: 110),
            LabPlannedExercise(name: "Calf Raise", sets: 3, reps: 10, kg: 40),
        ]),
    ]

    /// Exercises of the next workout ("Upper", Thursday).
    static var nextWorkoutPlan: [LabPlannedExercise] {
        workoutTemplates.first { $0.name == nextWorkout }?.exercises ?? []
    }

    // MARK: Lifting stats

    static let sessionsDone = 2
    static let sessionsPlanned = 3
    static let prs30d = 1
    static let liftsProgressing = 1
    static let liftsTotal = 4
    static let consistency = 0.42
    static let sessionsPerWeek: [Int] = [1, 2, 0, 0, 1, 3, 2, 2]
    static let prWeeks: Set<Int> = [1, 7]

    static let lifts: [LabLift] = [
        LabLift(name: "Bench Press", kg: 82.5, change: 2.5, status: .progressing,
                trend: [75, 75, 77.5, 77.5, 80, 80, 80, 82.5],
                e1rmHistory: [87.5, 87.5, 88.0, 90.4, 90.0, 93.3, 93.3, 92.5, 93.3, 96.3],
                next: LabTarget(kg: 85, reps: 5, sets: 4)),
        LabLift(name: "Back Squat", kg: 100, change: 0, status: .holding,
                trend: [95, 97.5, 97.5, 100, 100, 100, 100, 100],
                e1rmHistory: [110.8, 110.8, 113.8, 113.8, 116.7, 115.0, 116.7, 116.7, 115.5, 116.7],
                next: LabTarget(kg: 100, reps: 5, sets: 4)),
        LabLift(name: "Barbell Row", kg: 70, change: 0, status: .holding,
                trend: [65, 67.5, 67.5, 70, 70, 70, 70, 70],
                e1rmHistory: [82.3, 82.3, 85.5, 85.5, 86.0, 88.7, 88.7, 87.5, 88.7, 88.7],
                next: LabTarget(kg: 70, reps: 8, sets: 4)),
        LabLift(name: "Overhead Press", kg: 50, change: 0, status: .holding,
                trend: [47.5, 47.5, 50, 50, 50, 50, 50, 50],
                e1rmHistory: [57.0, 57.0, 57.0, 60.0, 60.0, 58.5, 60.0, 60.0, 59.0, 60.0],
                next: LabTarget(kg: 50, reps: 6, sets: 3)),
    ]

    // MARK: Running stats

    static let weekKmDone = 0.0
    static let weekKmPlanned = 18.5
    static let weeklyKm: [Double] = [12, 14.5, 9, 16, 15, 17.5, 18, 16.5]
    static let easyPace = "6:05"
    static let vdot = 46.2

    // MARK: Health

    static let hrv = 66, hrv28 = 68
    static let restingHR = 53, restingHR28 = 54, restingHRToday = 54
    static let sleep = 7.4, sleep28 = 7.2
    static let hrvSeries = Array(daily(for: metric("hrv")).suffix(30))
    static let rhrSeries = Array(daily(for: metric("rhr")).suffix(30))
    static let sleepSeries = Array(daily(for: metric("sleep")).suffix(30))

    // MARK: Coach

    static let coachCallsUsed = 1
    static let coachCallsLimit = 4
    static let coachVersion = "v3.10"
    static let coachHistory: [LabCoachEntry] = [
        LabCoachEntry(title: "Hold squat at 100 kg", date: daysAgo(6), outcome: "Approved"),
        LabCoachEntry(title: "Move Friday intervals to Saturday", date: daysAgo(13), outcome: "Rejected"),
        LabCoachEntry(title: "Weekly check-in", date: daysAgo(20), outcome: "No changes"),
    ]
    static let pendingProposal = LabCoachEntry(
        title: "Hold squat at 100 kg for one more week",
        date: daysAgo(1), outcome: "Two of your last three sessions ended at RPE 9.5.")

    // MARK: Metrics with detail charts

    static let metrics: [String: LabMetric] = Dictionary(uniqueKeysWithValues: [
        LabMetric(id: "hrv", title: "Heart Rate Variability", short: "HRV", symbol: "waveform.path.ecg",
                  color: .pink, unit: "ms", value: "\(hrv)", base: 79, amp: 7, higherIsBetter: true,
                  style: .line, baseline: Double(hrv28), latest: Double(hrv), step: 1, floor: 40,
                  about: "Overnight HRV from your watch. Higher than your own baseline usually means you have recovered well. The shaded band is within 10 % of your 28-day average."),
        LabMetric(id: "rhr", title: "Resting Heart Rate", short: "Resting HR", symbol: "heart.fill",
                  color: .pink, unit: "bpm", value: "\(restingHR)", base: 53.5, amp: 1.5, higherIsBetter: false,
                  style: .line, baseline: Double(restingHR28), latest: Double(restingHR), step: 1, floor: 38,
                  about: "Your lowest sustained heart rate overnight. A rise of a few beats above baseline can mean fatigue, illness or poor sleep. The shaded band is within 10 % of your 28-day average."),
        LabMetric(id: "sleep", title: "Sleep", short: "Sleep", symbol: "bed.double.fill",
                  color: .cyan, unit: "h", value: sleep.oneDecimal, base: 8.1, amp: 0.5, higherIsBetter: true,
                  style: .bar, latest: sleep, step: 0.1, floor: 4.5,
                  about: "Time asleep per night. Readiness considers the last three nights."),
        LabMetric(id: "km", title: "Running Distance", short: "Running", symbol: "figure.run",
                  color: .green, unit: "km", value: weeklyKm.last!.oneDecimal, base: 15, amp: 3,
                  higherIsBetter: true, style: .bar, kind: .sum,
                  about: "Kilometres per week. Planned runs may change based on your last run, readiness and weekly volume."),
        LabMetric(id: "sessions", title: "Lifting Sessions", short: "Sessions", symbol: "dumbbell.fill",
                  color: .orange, unit: "of 3", value: "\(sessionsDone)", base: 2, amp: 1,
                  higherIsBetter: true, style: .bar, kind: .count, detailUnit: "sessions",
                  about: "Lifting sessions done per week against the plan. Consistency over the last 8 weeks: 42 %."),
    ].map { ($0.id, $0) })

    static func metric(_ id: String) -> LabMetric { metrics[id]! }
}

// MARK: - Models

struct LabRun: Identifiable {
    var id: String { day }
    let day: String
    let title: String
    let symbol: String
    let km: Double
    let detail: String?
    let zone: String
    let pace: String
    let isToday: Bool

    var summary: String {
        [km.oneDecimal + " km", detail, zone].compactMap { $0 }.joined(separator: " · ")
    }
}

struct LabDay: Identifiable {
    enum Kind { case lift, run, rest }
    let id = UUID()
    let letter: String
    let name: String
    let title: String
    let kind: Kind
    let done: Bool
    let isToday: Bool
}

struct LabWorkout: Identifiable {
    let id = UUID()
    let title: String
    let date: Date
    let minutes: Int
    let volumeKg: Int
    let prs: Int
    var exercises: [LabExercise] = []
}

struct LabSet: Identifiable {
    let id = UUID()
    let kg: Double
    let reps: Int
    var isPR = false
    /// Epley estimate.
    var e1rm: Double { kg * (1 + Double(reps) / 30) }
}

struct LabExercise: Identifiable {
    let id = UUID()
    let name: String
    let sets: [LabSet]
    var hasPR: Bool { sets.contains(where: \.isPR) }
    var volumeKg: Double { sets.reduce(0) { $0 + $1.kg * Double($1.reps) } }
    /// Heaviest set, e.g. "82.5 kg × 5".
    var topSetText: String {
        guard let top = sets.max(by: { $0.kg < $1.kg }) else { return "" }
        return "\(top.kg.oneDecimal) kg × \(top.reps)"
    }
}

/// Target for a lift's next session: `sets` × `reps` at `kg`.
struct LabTarget {
    let kg: Double
    let reps: Int
    var sets = 3
    /// "85 kg × 5"
    var text: String { "\(kg.oneDecimal) kg × \(reps)" }
}

struct LabPlannedExercise: Identifiable {
    var id: String { name }
    let name: String
    let sets: Int
    let reps: Int
    let kg: Double
    var note: String? = nil
    /// "4 × 5"
    var setsText: String { "\(sets) × \(reps)" }
}

struct LabWorkoutTemplate: Identifiable {
    var id: String { name }
    let name: String
    let weekday: String
    let minutes: Int
    let exercises: [LabPlannedExercise]
}

struct LabLift: Identifiable {
    enum Status { case progressing, holding, stalled }
    var id: String { name }
    let name: String
    let kg: Double
    let change: Double
    let status: Status
    let trend: [Double]
    /// Estimated 1RM per week, oldest first, last = this week.
    var e1rmHistory: [Double] = []
    var next: LabTarget? = nil
}

struct LabCoachEntry: Identifiable {
    let id = UUID()
    let title: String
    let date: Date
    let outcome: String
}

struct LabMetric: Identifiable {
    enum Style { case line, bar }
    let id: String
    let title: String
    let short: String
    let symbol: String
    let color: Color
    let unit: String
    let value: String
    let base: Double
    let amp: Double
    let higherIsBetter: Bool
    let style: Style
    /// Value the 28-day baseline band is drawn around (HRV, resting HR).
    var baseline: Double? = nil
    /// Today's value, so the generated series ends on the number the card shows.
    var latest: Double? = nil
    /// Rounding step for generated daily values (0 = none).
    var step: Double = 0
    var floor: Double = 0
    var kind: Kind = .average
    /// Unit shown on the detail screen when it differs from `unit` ("of 3" -> "sessions").
    var detailUnit: String? = nil
    let about: String

    /// How daily values combine into a week or month: mean (HRV...), total (km), or integer count (sessions).
    enum Kind { case average, sum, count }

    /// Stable per-metric number for deterministic noise.
    var seed: Int { id.unicodeScalars.reduce(7) { ($0 &* 31 &+ Int($1.value)) & 0xFFFF } }

    /// Last 30 days, one value each (for sparklines).
    var series: [Double] { Array(LabData.daily(for: self).suffix(30)) }
}

// MARK: - Formatting

extension Double {
    /// "8,5" / "8.5" following the locale, "3" without a trailing ",0".
    var oneDecimal: String { formatted(.number.precision(.fractionLength(0...1))) }
}

enum LabFormat {
    /// "1:06" like Apple Fitness.
    static func duration(minutes: Int) -> String {
        Duration.seconds(minutes * 60).formatted(.time(pattern: .hourMinute))
    }

    /// Locale-correct "Tuesday 27 Oct" / "Dienstag, 27. Okt.".
    static func day(_ date: Date) -> String {
        date.formatted(.dateTime.weekday(.wide).day().month(.abbreviated))
    }

    static func relative(_ date: Date) -> String {
        let days = Calendar.current.dateComponents([.day], from: date, to: LabData.today).day ?? 0
        switch days {
        case 0: return "Today"
        case 1: return "Yesterday"
        case 2..<7: return date.formatted(.dateTime.weekday(.wide))
        default: return date.formatted(.dateTime.day().month(.abbreviated))
        }
    }

    static func kg(_ value: Int) -> String {
        value.formatted(.number.grouping(.automatic))
    }
}

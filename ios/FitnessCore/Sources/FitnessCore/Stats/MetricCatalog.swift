import Foundation

/// The metrics that can be pinned on Summary and opened in the W / M / 6M / Y detail chart, built from REAL stats only:
/// `/stats/health` (HRV, resting HR, sleep, steps, server bodyweight), the locally logged weights and
/// `/stats/volume/weekly` (running km, lifting sessions and volume per week).
public enum MetricCatalog: Sendable {
    public static let hrv = MetricInfo(
        id: "hrv", title: "Heart Rate Variability", short: "HRV", symbol: "waveform.path.ecg", domain: .heart,
        unit: "ms", higherIsBetter: true,
        about: "Overnight HRV from your watch. Compare it with your own 28-day average, not with other people: above it usually means you are well recovered.")
    public static let restingHR = MetricInfo(
        id: "rhr", title: "Resting Heart Rate", short: "Resting HR", symbol: "heart.fill", domain: .heart,
        unit: "bpm", higherIsBetter: false,
        about: "Resting heart rate from your watch. A few beats above your 28-day average can mean fatigue, a short night or a cold coming.")
    public static let sleep = MetricInfo(
        id: "sleep", title: "Sleep", short: "Sleep", symbol: "bed.double.fill", domain: .sleep,
        unit: "h", decimals: 1, higherIsBetter: true,
        about: "Time asleep per night from your watch. The trend over a week matters more than a single night.")
    public static let steps = MetricInfo(
        id: "steps", title: "Steps", short: "Steps", symbol: "figure.walk", domain: .activity,
        unit: "steps", style: .bar, higherIsBetter: true,
        about: "Daily step count from your watch. The detail chart shows the average per day of each period.")
    public static let bodyweight = MetricInfo(
        id: "bodyweight", title: "Bodyweight", short: "Bodyweight", symbol: "figure", domain: .body,
        unit: "kg", decimals: 1,
        about: "Weights you logged and from your scale, one per day. Day-to-day changes are mostly water; look at the monthly trend.")
    public static let runningDistance = MetricInfo(
        id: "km", title: "Running Distance", short: "Running", symbol: "figure.run", domain: .running,
        unit: "km", decimals: 1, aggregation: .total, granularity: .weekly, style: .bar, higherIsBetter: true,
        about: "Kilometres run per week (Monday to Sunday), from your watch.")
    public static let liftingSessions = MetricInfo(
        id: "sessions", title: "Lifting Sessions", short: "Lifting", symbol: "dumbbell.fill", domain: .lifting,
        unit: "sessions", aggregation: .total, granularity: .weekly, style: .bar, higherIsBetter: true,
        about: "Finished lifting workouts per week (Monday to Sunday).")
    public static let liftingVolume = MetricInfo(
        id: "volume", title: "Lifting Volume", short: "Volume", symbol: "scalemass.fill", domain: .lifting,
        unit: "t", decimals: 1, aggregation: .total, granularity: .weekly, style: .bar, higherIsBetter: true,
        about: "Weight times reps of all logged sets per week, in tonnes.")

    /// Default order of the editor's list.
    public static let all: [MetricInfo] = [hrv, restingHR, sleep, liftingSessions, runningDistance, bodyweight, steps, liftingVolume]
    public static let defaultPinned: [String] = ["hrv", "rhr", "sleep", "sessions"]

    public static func info(_ id: String) -> MetricInfo? { all.first { $0.id == id } }

    /// Everything the catalog reads from. Any part may be missing (not loaded yet / offline without cache).
    public struct Sources: Sendable {
        public var health: HealthStatsDTO?
        public var volume: WeeklyVolumeDTO?
        /// Server and locally logged weights merged (one per day).
        public var bodyweight: [BodyweightEntry]

        public init(health: HealthStatsDTO?, volume: WeeklyVolumeDTO?, localWeights: [LocalBodyweight] = []) {
            self.health = health
            self.volume = volume
            bodyweight = BodyweightMerge.merge(server: health?.bodyweight ?? [], local: localWeights)
        }
    }

    public static func points(_ metric: MetricInfo, from s: Sources) -> [MetricPoint] {
        let days = s.health?.days ?? []
        let weeks = s.volume?.weeks ?? []
        func daily(_ value: (HealthDayDTO) -> Double?) -> [MetricPoint] {
            days.compactMap { d in value(d).map { MetricPoint(day: String(d.date.prefix(10)), value: $0) } }
        }
        func weekly(_ value: (WeekVolumeDTO) -> Double?) -> [MetricPoint] {
            weeks.compactMap { w in value(w).map { MetricPoint(day: String(w.weekStart.prefix(10)), value: $0) } }
        }
        switch metric.id {
        case hrv.id: return daily(\.hrvMs)
        case restingHR.id: return daily(\.rhr)
        case sleep.id: return daily { HealthTransforms.sleepHours($0.sleepS) }
        case steps.id: return daily(\.steps)
        case bodyweight.id: return s.bodyweight.map { MetricPoint(day: $0.day, value: $0.weightKg) }
        case runningDistance.id: return weekly(\.runningKm)
        case liftingSessions.id: return weekly { $0.liftingSessions.map(Double.init) }
        case liftingVolume.id: return weekly { $0.liftingVolumeKg.map { $0 / 1000 } }
        default: return []
        }
    }

    /// The 28-day average drawn as a dashed line on the detail chart (HRV, resting HR, sleep), when the backend sends it.
    public static func baseline(_ metric: MetricInfo, summary: HealthSummaryDTO?) -> Double? {
        switch metric.id {
        case hrv.id: summary?.hrv28d
        case restingHR.id: summary?.rhr28d
        case sleep.id: HealthTransforms.sleepHours(summary?.sleep28dS)
        default: nil
        }
    }
}

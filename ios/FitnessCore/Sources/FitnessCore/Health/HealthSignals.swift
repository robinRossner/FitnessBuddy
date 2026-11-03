import Foundation

/// Pure logic of the Health tab's "Signals": cutting a metric's points to the picked period and averaging them.
public enum HealthSignals: Sendable {
    /// The period picker above the signal cards.
    public enum Span: Int, CaseIterable, Identifiable, Sendable {
        case week = 7, month = 30, quarter = 90
        public var id: Int { rawValue }
        public var label: String { "\(rawValue) Days" }
        /// "30-Day Avg"
        public var averageLabel: String { "\(rawValue)-Day Avg" }
    }

    /// Points of the last `days` days (today and the days before), oldest first.
    public static func window(_ points: [MetricPoint], days: Int, now: Date = AppClock.instant, calendar: Calendar = .current) -> [MetricPoint] {
        let from = DayKey.string(DayKey.daysAgo(days - 1, from: now, calendar: calendar), calendar: calendar)
        return points.filter { String($0.day.prefix(10)) >= from }.sorted { $0.day < $1.day }
    }

    /// Mean over the points (nil when there are none).
    public static func average(_ points: [MetricPoint]) -> Double? {
        MetricSeries.combine(points.map(\.value), .average)
    }

    /// Percent difference of `value` from `baseline` (nil when either is missing or the baseline is zero).
    public static func deltaPct(_ value: Double?, baseline: Double?) -> Double? {
        guard let value, let baseline, baseline != 0 else { return nil }
        return (value - baseline) / baseline * 100
    }

    /// First and last instant a chart of `days` days covers: the start of the first day to the end of today.
    public static func chartDomain(days: Int, now: Date = AppClock.instant, calendar: Calendar = .current) -> ClosedRange<Date> {
        let start = calendar.startOfDay(for: DayKey.daysAgo(days - 1, from: now, calendar: calendar))
        let end = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: now)) ?? now
        return start...end
    }

    /// Vertical range of a signal chart: bars start at zero, lines zoom in on the data (and the average) with some air.
    public static func yDomain(values: [Double], average: Double?, fromZero: Bool) -> ClosedRange<Double> {
        let all = values + (average.map { [$0] } ?? [])
        guard let lo = all.min(), let hi = all.max() else { return 0...1 }
        if fromZero { return 0...max(hi * 1.1, 1) }
        let pad = max((hi - lo) * 0.2, abs(hi) * 0.01, 0.1)
        return (lo - pad)...(hi + pad)
    }
}

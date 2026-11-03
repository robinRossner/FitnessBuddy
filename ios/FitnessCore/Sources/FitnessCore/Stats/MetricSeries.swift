import Foundation

// Metric time series for the summary cards and the W / M / 6M / Y detail chart. Pure: no views, no network.
// `MetricCatalog` builds the real series from the stats payloads; `MetricSeries` cuts them into chart buckets.

/// One value of a metric: per day ("YYYY-MM-DD") for daily metrics, per week (the week's Monday) for weekly ones.
public struct MetricPoint: Equatable, Sendable, Identifiable {
    public var day: String
    public var value: Double
    public var id: String { day }
    public var date: Date { DayKey.date(day) ?? .distantPast }

    public init(day: String, value: Double) { self.day = day; self.value = value }
}

/// How values combine into a week or month: a mean (HRV, sleep, bodyweight) or a sum (km, sessions).
public enum MetricAggregation: Sendable, Hashable { case average, total }
/// The resolution the data comes in.
public enum MetricGranularity: Sendable, Hashable { case daily, weekly }
public enum MetricChartStyle: Sendable, Hashable { case line, bar }

/// What a metric is and how it is shown. The colour is `domain.color`.
public struct MetricInfo: Identifiable, Hashable, Sendable {
    public var id: String
    /// "Heart Rate Variability" (detail title, editor).
    public var title: String
    /// "HRV" (card header).
    public var short: String
    public var symbol: String
    public var domain: Domain
    public var unit: String
    public var decimals: Int = 0
    public var aggregation: MetricAggregation = .average
    public var granularity: MetricGranularity = .daily
    public var style: MetricChartStyle = .line
    /// nil when neither direction is better (bodyweight).
    public var higherIsBetter: Bool? = nil
    /// One or two sentences for the detail screen's "About" section.
    public var about: String = ""

    /// Ranges that make sense for the data: weekly data has no W.
    public var ranges: [MetricRange] { granularity == .daily ? MetricRange.allCases : [.month, .halfYear, .year] }

    public func format(_ value: Double?) -> String {
        guard let value else { return "–" }
        return value.formatted(.number.precision(.fractionLength(decimals)))
    }
}

public enum MetricRange: String, CaseIterable, Identifiable, Sendable {
    case week = "W", month = "M", halfYear = "6M", year = "Y"
    public var id: String { rawValue }
}

/// One bar / line point of the detail chart. `key` is unique and categorical (Charts needs a String x for ratio bars).
public struct MetricBucket: Identifiable, Equatable, Sendable {
    public var key: String
    public var start: Date
    /// nil = no data in this period (drawn as a gap).
    public var value: Double?
    /// x-axis label, nil = none.
    public var label: String?
    public var id: String { key }
}

public enum MetricSeries: Sendable {
    public static func combine(_ values: [Double], _ aggregation: MetricAggregation) -> Double? {
        guard !values.isEmpty else { return nil }
        let sum = values.reduce(0, +)
        return aggregation == .total ? sum : sum / Double(values.count)
    }

    /// Chart buckets, oldest first, always the full range (missing periods have a nil value).
    /// Daily data: W = 7 days, M = 30 days, 6M = 26 weeks, Y = 12 months. Weekly data: M = 4 weeks, 6M = 26 weeks,
    /// Y = 12 months. Weeks start on Monday (like the backend), months are calendar months.
    public static func buckets(_ points: [MetricPoint], metric: MetricInfo, range: MetricRange,
                        now: Date = AppClock.instant, calendar: Calendar = .current) -> [MetricBucket] {
        var byDay: [String: [Double]] = [:]
        for p in points { byDay[String(p.day.prefix(10)), default: []].append(p.value) }
        let agg = metric.aggregation
        let dayMonth = Date.FormatStyle.dateTime.day().month(.abbreviated)

        func day(_ n: Int) -> Date { DayKey.daysAgo(n, from: now, calendar: calendar) }
        func dayBucket(_ n: Int, label: String?) -> MetricBucket {
            let d = day(n)
            return MetricBucket(key: "d\(n)", start: d, value: combine(byDay[DayKey.string(d, calendar: calendar)] ?? [], agg),
                                label: label)
        }
        /// Monday `w` weeks before this week's.
        func weekStart(_ w: Int) -> Date {
            let thisWeek = DayKey.date(DayKey.weekStart(now, calendar: calendar), calendar: calendar) ?? day(0)
            return calendar.date(byAdding: .day, value: -7 * w, to: thisWeek) ?? thisWeek
        }
        func weekValues(_ start: Date) -> [Double] {
            switch metric.granularity {
            case .weekly:
                return byDay[DayKey.string(start, calendar: calendar)] ?? []
            case .daily:
                let daily = (0..<7).compactMap { i -> Double? in
                    guard let d = calendar.date(byAdding: .day, value: i, to: start) else { return nil }
                    return combine(byDay[DayKey.string(d, calendar: calendar)] ?? [], agg)
                }
                return daily
            }
        }
        func weekBuckets(_ count: Int, label: (Int, Date) -> String?) -> [MetricBucket] {
            (0..<count).reversed().enumerated().map { i, w in
                let start = weekStart(w)
                return MetricBucket(key: "w\(w)", start: start, value: combine(weekValues(start), agg), label: label(i, start))
            }
        }

        switch (range, metric.granularity) {
        case (.week, .daily):
            return (0..<7).reversed().map { dayBucket($0, label: day($0).formatted(.dateTime.weekday(.abbreviated))) }
        case (.month, .daily):
            return (0..<30).reversed().map { n in dayBucket(n, label: (29 - n) % 7 == 0 ? day(n).formatted(dayMonth) : nil) }
        case (.week, .weekly), (.month, .weekly):
            return weekBuckets(4) { _, start in start.formatted(dayMonth) }
        case (.halfYear, _):
            var lastMonth: Int?
            var lastLabel = -10
            return weekBuckets(26) { i, start in
                let month = calendar.component(.month, from: start)
                guard month != lastMonth, i - lastLabel >= 3 else { return nil }
                lastMonth = month
                lastLabel = i
                return start.formatted(.dateTime.month(.abbreviated))
            }
        case (.year, _):
            let thisMonth = calendar.dateInterval(of: .month, for: now)?.start ?? day(0)
            return (0..<12).reversed().map { m in
                let start = calendar.date(byAdding: .month, value: -m, to: thisMonth) ?? thisMonth
                let prefix = String(DayKey.string(start, calendar: calendar).prefix(7))
                let values: [Double]
                switch metric.granularity {
                case .weekly:
                    values = byDay.filter { $0.key.hasPrefix(prefix) }.flatMap(\.value)
                case .daily:
                    // a month of a total metric sums its days; an average metric averages its daily values
                    values = byDay.filter { $0.key.hasPrefix(prefix) }.compactMap { combine($0.value, agg) }
                }
                return MetricBucket(key: "m\(m)", start: start, value: combine(values, agg),
                                    label: start.formatted(.dateTime.month(.narrow)))
            }
        }
    }

    /// The big number above the chart: the total of the range (total metrics) or the mean of the periods with data.
    public static func headline(_ buckets: [MetricBucket], aggregation: MetricAggregation) -> Double? {
        combine(buckets.compactMap(\.value), aggregation)
    }

    public static func headlineTitle(_ metric: MetricInfo) -> String { metric.aggregation == .total ? "Total" : "Average" }

    public static func caption(_ metric: MetricInfo, range: MetricRange) -> String {
        switch (range, metric.granularity) {
        case (.week, .daily): "Last 7 days"
        case (.month, .daily): "Last 30 days"
        case (.week, .weekly), (.month, .weekly): "Last 4 weeks, per week"
        case (.halfYear, _): "Last 6 months, per week"
        case (.year, _): "Last 12 months, per month"
        }
    }

    /// Latest point (by day).
    public static func latest(_ points: [MetricPoint]) -> MetricPoint? { points.max { $0.day < $1.day } }

    /// Values for a card sparkline, oldest first: the last 14 days with data (daily) or the last 8 weeks (weekly,
    /// missing weeks of a total count as 0). Empty when there is not enough to draw a line.
    public static func sparkline(_ points: [MetricPoint], metric: MetricInfo, now: Date = AppClock.instant, calendar: Calendar = .current) -> [Double] {
        switch metric.granularity {
        case .daily:
            let from = DayKey.string(DayKey.daysAgo(13, from: now, calendar: calendar), calendar: calendar)
            var byDay: [String: [Double]] = [:]
            for p in points where p.day >= from { byDay[p.day, default: []].append(p.value) }
            let values = byDay.keys.sorted().compactMap { combine(byDay[$0] ?? [], metric.aggregation) }
            return values.count >= 2 ? values : []
        case .weekly:
            let thisWeek = DayKey.date(DayKey.weekStart(now, calendar: calendar), calendar: calendar) ?? now
            var byWeek: [String: [Double]] = [:]
            for p in points { byWeek[p.day, default: []].append(p.value) }
            let keys = (0..<8).reversed().compactMap { w in
                calendar.date(byAdding: .day, value: -7 * w, to: thisWeek).map { DayKey.string($0, calendar: calendar) }
            }
            guard keys.contains(where: { byWeek[$0] != nil }) else { return [] }
            let values = keys.map { key -> Double? in
                combine(byWeek[key] ?? [], metric.aggregation) ?? (metric.aggregation == .total ? 0 : nil)
            }.compactMap { $0 }
            return values.count >= 2 ? values : []
        }
    }

    /// When the card's value is from: "Today", "Yesterday", "Mon 26 Oct"; weekly: "This Week", "Last Week", "Week of 12 Oct".
    public static func timeLabel(_ point: MetricPoint?, metric: MetricInfo, now: Date = AppClock.instant, calendar: Calendar = .current) -> String? {
        guard let point, let date = DayKey.date(point.day, calendar: calendar) else { return nil }
        switch metric.granularity {
        case .weekly:
            let thisWeek = DayKey.weekStart(now, calendar: calendar)
            if point.day == thisWeek { return "This Week" }
            let lastWeek = DayKey.weekStart(DayKey.daysAgo(7, from: now, calendar: calendar), calendar: calendar)
            if point.day == lastWeek { return "Last Week" }
            return "Week of " + date.formatted(.dateTime.day().month(.abbreviated))
        case .daily:
            if calendar.isDate(date, inSameDayAs: now) { return "Today" }
            if let y = calendar.date(byAdding: .day, value: -1, to: now), calendar.isDate(date, inSameDayAs: y) { return "Yesterday" }
            return date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        }
    }
}

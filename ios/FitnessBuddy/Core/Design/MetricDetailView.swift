import FitnessCore
import Charts
import SwiftData
import SwiftUI

/// Health-style detail of one metric: W / M / 6M / Y picker, the average (or total) of the range, a bar or line chart
/// and an About section. Data comes in as points; see `LiveMetricDetailView` for the catalog metrics with real data.
struct MetricDetailView: View {
    let metric: MetricInfo
    let points: [MetricPoint]
    /// Dashed reference line, e.g. the 28-day average (line metrics on W and M only).
    var baseline: Double? = nil
    /// Shown under the chart while more history is loading.
    var isLoading = false
    @State private var range: MetricRange

    init(metric: MetricInfo, points: [MetricPoint], baseline: Double? = nil, isLoading: Bool = false,
         initialRange: MetricRange = .month) {
        self.metric = metric
        self.points = points
        self.baseline = baseline
        self.isLoading = isLoading
        _range = State(initialValue: metric.ranges.contains(initialRange) ? initialRange : (metric.ranges.first ?? .month))
    }

    var body: some View {
        let data = MetricSeries.buckets(points, metric: metric, range: range)
        let headline = MetricSeries.headline(data, aggregation: metric.aggregation)
        List {
            Section {
                VStack(alignment: .leading, spacing: 16) {
                    Picker("Range", selection: $range) {
                        ForEach(metric.ranges) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(MetricSeries.headlineTitle(metric)).font(.subheadline).foregroundStyle(.secondary)
                        MetricText(value: metric.format(headline), unit: metric.unit, size: .largeTitle)
                        Text(MetricSeries.caption(metric, range: range)).font(.subheadline).foregroundStyle(.secondary)
                    }

                    if data.allSatisfy({ $0.value == nil }) {
                        ChartEmpty(symbol: metric.symbol, message: isLoading ? "Loading…" : "No data in this period.", height: 220)
                    } else {
                        MetricChart(metric: metric, data: data, baseline: range == .week || range == .month ? baseline : nil)
                            .frame(height: 220)
                    }
                    if isLoading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.mini)
                            Text("Loading the full history…")
                        }
                        .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .padding(.vertical, 6)
            }

            if !metric.about.isEmpty {
                Section {
                    Text(metric.about).font(.subheadline).foregroundStyle(.secondary)
                } header: {
                    Text("About \(metric.title)")
                }
            }
        }
        .navigationTitle(metric.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// The chart part of MetricDetailView. Categorical String x (bucket keys) so ratio-width bars render; labels mapped back.
private struct MetricChart: View {
    let metric: MetricInfo
    let data: [MetricBucket]
    let baseline: Double?

    var body: some View {
        let color = metric.domain.color
        let labels = Dictionary(uniqueKeysWithValues: data.compactMap { b in b.label.map { (b.key, $0) } })
        let labelKeys = data.filter { $0.label != nil }.map(\.key)
        let present = data.filter { $0.value != nil }
        let isCount = metric.decimals == 0 && metric.aggregation == .total
        let top = present.compactMap(\.value).max() ?? 1
        Chart {
            if let baseline {
                RuleMark(y: .value("28-day average", baseline))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .foregroundStyle(color.opacity(0.6))
            }
            ForEach(present) { b in
                switch metric.style {
                case .line:
                    LineMark(x: .value("Period", b.key), y: .value(metric.short, b.value ?? 0))
                        .interpolationMethod(.monotone)
                        .foregroundStyle(color)
                    if present.count < 12 {
                        PointMark(x: .value("Period", b.key), y: .value(metric.short, b.value ?? 0))
                            .foregroundStyle(color)
                            .symbolSize(24)
                    }
                case .bar:
                    BarMark(x: .value("Period", b.key), y: .value(metric.short, b.value ?? 0), width: .ratio(0.6))
                        .foregroundStyle(color.gradient)
                        .cornerRadius(3)
                }
            }
        }
        .chartXScale(domain: data.map(\.key))
        .chartXAxis {
            AxisMarks(values: labelKeys) { value in
                // No tick line: it ran through the middle of the label. The last label ends at its position so it is
                // not cut off at the card's edge or run into the y-axis labels.
                AxisValueLabel(anchor: labelAnchor(value.index, of: labelKeys.count)) {
                    if let key = value.as(String.self), let text = labels[key] { Text(text).font(.caption2).fixedSize() }
                }
            }
        }
        .chartYAxis {
            if isCount && top <= 4 {
                AxisMarks(values: .stride(by: 1)) { _ in AxisGridLine().foregroundStyle(.quaternary); AxisValueLabel() }
            } else {
                AxisMarks { _ in AxisGridLine().foregroundStyle(.quaternary); AxisValueLabel() }
            }
        }
        .modifier(MetricYScale(includesZero: metric.style == .bar, countTop: isCount ? max(1, ceil(top)) : nil))
        .accessibilityLabel("\(metric.title) chart")
        .accessibilityValue(present.map { "\($0.label ?? "") \(metric.format($0.value)) \(metric.unit)" }.joined(separator: ", "))
    }
}

/// Where a label hangs off its x position: centred, except the last, which ends at its position.
private func labelAnchor(_ index: Int, of count: Int) -> UnitPoint {
    count > 1 && index == count - 1 ? .topTrailing : .top
}

private struct MetricYScale: ViewModifier {
    let includesZero: Bool
    let countTop: Double?

    func body(content: Content) -> some View {
        if let countTop {
            content.chartYScale(domain: 0...countTop)
        } else {
            content.chartYScale(domain: .automatic(includesZero: includesZero))
        }
    }
}

/// MetricDetailView for a `MetricCatalog` metric with the real data: health for the last 365 days, weekly volume for
/// 52 weeks and the weights logged on this device. Shows the shorter cached data (the tabs' own requests) at once while
/// the long history loads.
struct LiveMetricDetailView: View {
    @Environment(AppEnvironment.self) private var env
    @Query(sort: \LocalBodyMeasurement.measuredAt, order: .reverse) private var measurements: [LocalBodyMeasurement]
    let metric: MetricInfo

    private var longHealth: StatsResource<HealthStatsDTO> { env.stats.resource(StatsEndpoints.health(days: 365)) }
    private var longVolume: StatsResource<WeeklyVolumeDTO> { env.stats.resource(StatsEndpoints.weeklyVolume(weeks: 52)) }
    private var shortHealth: StatsResource<HealthStatsDTO> { env.stats.resource(StatsEndpoints.health()) }
    private var shortVolume: StatsResource<WeeklyVolumeDTO> { env.stats.resource(StatsEndpoints.weeklyVolume()) }

    private var usesHealth: Bool { metric.granularity == .daily }

    var body: some View {
        let health = longHealth.value ?? shortHealth.value
        let volume = longVolume.value ?? shortVolume.value
        let sources = MetricCatalog.Sources(health: health, volume: volume,
                                            localWeights: measurements.map(BodyweightLog.snapshot))
        let long: any StatsLoadable = usesHealth ? longHealth : longVolume
        let loading = usesHealth ? (longHealth.value == nil && longHealth.isRefreshing)
                                 : (longVolume.value == nil && longVolume.isRefreshing)
        MetricDetailView(metric: metric, points: MetricCatalog.points(metric, from: sources),
                         baseline: MetricCatalog.baseline(metric, summary: health?.summary), isLoading: loading)
            .loadsStats([long])
    }
}

import FitnessCore
import Charts
import SwiftUI

/// Small line under a signal card's numbers: an optional trend badge and a grey sentence.
struct SignalFootnote: Equatable {
    var text: String
    /// Percent change to show as a coloured badge; nil = text only.
    var delta: Double? = nil
    var higherIsBetter = true
}

/// One signal of the Health tab: latest value, average of the picked period and a mini chart with the average line.
/// Opens the full history chart.
struct SignalCard: View {
    let metric: MetricInfo
    /// Every point of the metric (the latest value comes from here, the chart and average from the period).
    let points: [MetricPoint]
    let span: HealthSignals.Span
    var footnote: SignalFootnote?
    var emptyMessage = "No data in this period."

    var body: some View {
        let window = HealthSignals.window(points, days: span.rawValue)
        let average = HealthSignals.average(window)
        let latest = MetricSeries.latest(points)
        NavigationLink {
            LiveMetricDetailView(metric: metric).screenIdentifier(AXID.Health.metricDetail)
        } label: {
            Card {
                CardHeader(title: metric.short, symbol: metric.symbol, color: metric.domain.color,
                           time: MetricSeries.timeLabel(latest, metric: metric) ?? "No Data", chevron: true)
                HStack(alignment: .firstTextBaseline) {
                    MetricText(value: metric.format(latest?.value), unit: metric.unit, size: .title)
                    Spacer(minLength: 8)
                    VStack(alignment: .trailing, spacing: 0) {
                        Text(span.averageLabel).font(.caption).foregroundStyle(.secondary)
                        MetricText(value: metric.format(average), unit: metric.unit, size: .headline, color: .secondary)
                    }
                }
                SignalChart(metric: metric, points: window, average: average, days: span.rawValue, emptyMessage: emptyMessage)
                    .frame(height: 80)
                if let footnote {
                    HStack(spacing: 6) {
                        if let delta = footnote.delta {
                            TrendBadge(delta: delta, higherIsBetter: footnote.higherIsBetter)
                        }
                        Text(footnote.text).font(.footnote).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(metric.short)
        .accessibilityValue(accessibilityValue(latest: latest, average: average))
        .accessibilityHint("Shows the chart")
        .accessibilityAddTraits(.isLink)
        .accessibilityIdentifier(AXID.Health.signalCard(metric.id))
    }

    private func accessibilityValue(latest: MetricPoint?, average: Double?) -> String {
        guard let latest else { return "No data" }
        var text = "Latest \(metric.format(latest.value)) \(metric.unit)"
        if let average { text += ", \(span.rawValue) day average \(metric.format(average)) \(metric.unit)" }
        if let footnote { text += ", \(footnote.text)" }
        return text
    }
}

/// The chart of one signal over the picked period, without axes; the dashed line is the period average.
struct SignalChart: View {
    let metric: MetricInfo
    /// Points inside the period, oldest first.
    let points: [MetricPoint]
    let average: Double?
    let days: Int
    let emptyMessage: String

    var body: some View {
        let color = metric.domain.color
        let isBar = metric.style == .bar
        if points.count < (isBar ? 1 : 2) {
            Text(emptyMessage)
                .font(.footnote).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            let y = HealthSignals.yDomain(values: points.map(\.value), average: average, fromZero: isBar)
            Chart {
                ForEach(points) { p in
                    if isBar {
                        BarMark(x: .value("Day", p.date, unit: .day), y: .value(metric.short, p.value))
                            .foregroundStyle(color.gradient)
                            .cornerRadius(2)
                    } else {
                        AreaMark(x: .value("Day", p.date, unit: .day), yStart: .value("Low", y.lowerBound),
                                 yEnd: .value(metric.short, p.value), series: .value("Series", "area"))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(LinearGradient(colors: [color.opacity(0.3), color.opacity(0.02)],
                                                            startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("Day", p.date, unit: .day), y: .value(metric.short, p.value),
                                 series: .value("Series", "line"))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(color)
                            .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                        if points.count <= 14 {
                            PointMark(x: .value("Day", p.date, unit: .day), y: .value(metric.short, p.value))
                                .symbolSize(24)
                                .foregroundStyle(color)
                        }
                    }
                }
                if let average {
                    RuleMark(y: .value("Average", average))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .foregroundStyle(.secondary)
                }
            }
            .chartXScale(domain: HealthSignals.chartDomain(days: days))
            .chartYScale(domain: y)
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .accessibilityHidden(true)
        }
    }
}

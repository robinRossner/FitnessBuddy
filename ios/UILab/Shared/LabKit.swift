import Charts
import SwiftUI

// Charts: bar charts that use `width: .ratio(...)` (or any automatic bar width) need a DISCRETE x axis.
// An Int/Double x is continuous, so ratio-width bars collapse to nothing. Use a String x value:
//
//     ForEach(Array(values.enumerated()), id: \.offset) { i, v in
//         BarMark(x: .value("Week", "W\(i + 1)"), y: .value("km", v), width: .ratio(0.55))
//     }
//     .chartXAxis(.hidden)        // or AxisMarks(values: ["W1", "W5"]) for a few labels
//
// Overlay marks (PointMark/RuleMark/LineMark) must use the SAME String x value. The key must be unique per
// bar; map it to a display label in AxisValueLabel (see MetricDetailView). Categorical bands also keep the
// first/last bar away from the y-axis labels. LineMark/AreaMark work on a String x too.

/// A metric value the Apple way: big rounded numerals in the primary colour, the unit small and grey.
struct MetricText: View {
    let value: String
    var unit: String? = nil
    var size: Font.TextStyle = .title
    var color: Color = .primary

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 3) {
            Text(value)
                .font(.system(size, design: .rounded).weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(color)
            if let unit {
                Text(unit)
                    .font(.system(.subheadline, design: .rounded).weight(.semibold))
                    .foregroundStyle(.secondary)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.7)
    }
}

/// Minimal line chart for summary cards. Details live on the drill-down screen.
struct Sparkline: View {
    let values: [Double]
    var color: Color
    var lineWidth: CGFloat = 2
    var showsEndDot = true

    var body: some View {
        GeometryReader { geo in
            let pts = points(in: geo.size)
            ZStack {
                Path { p in
                    guard let first = pts.first else { return }
                    p.move(to: first)
                    for pt in pts.dropFirst() { p.addLine(to: pt) }
                }
                .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round))
                if showsEndDot, let last = pts.last {
                    Circle().fill(color).frame(width: 7, height: 7).position(last)
                }
            }
        }
        .accessibilityHidden(true)
    }

    private func points(in size: CGSize) -> [CGPoint] {
        guard values.count > 1, let lo = values.min(), let hi = values.max() else { return [] }
        let span = max(hi - lo, 0.0001)
        let inset: CGFloat = 4
        return values.enumerated().map { i, v in
            CGPoint(x: inset + (size.width - 2 * inset) * CGFloat(i) / CGFloat(values.count - 1),
                    y: inset + (size.height - 2 * inset) * CGFloat(1 - (v - lo) / span))
        }
    }
}

/// Explanations go behind this instead of sitting on the card.
struct InfoButton: View {
    let text: String
    @State private var shown = false

    var body: some View {
        Button { shown = true } label: {
            Image(systemName: "info.circle")
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .accessibilityLabel("About")
        .popover(isPresented: $shown) {
            Text(text)
                .font(.footnote)
                .fixedSize(horizontal: false, vertical: true)
                .padding()
                .frame(width: 280)
                .presentationCompactAdaptation(.popover)
        }
    }
}

/// Arrow that is coloured by whether the change is good, not by its direction.
struct TrendBadge: View {
    let delta: Double
    let higherIsBetter: Bool
    var suffix = "%"

    var body: some View {
        let flat = abs(delta) < 1
        let good = flat || (delta > 0) == higherIsBetter
        HStack(spacing: 2) {
            Image(systemName: flat ? "arrow.right" : (delta > 0 ? "arrow.up" : "arrow.down"))
            Text("\(abs(delta).oneDecimal) \(suffix)")
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(flat ? Color.secondary : (good ? Color.green : Color.orange))
    }
}

// MARK: - Detail screen (shared by all variants)

enum LabRange: String, CaseIterable, Identifiable {
    case day = "D", week = "W", month = "M", halfYear = "6M", year = "Y"
    var id: String { rawValue }

    /// Number of points for an averaged metric (counts/sums aggregate more coarsely, see MetricDetailView).
    var count: Int {
        switch self {
        case .day: 24
        case .week: 7
        case .month: 30
        case .halfYear: 26
        case .year: 12
        }
    }

    var caption: String {
        switch self {
        case .day: "Today"
        case .week: "Last 7 days"
        case .month: "Last 30 days"
        case .halfYear: "Last 6 months, weekly"
        case .year: "Last 12 months, monthly"
        }
    }
}

extension LabMetric {
    /// Ranges that make sense: nothing here is measured hourly, so D is hidden for every metric.
    var ranges: [LabRange] { LabRange.allCases.filter { $0 != .day } }
}

struct MetricDetailView: View {
    let metric: LabMetric
    @State private var range: LabRange = .month

    private struct Bucket: Identifiable {
        let key: String        // unique, categorical x value
        let value: Double
        let label: String?     // x-axis label, nil = none
        var id: String { key }
    }

    private var isTotal: Bool { metric.kind != .average }

    private func combine(_ values: ArraySlice<Double>) -> Double {
        let sum = values.reduce(0, +)
        return isTotal ? sum : sum / Double(max(values.count, 1))
    }

    private func buckets() -> [Bucket] {
        let total = 366
        let daily = LabData.daily(for: metric, total: total)
        let cal = Calendar.current
        func value(ago n: Int) -> Double { daily[total - 1 - n] }
        func date(ago n: Int) -> Date { LabData.startOfDay(n) }
        /// Mean/sum of the 7 days that start `week * 7 + 6` days ago.
        func week(_ w: Int) -> Double {
            let hi = total - 1 - w * 7, lo = hi - 6
            return combine(daily[lo...hi])
        }
        let dayMonth = Date.FormatStyle.dateTime.day().month(.abbreviated)

        switch range {
        case .day, .week:
            return (0..<7).reversed().map { n in
                Bucket(key: "d\(n)", value: value(ago: n),
                       label: date(ago: n).formatted(.dateTime.weekday(.narrow)))
            }
        case .month:
            if isTotal {
                return (0..<4).reversed().map { w in
                    Bucket(key: "w\(w)", value: week(w), label: date(ago: w * 7 + 6).formatted(dayMonth))
                }
            }
            return (0..<30).reversed().map { n in
                Bucket(key: "d\(n)", value: value(ago: n),
                       label: (29 - n) % 7 == 0 ? date(ago: n).formatted(dayMonth) : nil)
            }
        case .halfYear:
            var lastMonth: Int?
            var lastLabel = -10
            return (0..<26).reversed().enumerated().map { i, w in
                let start = date(ago: w * 7 + 6)
                let month = cal.component(.month, from: start)
                var label: String?
                if month != lastMonth, i - lastLabel >= 3 {
                    label = start.formatted(.dateTime.month(.abbreviated))
                    lastMonth = month
                    lastLabel = i
                }
                return Bucket(key: "w\(w)", value: week(w), label: label)
            }
        case .year:
            let thisMonth = cal.dateInterval(of: .month, for: LabData.startOfDay(0))!.start
            return (0..<12).reversed().map { m in
                let start = cal.date(byAdding: .month, value: -m, to: thisMonth)!
                let days = (0..<total).filter { cal.isDate(date(ago: $0), equalTo: start, toGranularity: .month) }
                let slice = days.isEmpty ? [0] : days.map { value(ago: $0) }
                return Bucket(key: "m\(m)", value: combine(slice[...]),
                              label: start.formatted(.dateTime.month(.narrow)))
            }
        }
    }

    private var caption: String {
        switch (range, isTotal) {
        case (.month, true): "Last 4 weeks, per week"
        default: range.caption
        }
    }

    var body: some View {
        let data = buckets()
        let labels = Dictionary(uniqueKeysWithValues: data.compactMap { b in b.label.map { (b.key, $0) } })
        let labelKeys = data.filter { $0.label != nil }.map(\.key)
        let headline = isTotal ? data.reduce(0) { $0 + $1.value }
                               : data.reduce(0) { $0 + $1.value } / Double(max(data.count, 1))
        let top = data.map(\.value).max() ?? 1

        List {
            Section {
                VStack(alignment: .leading, spacing: 16) {
                    Picker("Range", selection: $range) {
                        ForEach(metric.ranges) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(isTotal ? "Total" : "Average").font(.subheadline).foregroundStyle(.secondary)
                        MetricText(value: headline.oneDecimal, unit: metric.detailUnit ?? metric.unit, size: .largeTitle)
                        Text(caption).font(.subheadline).foregroundStyle(.secondary)
                    }

                    chart(data, labels: labels, labelKeys: labelKeys, top: top)
                        .frame(height: 220)
                }
                .padding(.vertical, 6)
            }

            Section {
                Text(metric.about)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } header: {
                Text("About \(metric.title)")
            }
        }
        .navigationTitle(metric.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private func chart(_ data: [Bucket], labels: [String: String], labelKeys: [String], top: Double) -> some View {
        let isCount = metric.kind == .count
        let countTop = max(1, ceil(top))
        Chart {
            if let base = metric.baseline, metric.style == .line {
                ForEach(data) { b in
                    AreaMark(x: .value("Period", b.key),
                             yStart: .value("Band low", base * 0.9), yEnd: .value("Band high", base * 1.1))
                        .foregroundStyle(metric.color.opacity(0.16))
                }
                RuleMark(y: .value("28-day average", base))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .foregroundStyle(metric.color.opacity(0.5))
            }
            ForEach(data) { b in
                switch metric.style {
                case .line:
                    LineMark(x: .value("Period", b.key), y: .value(metric.short, b.value))
                        .interpolationMethod(.monotone)
                        .foregroundStyle(metric.color)
                case .bar:
                    BarMark(x: .value("Period", b.key), y: .value(metric.short, b.value))
                        .foregroundStyle(metric.color.gradient)
                        .cornerRadius(3)
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: labelKeys) { value in
                AxisTick()
                AxisValueLabel {
                    if let key = value.as(String.self), let text = labels[key] {
                        Text(text).font(.caption2)
                    }
                }
            }
        }
        .chartYAxis {
            if isCount && countTop <= 4 {
                AxisMarks(values: .stride(by: 1)) { _ in
                    AxisGridLine().foregroundStyle(.quaternary)
                    AxisValueLabel()
                }
            } else {
                AxisMarks { _ in
                    AxisGridLine().foregroundStyle(.quaternary)
                    AxisValueLabel()
                }
            }
        }
        .chartXScale(range: .plotDimension(padding: 6))
        .modifier(YScale(isCount: isCount, top: countTop, includesZero: metric.style == .bar))
    }

    private struct YScale: ViewModifier {
        let isCount: Bool
        let top: Double
        let includesZero: Bool
        func body(content: Content) -> some View {
            if isCount {
                content.chartYScale(domain: 0...top)
            } else {
                content.chartYScale(domain: .automatic(includesZero: includesZero))
            }
        }
    }
}

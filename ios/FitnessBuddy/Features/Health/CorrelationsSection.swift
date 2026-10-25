import Charts
import SwiftUI

struct CorrelationsSection: View {
    let resource: StatsResource<CorrelationsDTO>

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel(title: "What moves what", symbol: "point.3.connected.trianglepath.dotted", color: Domain.recovery.color)
            if let items = resource.value?.items, !items.isEmpty {
                ForEach(items) { CorrelationCard(item: $0) }
            } else if resource.value != nil {
                Card { ChartEmpty(symbol: "point.3.connected.trianglepath.dotted", message: "Not enough data yet. Patterns between sleep, HRV and training show up after a few weeks.") }
            } else {
                StatsPlaceholder(resource: resource, symbol: "point.3.connected.trianglepath.dotted", emptyTitle: "correlations")
            }
        }
    }
}

enum CorrelationText {
    static func strength(_ r: Double) -> String {
        let a = abs(r)
        // Same cut-offs as the backend note: <0.2 negligible, <0.4 weak, <0.6 moderate, else strong.
        if a < 0.2 { return "No clear link" }
        let word = a < 0.4 ? "Weak" : (a < 0.6 ? "Moderate" : "Strong")
        return "\(word) \(r > 0 ? "positive" : "negative") link"
    }

    /// Least-squares line through the points (nil with fewer than 3 points or no spread in x).
    static func trendLine(_ pts: [CorrelationPointDTO]) -> (x0: Double, y0: Double, x1: Double, y1: Double)? {
        guard pts.count >= 3 else { return nil }
        let n = Double(pts.count)
        let mx = pts.map(\.x).reduce(0, +) / n, my = pts.map(\.y).reduce(0, +) / n
        let sxx = pts.map { ($0.x - mx) * ($0.x - mx) }.reduce(0, +)
        guard sxx > 0 else { return nil }
        let slope = pts.map { ($0.x - mx) * ($0.y - my) }.reduce(0, +) / sxx
        let lo = pts.map(\.x).min()!, hi = pts.map(\.x).max()!
        return (lo, my + slope * (lo - mx), hi, my + slope * (hi - mx))
    }
}

private struct CorrelationCard: View {
    let item: CorrelationDTO

    var body: some View {
        let enough = item.r != nil && item.points.count >= 3
        Card {
            HStack(alignment: .firstTextBaseline) {
                Text(item.title).font(.headline)
                Spacer()
                if let n = item.n { Text("n = \(n)").font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
            }
            if let r = item.r, enough {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("r = \(Fmt.number(r, maxFraction: 2))").metricStyle(.title2).foregroundStyle(Domain.recovery.color)
                    Text(CorrelationText.strength(r)).font(.subheadline).foregroundStyle(.secondary)
                }
                Chart {
                    ForEach(item.points) { p in
                        PointMark(x: .value(item.xLabel ?? "x", p.x), y: .value(item.yLabel ?? "y", p.y))
                            .foregroundStyle(Domain.recovery.color.opacity(0.7))
                            .symbolSize(36)
                    }
                    if let line = CorrelationText.trendLine(item.points) {
                        LineMark(x: .value("x", line.x0), y: .value("y", line.y0), series: .value("Series", "trend"))
                            .foregroundStyle(Color.gray)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                        LineMark(x: .value("x", line.x1), y: .value("y", line.y1), series: .value("Series", "trend"))
                            .foregroundStyle(Color.gray)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                    }
                }
                .chartXScale(domain: .automatic(includesZero: false))
                .chartYScale(domain: .automatic(includesZero: false))
                .frame(height: 170)
                .accessibilityLabel("\(item.title): \(item.points.count) points, correlation \(Fmt.number(r, maxFraction: 2))")
                .accessibilityValue(CorrelationText.strength(r))
                VStack(alignment: .leading, spacing: 2) {
                    if let x = item.xLabel { Label("x: \(x)", systemImage: "arrow.right") }
                    if let y = item.yLabel { Label("y: \(y)", systemImage: "arrow.up") }
                }
                .font(.caption).foregroundStyle(.secondary).labelStyle(.titleAndIcon)
            } else {
                ChartEmpty(symbol: "chart.dots.scatter", message: "Not enough data yet", height: 90)
            }
            if let note = item.note, !note.isEmpty {
                Text(note).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

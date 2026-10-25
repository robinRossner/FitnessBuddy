import Charts
import SwiftUI

struct InsightsRoute: Hashable {}

/// What moves what: correlations grouped by category. Each card leads with the plain-language sentence and a small scatter.
struct InsightsView: View {
    @Environment(AppEnvironment.self) private var env

    private var correlations: StatsResource<CorrelationsDTO> { env.stats.resource(StatsEndpoints.correlations()) }

    var body: some View {
        let correlations = correlations
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let items = correlations.value?.items {
                    let groups = InsightTransforms.grouped(items)
                    if groups.isEmpty {
                        Card { ChartEmpty(symbol: "lightbulb", message: "Not enough data yet. Patterns between sleep, HRV and training show up after a few weeks.") }
                    }
                    ForEach(groups) { group in
                        VStack(alignment: .leading, spacing: 10) {
                            SectionLabel(title: group.category.title, symbol: group.category == .hybrid ? "figure.run.circle" : "heart.fill",
                                         color: Domain.recovery.color)
                                .accessibilityAddTraits(.isHeader)
                            ForEach(group.items) { item in
                                if InsightTransforms.hasEnoughData(item) { InsightCard(item: item) } else { CollapsedInsight(item: item) }
                            }
                        }
                    }
                } else {
                    StatsPlaceholder(resource: correlations, symbol: "lightbulb", emptyTitle: "insights")
                }
                FreshnessLabel(resource: correlations)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Color.screenBackground)
        .navigationTitle("Insights")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await refreshStats(env, [correlations]) }
        .loadsStats([correlations])
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

private struct CollapsedInsight: View {
    let item: CorrelationDTO

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "chart.dots.scatter").foregroundStyle(.tertiary).accessibilityHidden(true)
            Text(item.title).font(.subheadline.weight(.medium)).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 8)
            Text("Needs more data").font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

private struct InsightCard: View {
    let item: CorrelationDTO

    var body: some View {
        Card {
            Text(item.insight ?? CorrelationText.strength(item.r ?? 0))
                .font(.headline).fixedSize(horizontal: false, vertical: true)
            Text(item.title).font(.footnote.weight(.medium)).foregroundStyle(.secondary)
            Chart {
                ForEach(item.points) { p in
                    PointMark(x: .value(item.xLabel ?? "x", p.x), y: .value(item.yLabel ?? "y", p.y))
                        .foregroundStyle(Domain.recovery.color.opacity(0.7))
                        .symbolSize(28)
                }
                if let line = CorrelationText.trendLine(item.points) {
                    LineMark(x: .value("x", line.x0), y: .value("y", line.y0), series: .value("Series", "trend"))
                        .foregroundStyle(Color.gray).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                    LineMark(x: .value("x", line.x1), y: .value("y", line.y1), series: .value("Series", "trend"))
                        .foregroundStyle(Color.gray).lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 4]))
                }
            }
            .chartXScale(domain: .automatic(includesZero: false))
            .chartYScale(domain: .automatic(includesZero: false))
            .frame(height: 130)
            .accessibilityLabel("\(item.title): \(item.points.count) points")
            .accessibilityValue(CorrelationText.strength(item.r ?? 0))
            if item.xLabel != nil || item.yLabel != nil {
                VStack(alignment: .leading, spacing: 2) {
                    if let x = item.xLabel { Label(x, systemImage: "arrow.right") }
                    if let y = item.yLabel { Label(y, systemImage: "arrow.up") }
                }
                .font(.caption).foregroundStyle(.secondary).labelStyle(.titleAndIcon)
            }
            HStack {
                if let r = item.r { Text("r = \(Fmt.number(r, maxFraction: 2)) · \(CorrelationText.strength(r))") }
                Spacer(minLength: 8)
                if let n = item.n { Text("n = \(n)") }
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .contain)
    }
}

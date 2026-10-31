import Charts
import SwiftUI

// MARK: - Model

/// One correlation card, mirroring the real app's Insights screen (docs/api/examples/stats_correlations.json).
struct LabInsight: Identifiable {
    enum Category: String, CaseIterable {
        case recovery, hybrid
        var title: String { self == .recovery ? "Recovery" : "Hybrid" }
    }

    struct Point: Identifiable {
        let id = UUID()
        let x: Double
        let y: Double
    }

    let id: String
    let category: Category
    /// Footnote title, e.g. "Sleep vs lifting performance".
    let title: String
    let xLabel: String
    let yLabel: String
    let r: Double
    let n: Int
    /// Caption under the chart.
    let note: String
    /// Full sentence as the API sends it, e.g. "More sleep tends to go with better lifting days (r=0.58, n=11, early read)".
    let insight: String
    let points: [Point]

    static func points(_ pairs: [(Double, Double)]) -> [Point] {
        pairs.map { Point(x: $0.0, y: $0.1) }
    }

    /// Plain-language headline: the sentence without the "(r=…, n=…)" tail.
    var headline: String {
        if let range = insight.range(of: " (r=") { return String(insight[..<range.lowerBound]) }
        return insight
    }

    /// 0 = no clear link, 1 = weak, 2 = moderate, 3 = strong.
    var strength: Int {
        switch abs(r) {
        case ..<0.2: 0
        case ..<0.4: 1
        case ..<0.6: 2
        default: 3
        }
    }

    /// "No clear link", "Weak positive", "Moderate negative", "Strong positive".
    var strengthLabel: String {
        if strength == 0 { return "No clear link" }
        let word = ["", "Weak", "Moderate", "Strong"][strength]
        return "\(word) \(r > 0 ? "positive" : "negative")"
    }

    /// Least-squares fit y = slope * x + intercept.
    var fit: (slope: Double, intercept: Double)? {
        let count = Double(points.count)
        guard count > 1 else { return nil }
        let mx = points.map(\.x).reduce(0, +) / count
        let my = points.map(\.y).reduce(0, +) / count
        let sxx = points.reduce(0) { $0 + ($1.x - mx) * ($1.x - mx) }
        guard sxx > 0 else { return nil }
        let sxy = points.reduce(0) { $0 + ($1.x - mx) * ($1.y - my) }
        let slope = sxy / sxx
        return (slope, my - slope * mx)
    }
}

// MARK: - Views

/// Three dots filled by strength (none, 1, 2, 3). Reads as a signal meter.
struct LabStrengthMeter: View {
    let insight: LabInsight
    var accent: Color

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(i < insight.strength ? accent : Color.secondary.opacity(0.25))
                    .frame(width: 7, height: 7)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(insight.strengthLabel)
    }
}

/// Card content only (no background): headline, title, scatter chart with trend line, note.
/// Wrap it in whatever card style the variant uses.
struct LabInsightCard: View {
    let insight: LabInsight
    var accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(insight.headline)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Text(insight.title)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    LabStrengthMeter(insight: insight, accent: accent)
                    Text(insight.strengthLabel)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
            }

            Chart {
                ForEach(insight.points) { p in
                    PointMark(x: .value(insight.xLabel, p.x), y: .value(insight.yLabel, p.y))
                        .symbolSize(36)
                        .foregroundStyle(accent.opacity(0.75))
                }
                if let fit = insight.fit, let lo = insight.points.map(\.x).min(),
                   let hi = insight.points.map(\.x).max(), insight.strength > 0 {
                    LineMark(x: .value(insight.xLabel, lo), y: .value(insight.yLabel, fit.slope * lo + fit.intercept),
                             series: .value("Trend", "trend"))
                        .foregroundStyle(accent)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                    LineMark(x: .value(insight.xLabel, hi), y: .value(insight.yLabel, fit.slope * hi + fit.intercept),
                             series: .value("Trend", "trend"))
                        .foregroundStyle(accent)
                        .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                }
            }
            .chartXScale(domain: .automatic(includesZero: false))
            .chartYScale(domain: .automatic(includesZero: false))
            .chartXAxisLabel(position: .bottom, alignment: .center) {
                Text(insight.xLabel).font(.caption2).foregroundStyle(.secondary)
            }
            .chartYAxisLabel(position: .top, alignment: .leading) {
                Text(insight.yLabel).font(.caption2).foregroundStyle(.secondary)
            }
            .frame(height: 170)

            Text(insight.note)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// One-line (max two lines) sentence plus a strength meter, for lists and home screens.
struct LabInsightRow: View {
    let insight: LabInsight
    var accent: Color

    var body: some View {
        HStack(spacing: 12) {
            Text(insight.headline)
                .font(.subheadline)
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            LabStrengthMeter(insight: insight, accent: accent)
        }
    }
}

/// All insights grouped by category ("Recovery", "Hybrid") with headers; each in a rounded card.
/// Embed in a ScrollView (it is a plain VStack).
struct LabInsightsList: View {
    var accent: Color
    var cardBackground: Color = Color(.secondarySystemGroupedBackground)
    var insights: [LabInsight] = LabData.insights

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            ForEach(LabInsight.Category.allCases, id: \.self) { category in
                let items = insights.filter { $0.category == category }
                if !items.isEmpty {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(category.title)
                            .font(.title3.weight(.semibold))
                        ForEach(items) { insight in
                            LabInsightCard(insight: insight, accent: accent)
                                .padding(16)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(cardBackground, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                        }
                    }
                }
            }
        }
    }
}

#Preview {
    ScrollView { LabInsightsList(accent: .teal).padding() }
}

import Charts
import SwiftUI

// Correlation ("insight") building blocks in the kit's look. Colour: Domain.insights.

extension CorrelationDTO {
    /// 0 = no clear link, 1 = weak, 2 = moderate, 3 = strong (same cut-offs as the backend: 0.2 / 0.4 / 0.6).
    var strength: Int {
        guard let r else { return 0 }
        switch abs(r) {
        case ..<0.2: return 0
        case ..<0.4: return 1
        case ..<0.6: return 2
        default: return 3
        }
    }

    /// "No clear link", "Weak positive", "Moderate negative", "Strong positive".
    var strengthLabel: String {
        guard strength > 0, let r else { return "No clear link" }
        return "\(["", "Weak", "Moderate", "Strong"][strength]) \(r > 0 ? "positive" : "negative")"
    }

    /// The plain-language sentence without the "(r=…, n=…)" tail; falls back to the strength or the title.
    var headline: String {
        if let insight = insight?.trimmingCharacters(in: .whitespacesAndNewlines), !insight.isEmpty {
            if let range = insight.range(of: " (r=") { return String(insight[..<range.lowerBound]) }
            return insight
        }
        return r == nil ? title : "\(strengthLabel) link: \(title.lowercased())"
    }
}

extension InsightTransforms {
    /// The featured insight on Summary: the strongest correlation (largest |r|) that has enough data and is at least weak.
    static func strongest(_ items: [CorrelationDTO]) -> CorrelationDTO? {
        items.filter { !droppedIds.contains($0.id) && hasEnoughData($0) && $0.strength > 0 }
            .max { abs($0.r ?? 0) < abs($1.r ?? 0) }
    }
}

/// Three dots filled by strength. Reads as a signal meter.
struct StrengthMeter: View {
    let item: CorrelationDTO
    var color: Color = Domain.insights.color

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(i < item.strength ? color : Color.secondary.opacity(0.25))
                    .frame(width: 7, height: 7)
            }
        }
        .accessibilityElement()
        .accessibilityLabel(item.strengthLabel)
    }
}

/// One- or two-line sentence plus the strength meter, for home screens and lists (no background).
struct InsightRow: View {
    let item: CorrelationDTO
    var color: Color = Domain.insights.color

    var body: some View {
        HStack(spacing: 12) {
            Text(item.headline)
                .font(.subheadline)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            StrengthMeter(item: item, color: color)
        }
    }
}

/// Card content only (wrap it in a Card): headline, title + strength, scatter with trend line, r / n and the note.
struct InsightCardContent: View {
    let item: CorrelationDTO
    var color: Color = Domain.insights.color
    var chartHeight: CGFloat = 170

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.headline).font(.headline).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Text(item.title).font(.footnote).foregroundStyle(.secondary)
                    Spacer(minLength: 0)
                    StrengthMeter(item: item, color: color)
                    Text(item.strengthLabel).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                }
            }
            if item.points.count >= 3 {
                Chart {
                    ForEach(item.points) { p in
                        PointMark(x: .value(item.xLabel ?? "x", p.x), y: .value(item.yLabel ?? "y", p.y))
                            .symbolSize(36)
                            .foregroundStyle(color.opacity(0.75))
                    }
                    if item.strength > 0, let line = CorrelationTrend.line(item.points) {
                        LineMark(x: .value("x", line.x0), y: .value("y", line.y0), series: .value("Series", "trend"))
                            .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                        LineMark(x: .value("x", line.x1), y: .value("y", line.y1), series: .value("Series", "trend"))
                            .foregroundStyle(color).lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round))
                    }
                }
                .chartXScale(domain: .automatic(includesZero: false))
                .chartYScale(domain: .automatic(includesZero: false))
                .chartXAxisLabel(position: .bottom, alignment: .center) {
                    if let x = item.xLabel { Text(x).font(.caption2).foregroundStyle(.secondary) }
                }
                .chartYAxisLabel(position: .top, alignment: .leading) {
                    if let y = item.yLabel { Text(y).font(.caption2).foregroundStyle(.secondary) }
                }
                .frame(height: chartHeight)
                .accessibilityLabel("\(item.title): \(item.points.count) points")
                .accessibilityValue(item.strengthLabel)
            }
            HStack {
                if let r = item.r { Text("r = \(Fmt.number(r, maxFraction: 2))") }
                Spacer(minLength: 8)
                if let n = item.n { Text("n = \(n)") }
            }
            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            if let note = item.note, !note.isEmpty {
                Text(note).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// Full screen for one correlation.
struct InsightDetailView: View {
    let item: CorrelationDTO

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Card { InsightCardContent(item: item, chartHeight: 240) }
                Text("A correlation shows that two things tend to move together, not that one causes the other. It gets more reliable with more days.")
                    .font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
        }
        .screenStyle()
        .navigationTitle(item.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

enum CorrelationTrend {
    /// Least-squares line through the points (nil with fewer than 3 points or no spread in x).
    static func line(_ pts: [CorrelationPointDTO]) -> (x0: Double, y0: Double, x1: Double, y1: Double)? {
        guard pts.count >= 3, let lo = pts.map(\.x).min(), let hi = pts.map(\.x).max() else { return nil }
        let n = Double(pts.count)
        let mx = pts.map(\.x).reduce(0, +) / n, my = pts.map(\.y).reduce(0, +) / n
        let sxx = pts.map { ($0.x - mx) * ($0.x - mx) }.reduce(0, +)
        guard sxx > 0 else { return nil }
        let slope = pts.map { ($0.x - mx) * ($0.y - my) }.reduce(0, +) / sxx
        return (lo, my + slope * (lo - mx), hi, my + slope * (hi - mx))
    }
}

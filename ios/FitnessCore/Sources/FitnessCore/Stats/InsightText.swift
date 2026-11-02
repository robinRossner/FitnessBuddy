import Foundation

extension CorrelationDTO {
    /// 0 = no clear link, 1 = weak, 2 = moderate, 3 = strong (same cut-offs as the backend: 0.2 / 0.4 / 0.6).
    public var strength: Int {
        guard let r else { return 0 }
        switch abs(r) {
        case ..<0.2: return 0
        case ..<0.4: return 1
        case ..<0.6: return 2
        default: return 3
        }
    }

    /// "No clear link", "Weak positive", "Moderate negative", "Strong positive".
    public var strengthLabel: String {
        guard strength > 0, let r else { return "No clear link" }
        return "\(["", "Weak", "Moderate", "Strong"][strength]) \(r > 0 ? "positive" : "negative")"
    }

    /// The plain-language sentence without the "(r=…, n=…)" tail; falls back to the strength or the title.
    public var headline: String {
        if let insight = insight?.trimmingCharacters(in: .whitespacesAndNewlines), !insight.isEmpty {
            if let range = insight.range(of: " (r=") { return String(insight[..<range.lowerBound]) }
            return insight
        }
        return r == nil ? title : "\(strengthLabel) link: \(title.lowercased())"
    }
}

extension InsightTransforms {
    /// The featured insight on Summary: the strongest correlation (largest |r|) that has enough data and is at least weak.
    public static func strongest(_ items: [CorrelationDTO]) -> CorrelationDTO? {
        items.filter { !droppedIds.contains($0.id) && hasEnoughData($0) && $0.strength > 0 }
            .max { abs($0.r ?? 0) < abs($1.r ?? 0) }
    }
}

public enum CorrelationText: Sendable {
    public static func strength(_ r: Double) -> String {
        let a = abs(r)
        // Same cut-offs as the backend note: <0.2 negligible, <0.4 weak, <0.6 moderate, else strong.
        if a < 0.2 { return "No clear link" }
        let word = a < 0.4 ? "Weak" : (a < 0.6 ? "Moderate" : "Strong")
        return "\(word) \(r > 0 ? "positive" : "negative") link"
    }

    /// Least-squares line through the points (nil with fewer than 3 points or no spread in x).
    public static func trendLine(_ pts: [CorrelationPointDTO]) -> (x0: Double, y0: Double, x1: Double, y1: Double)? {
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

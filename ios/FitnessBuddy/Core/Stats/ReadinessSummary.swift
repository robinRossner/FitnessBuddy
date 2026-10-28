import Foundation

// Readiness as the cards show it: a headline, a few compact metric chips and (sheet only) the long explanation.
// Newer backends send `readiness_today`; older ones only a colour + reason (+ signals) or the day's HRV / RHR / sleep,
// so every field here has a fallback computed from what is there.

extension ReadinessLevel {
    /// "Ready" / "Take it easier" / "Recover" -> level (used when the backend omits `level`).
    init(headline: String?) {
        switch headline?.trimmingCharacters(in: .whitespaces).lowercased() {
        case "ready": self = .green
        case "take it easier": self = .yellow
        case "recover": self = .red
        default: self = .unknown
        }
    }
}

extension MetricStatus {
    var word: String {
        switch self {
        case .ok: "normal"
        case .warn: "slightly off"
        case .bad: "off"
        case .unknown: "no rating"
        }
    }
}

struct MetricDelta: Equatable, Sendable {
    var up: Bool
    var text: String
}

extension ReadinessMetricDTO {
    var displayLabel: String {
        if let label, !label.isEmpty { return label }
        return key.count <= 3 ? key.uppercased() : key.capitalized
    }

    /// Chips use the short names ("RHR").
    var chipLabel: String { key == "rhr" ? "RHR" : displayLabel }

    var resolvedStatus: MetricStatus { status ?? .unknown }

    /// "66", "53", "7.4" (no unit).
    var valueText: String {
        guard let value else { return "–" }
        return Fmt.number(value, maxFraction: unit == "h" ? 1 : (abs(value) >= 100 ? 0 : (unit == "ms" || unit == "bpm" ? 0 : 1)))
    }

    /// "66 ms", "53 bpm", "7.4 h".
    var valueWithUnit: String { unit.map { "\(valueText) \($0)" } ?? valueText }

    /// Value as shown on a chip: the unit is dropped for bpm ("RHR 53").
    var chipValue: String { unit == "bpm" ? valueText : valueWithUnit }

    var baselineText: String? {
        guard let baseline else { return nil }
        let m = ReadinessMetricDTO(key: key, label: label, value: baseline, unit: unit, baseline: nil, deltaPct: nil, status: nil)
        return m.valueWithUnit
    }

    /// bpm metrics change in absolute beats ("↑1"), others in percent ("↓4%"); sleep carries no delta.
    var chipDelta: MetricDelta? {
        if unit == "bpm", let value, let baseline {
            let d = Int((value - baseline).rounded())
            return d == 0 ? nil : MetricDelta(up: d > 0, text: "\(abs(d))")
        }
        if unit == "h" { return nil }
        if let p = deltaPct, abs(p) >= 0.5 { return MetricDelta(up: p > 0, text: "\(Int(abs(p).rounded()))%") }
        return nil
    }

    var spokenDescription: String {
        var parts = ["\(displayLabel) \(valueWithUnit)"]
        if let d = chipDelta { parts.append("\(d.up ? "up" : "down") \(d.text.replacingOccurrences(of: "%", with: " percent"))") }
        parts.append(resolvedStatus.word)
        return parts.joined(separator: ", ")
    }
}

struct ReadinessSummary: Equatable, Sendable {
    var level: ReadinessLevel
    var headline: String
    var metrics: [ReadinessMetricDTO]
    var details: [String]

    /// Best summary from whatever is available: the new `readiness_today`, the legacy colour/reason(+signals),
    /// and the health day (for HRV / RHR / sleep numbers). Nil when there is nothing at all.
    static func make(today: ReadinessTodayDTO?, legacy: ReadinessDTO?, day: HealthDayDTO?) -> ReadinessSummary? {
        if let today {
            var level = ReadinessLevel(color: today.level)
            if level == .unknown { level = ReadinessLevel(headline: today.headline) }
            if level == .unknown, let legacy { level = legacy.level }
            let headline = nonEmpty(today.headline) ?? level.title
            // The backend's own answer is trusted as is (an empty metric list means "no data", not "compute it yourself").
            return ReadinessSummary(level: level, headline: headline, metrics: today.metrics,
                                    details: completeDetails(today.details, metrics: today.metrics))
        }
        if let legacy {
            return ReadinessSummary(level: legacy.level, headline: legacy.level.title,
                                    metrics: fallbackMetrics(legacy: legacy, day: day), details: fallbackDetails(legacy: legacy, day: day))
        }
        if let day, let r = day.readiness {
            return ReadinessSummary(level: r.readinessLevel, headline: r.readinessLevel.title,
                                    metrics: dayMetrics(day), details: fallbackDetails(legacy: nil, day: day))
        }
        return nil
    }

    /// The Why list must name every factor the score uses. Current backends already do; an older one
    /// lists only the flagged factors, so a factor no line mentions is added from its metric (or "no data").
    static func completeDetails(_ details: [String], metrics: [ReadinessMetricDTO]) -> [String] {
        guard !details.isEmpty, !metrics.isEmpty else { return details }
        let factors: [(key: String, label: String, words: [String])] = [
            ("hrv", "HRV", ["hrv"]), ("rhr", "Resting HR", ["resting hr", "rhr"]), ("sleep", "Sleep", ["sleep"]),
        ]
        var out = details
        for f in factors where !details.contains(where: { line in f.words.contains { line.lowercased().contains($0) } }) {
            guard let m = metrics.first(where: { $0.key == f.key }), m.value != nil else {
                out.append("\(f.label): no data")
                continue
            }
            var line = "\(f.label) \(m.valueWithUnit)"
            if let b = m.baselineText { line += " (baseline \(b))" }
            out.append("\(line) - \(m.resolvedStatus == .ok ? "neutral" : "pulls it down")")
        }
        return out
    }

    private static func nonEmpty(_ s: String?) -> String? {
        guard let s = s?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty else { return nil }
        return s
    }

    private static func fallbackMetrics(legacy: ReadinessDTO?, day: HealthDayDTO?) -> [ReadinessMetricDTO] {
        if let signals = legacy?.signals, !signals.isEmpty {
            let metrics = signals.compactMap(signalMetric)
            if !metrics.isEmpty { return metrics }
        }
        return day.map(dayMetrics) ?? []
    }

    private static func fallbackDetails(legacy: ReadinessDTO?, day: HealthDayDTO?) -> [String] {
        var lines: [String] = []
        if let reason = nonEmpty(legacy?.reason) ?? nonEmpty(day?.readiness?.reason) { lines.append(reason) }
        for s in legacy?.signals ?? [] {
            if let d = nonEmpty(s.detail), !lines.contains(d) { lines.append(d) }
        }
        return lines
    }

    private static func signalMetric(_ s: SignalDTO) -> ReadinessMetricDTO? {
        let name = (s.name ?? "").lowercased()
        let (key, label, unit): (String, String, String)
        if name.contains("hrv") { (key, label, unit) = ("hrv", "HRV", "ms") }
        else if name.contains("rhr") || name.contains("resting") { (key, label, unit) = ("rhr", "RHR", "bpm") }
        else if name.contains("sleep") { (key, label, unit) = ("sleep", "Sleep", "h") }
        else { return nil }
        guard let value = s.value else { return nil }
        let status: MetricStatus = switch (s.level ?? "").lowercased() {
        case "green": .ok
        case "yellow": .warn
        case "red": .bad
        default: .unknown
        }
        return ReadinessMetricDTO(key: key, label: label, value: value, unit: unit, baseline: s.baseline,
                                  deltaPct: pct(value, s.baseline), status: status)
    }

    private static func pct(_ value: Double, _ baseline: Double?) -> Double? {
        guard let baseline, baseline != 0 else { return nil }
        return (value - baseline) / baseline * 100
    }

    /// HRV / resting HR / sleep of one health day, rated against simple thresholds.
    static func dayMetrics(_ day: HealthDayDTO) -> [ReadinessMetricDTO] {
        var out: [ReadinessMetricDTO] = []
        if let v = day.hrvMs {
            let d = pct(v, day.hrvBaselineMs)
            let status: MetricStatus = d.map { $0 >= -10 ? .ok : ($0 >= -20 ? .warn : .bad) } ?? .unknown
            out.append(ReadinessMetricDTO(key: "hrv", label: "HRV", value: v, unit: "ms", baseline: day.hrvBaselineMs, deltaPct: d, status: status))
        }
        if let v = day.rhr {
            let d = pct(v, day.rhrBaseline)
            let diff = day.rhrBaseline.map { v - $0 }
            let status: MetricStatus = diff.map { $0 <= 2 ? .ok : ($0 <= 5 ? .warn : .bad) } ?? .unknown
            out.append(ReadinessMetricDTO(key: "rhr", label: "RHR", value: v, unit: "bpm", baseline: day.rhrBaseline, deltaPct: d, status: status))
        }
        if let s = day.sleepS {
            let h = s / 3600
            let status: MetricStatus = h >= 7 ? .ok : (h >= 6 ? .warn : .bad)
            out.append(ReadinessMetricDTO(key: "sleep", label: "Sleep", value: (h * 10).rounded() / 10, unit: "h", baseline: nil, deltaPct: nil, status: status))
        }
        return out
    }
}

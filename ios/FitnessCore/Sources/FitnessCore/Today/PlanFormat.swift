import Foundation

/// Display strings for plan data.
public enum PlanFormat: Sendable {
    /// "3 × 9 @ 75 kg", "9 / 8 / 8 @ 75 kg", "2 × 7 @ 17.5 kg"
    public static func setsSummary(_ sets: [PlannedSetDTO]) -> String {
        guard !sets.isEmpty else { return "" }
        if sets.allSatisfy({ $0.targetDurationS != nil }) {
            let secs = sets.compactMap(\.targetDurationS)
            return "\(sets.count) × \(Fmt.number(secs.first ?? 0, maxFraction: 0)) s"
        }
        let weights = Set(sets.map { $0.targetWeight ?? -1 })
        let reps = sets.map { $0.targetReps }
        let repsText: String
        if Set(reps).count == 1, let r = reps.first {
            repsText = "\(sets.count) × " + (r.map(String.init) ?? "?")
        } else {
            repsText = reps.map { $0.map(String.init) ?? "?" }.joined(separator: " / ")
        }
        if weights.count == 1, let w = sets.first?.targetWeight {
            return "\(repsText) @ \(Fmt.number(w)) kg"
        } else if weights.count == 1 {
            return repsText
        } else {
            let ws = sets.map { $0.targetWeight.map { Fmt.number($0) } ?? "?" }.joined(separator: " / ")
            return "\(repsText) @ \(ws) kg"
        }
    }

    public static func lastSummary(_ last: LastPerformanceDTO?) -> String? {
        guard let last, !last.sets.isEmpty else { return nil }
        let parts = last.sets.map { s -> String in
            if let d = s.durationS { return "\(Fmt.number(d, maxFraction: 0)) s" }
            return "\(s.weight.map { Fmt.number($0) } ?? "–")×\(s.reps.map(String.init) ?? "–")"
        }
        return parts.joined(separator: ", ")
    }

    public static func hr(_ hr: HRRangeDTO?) -> String? {
        guard let lo = hr?.low, let hi = hr?.high else { return nil }
        return "\(Int(lo))–\(Int(hi))"
    }

    public static func pace(_ pace: PaceRangeDTO?) -> String? {
        if let lo = pace?.minSPerKm, let hi = pace?.maxSPerKm { return "\(Fmt.pace(lo))–\(Fmt.pace(hi))" }
        return pace?.label
    }

    public static func runTitle(_ type: String) -> String {
        switch type {
        case "easy_run": "Easy run"
        case "long_run": "Long run"
        case "intervals": "Intervals"
        default: type.capitalized
        }
    }

    /// Today card title. Lifting days only say which half ("Upper body"); the lifts themselves are in the workout.
    public static func cardTitle(type: String, kind: String, title: String?) -> String {
        if type == "lifting" {
            switch kind {
            case "upper": return "Upper body"
            case "lower": return "Lower body"
            case "full": return "Full body"
            default: return title ?? "Lifting"
            }
        }
        return title ?? runTitle(type)
    }

    public static func symbol(forKind kind: String) -> String {
        switch kind {
        case "upper", "full", "other": "dumbbell.fill"
        case "lower": "figure.strengthtraining.functional"
        case "easy_run": "figure.run"
        case "intervals": "speedometer"
        case "long_run": "figure.hiking"
        default: "moon.zzz.fill"
        }
    }

    public static func shortName(forKind kind: String) -> String {
        switch kind {
        case "upper": "Upper"
        case "full", "other": "Lift"
        case "lower": "Lower"
        case "easy_run": "Easy"
        case "intervals": "Intervals"
        case "long_run": "Long"
        default: "Rest"
        }
    }

    /// "last_run" -> "Your last run"; unknown keys are humanised ("weekly_volume" style).
    public static func adaptsOnLabel(_ key: String) -> String {
        switch key.lowercased() {
        case "last_run", "last_runs": return "Your last run"
        case "readiness": return "Your readiness"
        case "weekly_volume", "volume": return "Your weekly volume"
        case "hrv": return "Your HRV"
        case "sleep": return "Your sleep"
        default:
            let words = key.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
            return words.isEmpty ? key : words.prefix(1).uppercased() + words.dropFirst()
        }
    }
}

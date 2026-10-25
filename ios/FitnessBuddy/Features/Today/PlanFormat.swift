import Foundation

/// Display strings for plan data.
enum PlanFormat {
    /// "3 × 9 @ 75 kg", "9 / 8 / 8 @ 75 kg", "2 × 7 @ 17.5 kg"
    static func setsSummary(_ sets: [PlannedSetDTO]) -> String {
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

    static func lastSummary(_ last: LastPerformanceDTO?) -> String? {
        guard let last, !last.sets.isEmpty else { return nil }
        let parts = last.sets.map { s -> String in
            if let d = s.durationS { return "\(Fmt.number(d, maxFraction: 0)) s" }
            return "\(s.weight.map { Fmt.number($0) } ?? "–")×\(s.reps.map(String.init) ?? "–")"
        }
        return parts.joined(separator: ", ")
    }

    static func hr(_ hr: HRRangeDTO?) -> String? {
        guard let lo = hr?.low, let hi = hr?.high else { return nil }
        return "\(Int(lo))–\(Int(hi))"
    }

    static func pace(_ pace: PaceRangeDTO?) -> String? {
        if let lo = pace?.minSPerKm, let hi = pace?.maxSPerKm { return "\(Fmt.pace(lo))–\(Fmt.pace(hi))" }
        return pace?.label
    }

    static func runTitle(_ type: String) -> String {
        switch type {
        case "easy_run": "Easy run"
        case "long_run": "Long run"
        case "intervals": "Intervals"
        default: type.capitalized
        }
    }

    static func symbol(forKind kind: String) -> String {
        switch kind {
        case "upper": "dumbbell.fill"
        case "lower": "figure.strengthtraining.functional"
        case "easy_run": "figure.run"
        case "intervals": "speedometer"
        case "long_run": "figure.hiking"
        default: "moon.zzz.fill"
        }
    }

    static func shortName(forKind kind: String) -> String {
        switch kind {
        case "upper": "Upper"
        case "lower": "Lower"
        case "easy_run": "Easy"
        case "intervals": "Intervals"
        case "long_run": "Long"
        default: "Rest"
        }
    }
}

import Foundation

/// Weight stepping rules for the +/- buttons.
/// 1. If the variant has a `weight_ladder`, step to the next / previous rung.
/// 2. Otherwise (or beyond the ladder's ends) step by the equipment default:
///    barbell 5 kg, dumbbell = configured dumbbell increment, everything else 2.5 kg.
enum WeightStepper {
    static let fallbackStep = 2.5
    static let barbellStep = 5.0
    private static let epsilon = 0.001

    /// `increments` is the backend's `config.increments_kg` (per equipment type, plus `default_stack_step` and
    /// `bodyweight_added`); when absent the built-in defaults apply.
    static func defaultStep(equipmentType: String, dumbbellIncrement: Double?, increments: [String: Double]? = nil) -> Double {
        if equipmentType == "dumbbell", let dumbbellIncrement { return dumbbellIncrement }
        if let step = increments?[equipmentType] { return step }
        switch equipmentType {
        case "barbell": return barbellStep
        case "dumbbell": return dumbbellIncrement ?? fallbackStep
        case "machine_stack", "cable": return increments?["default_stack_step"] ?? fallbackStep
        case "bodyweight": return increments?["bodyweight_added"] ?? fallbackStep
        default: return increments?["other"] ?? fallbackStep
        }
    }

    static func next(from current: Double?, ladder: [Double], equipmentType: String, dumbbellIncrement: Double?,
                     increments: [String: Double]? = nil) -> Double {
        let value = current ?? 0
        if let rung = ladder.sorted().first(where: { $0 > value + epsilon }) { return rung }
        return round2(value + defaultStep(equipmentType: equipmentType, dumbbellIncrement: dumbbellIncrement, increments: increments))
    }

    static func previous(from current: Double?, ladder: [Double], equipmentType: String, dumbbellIncrement: Double?,
                         increments: [String: Double]? = nil) -> Double {
        let value = current ?? 0
        if let rung = ladder.sorted().last(where: { $0 < value - epsilon }) { return rung }
        return max(0, round2(value - defaultStep(equipmentType: equipmentType, dumbbellIncrement: dumbbellIncrement, increments: increments)))
    }

    private static func round2(_ v: Double) -> Double { (v * 100).rounded() / 100 }
}

/// Reps / seconds stepping.
enum CountStepper {
    static func reps(_ current: Int?, delta: Int) -> Int { max(0, (current ?? 0) + delta) }
    static func seconds(_ current: Double?, delta: Double) -> Double { max(0, (current ?? 0) + delta) }
}

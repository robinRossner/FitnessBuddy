import Foundation

enum Fmt {
    /// "80", "77.5", "2.25" (no trailing zeros).
    static func number(_ v: Double, maxFraction: Int = 2) -> String {
        v.formatted(.number.precision(.fractionLength(0...maxFraction)).grouping(.never))
    }

    static func weight(_ kg: Double?) -> String { kg.map { number($0) } ?? "–" }

    static func duration(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds.rounded()))
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }

    /// Human duration "52 min" / "1 h 05 min".
    static func longDuration(_ seconds: TimeInterval) -> String {
        let mins = Int((seconds / 60).rounded())
        return mins >= 60 ? "\(mins / 60) h \(String(format: "%02d", mins % 60)) min" : "\(mins) min"
    }

    /// seconds/km -> "4:10"
    static func pace(_ secPerKm: Double) -> String {
        let t = Int(secPerKm.rounded())
        return String(format: "%d:%02d", t / 60, t % 60)
    }

    static func volume(_ kg: Double) -> String {
        kg >= 10_000 ? number(kg / 1000, maxFraction: 1) + " t" : number(kg.rounded(), maxFraction: 0) + " kg"
    }

    static func km(_ v: Double) -> String { number(v, maxFraction: 1) }
}

extension String {
    /// "Squat (Smith Machine) @ Gym 3" -> "Squat (Smith Machine)": the gym is shown separately.
    var withoutGymSuffix: String {
        guard let r = range(of: " @ ", options: .backwards) else { return self }
        return String(self[..<r.lowerBound])
    }
}

import Foundation

/// The "Show last session" setting: when off, a workout hides what was done last time and the boxes
/// start at today's target instead, so the session reads as "do this" and not "beat that".
public enum LastSession: Sendable {
    /// UserDefaults / @AppStorage key (local only, not synced). Absent means on.
    public static let defaultsKey = "showLastSession"

    public static func isShown(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: defaultsKey) == nil ? true : defaults.bool(forKey: defaultsKey)
    }

    /// Weight / reps / seconds of a set; any part may be unknown.
    public struct Values: Sendable, Equatable {
        public var weight: Double?
        public var reps: Int?
        public var duration: Double?

        public init(weight: Double? = nil, reps: Int? = nil, duration: Double? = nil) {
            self.weight = weight; self.reps = reps; self.duration = duration
        }

        public var isEmpty: Bool { weight == nil && reps == nil && duration == nil }
    }

    /// "50 × 8" or "45 s"; nil when there is nothing to show.
    public static func format(_ v: Values) -> String? {
        if let d = v.duration { return "\(Fmt.number(d, maxFraction: 0)) s" }
        guard v.weight != nil || v.reps != nil else { return nil }
        return "\(Fmt.weight(v.weight)) × \(v.reps.map(String.init) ?? "–")"
    }

    /// The caption under a set row: "Target 50 × 8  ·  Last 47.5 × 8"; the Last part is dropped with `showLast` off.
    public static func caption(target: Values, last: Values, showLast: Bool) -> String? {
        var parts: [String] = []
        if let t = format(target) { parts.append("Target \(t)") }
        if showLast, let l = format(last) { parts.append("Last \(l)") }
        return parts.isEmpty ? nil : parts.joined(separator: "  ·  ")
    }

    /// Starting values of a set that has no plan target of its own (added / swapped exercise, repeated workout)
    /// while last session is hidden: the exercise's target when one is known, else no weight and the top of the
    /// rep range. A timed set keeps its last duration (nothing else marks it as timed), it is just not displayed.
    public static func hiddenPrefill(target: Values?, last: Values?, repRangeHigh: Int?) -> Values {
        if let target, !target.isEmpty {
            return Values(weight: target.weight, reps: target.reps ?? repRangeHigh, duration: target.duration)
        }
        return Values(weight: nil, reps: repRangeHigh, duration: last?.duration)
    }
}

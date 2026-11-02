import Foundation

/// Pure helpers behind the "old → new" lines (unit-tested in CoachTabTests).
public enum CoachChangeFormat: Sendable {
    /// ("82.5", "85 kg"): the unit is written once, after the new value, when both sides carry it.
    public static func parts(from: JSONValue, to: JSONValue, path: String?) -> (old: String, new: String) {
        let old = CoachValueFormat.text(from, path: path)
        let new = CoachValueFormat.text(to, path: path)
        guard let unit = path.flatMap(CoachValueFormat.unit(forPath:)) else { return (old, new) }
        let suffix = unit == "%" ? " %" : " " + unit
        guard old.hasSuffix(suffix), new.hasSuffix(suffix), old.count > suffix.count else { return (old, new) }
        return (String(old.dropLast(suffix.count)), new)
    }

    /// The server's label, or a readable name made from the parameter path when it only echoed the path
    /// ("exercise_overrides.triceps_extension.target_weight" -> "Triceps extension target weight").
    public static func title(label: String, param: String) -> String {
        let trimmed = label.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty, trimmed != param { return trimmed }
        var segments = param.split(separator: ".").map(String.init)
        if segments.count > 2 { segments.removeFirst() }
        let words = segments.joined(separator: " ").replacingOccurrences(of: "_", with: " ")
        guard let first = words.first else { return param }
        return first.uppercased() + words.dropFirst()
    }

    /// One line of a proposal's changes, parameter or schedule.
    public struct Line: Identifiable, Equatable, Sendable {
        public enum Kind: Equatable, Sendable {
            case value(old: String, new: String)
            case schedule(symbol: String)
        }
        public let id: String
        public let title: String
        public let kind: Kind
    }

    /// The lines the pending card shows: still-open changes first (parameter, then schedule); every change when
    /// none is open (e.g. all invalid).
    public static func lines(for p: CoachProposalDTO) -> [Line] {
        let open = p.changes.filter { CoachLogic.isSelectable($0) }
        let openSchedule = p.scheduleChanges.filter { CoachLogic.isSelectable($0) }
        let useOpen = !open.isEmpty || !openSchedule.isEmpty
        let params = useOpen ? open : p.changes
        let schedule = useOpen ? openSchedule : p.scheduleChanges
        return params.map { c in
            let parts = Self.parts(from: c.from, to: c.to, path: c.param)
            return Line(id: c.id, title: title(label: c.label, param: c.param), kind: .value(old: parts.old, new: parts.new))
        } + schedule.map { Line(id: $0.id, title: $0.label, kind: .schedule(symbol: $0.action.symbol)) }
    }
}

/// Weekly check-in usage: one segment per allowed call, filled per call made.
public enum CoachCheckIns: Sendable {
    /// Longest gauge drawn; a larger budget shows the number only.
    public static let maxSegments = 10

    /// Filled flags, nil when there is no usable budget to draw.
    public static func segments(used: Int, limit: Int) -> [Bool]? {
        guard limit > 0, limit <= maxSegments else { return nil }
        let filled = min(max(used, 0), limit)
        return (0..<limit).map { $0 < filled }
    }

    public static func remainingText(used: Int, limit: Int) -> String {
        guard limit > 0 else { return "No automatic check-ins" }
        let left = max(limit - used, 0)
        return left == 0 ? "All used this week" : "\(left) left this week"
    }
}

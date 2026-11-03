import Foundation

/// Weight-stack steps: the generator rule (same as the backend's `/weight-ladder/preview`) and chip editing.
public enum LadderRules: Sendable {
    public static let maxSteps = 60
    public static let quantum = 0.25

    /// Rounds to the nearest 0.25 kg.
    public static func round(_ v: Double) -> Double { (v / quantum).rounded() * quantum }

    /// Sorted, unique (after rounding), only positive finite values, at most `maxSteps`.
    public static func normalize(_ values: [Double]) -> [Double] {
        var seen = Set<Double>()
        var out: [Double] = []
        for v in values where v.isFinite && v > 0 {
            let r = round(v)
            if r > 0, seen.insert(r).inserted { out.append(r) }
        }
        return Array(out.sorted().prefix(maxSteps))
    }

    /// `start + i * step` for `count` steps (count clamped to 60), plus `extra`, normalised. Invalid input gives only `extra`.
    public static func generate(start: Double, step: Double, count: Int, extra: [Double] = []) -> [Double] {
        guard start.isFinite, step.isFinite, start > 0, step > 0, count > 0 else { return normalize(extra) }
        let n = min(count, maxSteps)
        return normalize((0..<n).map { start + Double($0) * step } + extra)
    }

    /// Parses "2,5" / "2.5" / " 12.5 kg ".
    public static func parse(_ text: String) -> Double? {
        let cleaned = text.lowercased().replacingOccurrences(of: "kg", with: "").replacingOccurrences(of: ",", with: ".")
            .trimmingCharacters(in: .whitespaces)
        guard let v = Double(cleaned), v > 0 else { return nil }
        return v
    }

    public static func adding(_ value: Double, to ladder: [Double]) -> [Double] { normalize(ladder + [value]) }

    public static func removing(_ value: Double, from ladder: [Double]) -> [Double] {
        ladder.filter { abs($0 - value) > 0.001 }
    }

    /// Start / step guess for the generator from an existing ladder (first two values), else the defaults.
    public static func generatorSeed(for ladder: [Double]) -> (start: Double, step: Double, count: Int) {
        guard ladder.count >= 2 else { return (5, 5, 15) }
        let step = ladder[1] - ladder[0]
        return (ladder[0], step > 0 ? step : 5, ladder.count)
    }

    /// "15 steps · 5–75 kg"
    public static func summary(_ ladder: [Double]) -> String? {
        guard let lo = ladder.first, let hi = ladder.last else { return nil }
        let n = ladder.count
        return "\(n) step\(n == 1 ? "" : "s") · \(Fmt.number(lo))–\(Fmt.number(hi)) kg"
    }

    public struct Preset: Identifiable, Equatable, Sendable {
        public let title: String
        public let start: Double
        public let step: Double
        public let count: Int
        public var id: String { title }
    }

    public static let presets: [Preset] = [
        Preset(title: "5 kg steps", start: 5, step: 5, count: 15),
        Preset(title: "2.5 kg steps", start: 2.5, step: 2.5, count: 30),
        Preset(title: "7 kg plates", start: 7, step: 7, count: 10),
    ]
}

/// Machine list rules: which exercises count as set up, progress, grouping, the "previous gym" hint.
public enum MachineLogic: Sendable {
    /// Stack machines need their steps; plate-loaded / smith exercises are set up as soon as the machine exists.
    public static func needsLadder(_ equipmentType: String?) -> Bool {
        ["machine_stack", "machine", "cable"].contains(equipmentType ?? "")
    }

    public static func isSetUp(_ item: MachineItemDTO) -> Bool {
        guard let variant = item.variant, variant.active != false else { return false }
        return !needsLadder(variant.equipmentType ?? item.equipmentType) || !variant.ladder.isEmpty
    }

    public struct Progress: Equatable, Sendable {
        public var done: Int
        public var total: Int
        public var remaining: Int { max(0, total - done) }
        public var isComplete: Bool { total > 0 && done >= total }
        /// "6 of 9 program machines set up"
        public var text: String { total == 0 ? "No program machines at this gym" : "\(done) of \(total) program machines set up" }
        public var fraction: Double { total == 0 ? 0 : Double(done) / Double(total) }
    }

    /// Progress over the exercises of the program (other exercises never count).
    public static func progress(_ items: [MachineItemDTO]) -> Progress {
        let program = items.filter(\.inProgram)
        return Progress(done: program.filter(isSetUp).count, total: program.count)
    }

    /// "N machines need setup" (nil when none).
    public static func needsSetupText(_ count: Int?) -> String? {
        guard let count, count > 0 else { return nil }
        return "\(count) machine\(count == 1 ? "" : "s") need\(count == 1 ? "s" : "") setup"
    }

    public struct Group: Identifiable, Equatable, Sendable {
        public var title: String
        public var items: [MachineItemDTO]
        public var id: String { title }
    }

    private static let regionOrder = ["chest", "back", "shoulders", "arms", "legs", "core"]

    public static func regionTitle(_ raw: String?) -> String {
        guard let raw, !raw.isEmpty else { return "Other" }
        return raw.replacingOccurrences(of: "_", with: " ").capitalized
    }

    /// "In your program" grouped by body region, then one "Other exercises" group.
    public static func sections(_ items: [MachineItemDTO]) -> (program: [Group], other: [MachineItemDTO]) {
        let program = Dictionary(grouping: items.filter(\.inProgram)) { regionTitle($0.bodyRegion) }
        func rank(_ title: String) -> Int { regionOrder.firstIndex(of: title.lowercased()) ?? regionOrder.count }
        let groups = program.map { Group(title: $0.key, items: $0.value.sorted { $0.exerciseName < $1.exerciseName }) }
            .sorted { (rank($0.title), $0.title) < (rank($1.title), $1.title) }
        let other = items.filter { !$0.inProgram }.sorted { $0.exerciseName < $1.exerciseName }
        return (groups, other)
    }

    /// "Gym 2: 65 kg × 9" (or "Gym 2: 15 steps · 5–75 kg" when there is no top set).
    public static func previousHint(_ previous: PreviousMachineDTO?) -> String? {
        guard let previous else { return nil }
        let gym = previous.gymName ?? "Previous gym"
        if let w = previous.lastTopSet?.weightKg {
            return "\(gym): \(Fmt.number(w)) kg" + (previous.lastTopSet?.reps.map { " × \($0)" } ?? "")
        }
        if let ladder = previous.weightLadder, let s = LadderRules.summary(ladder) { return "\(gym): \(s)" }
        return nil
    }

    /// Default machine name: "<exercise> @ <gym>".
    public static func defaultName(exercise: String, gym: String) -> String { "\(exercise) @ \(gym)" }

    /// Row status line.
    public static func statusText(_ item: MachineItemDTO) -> String {
        guard isSetUp(item) else { return "Needs setup" }
        if let s = LadderRules.summary(item.variant?.ladder ?? []) { return s }
        return "Set up"
    }

    /// Finds the machine row of a plan exercise: by exercise id, else by slug ("pull_up") or name.
    public static func match(_ items: [MachineItemDTO], exerciseId: Int?, slug: String?, name: String?) -> MachineItemDTO? {
        if let exerciseId, let hit = items.first(where: { $0.exerciseId == exerciseId }) { return hit }
        func norm(_ s: String) -> String {
            s.withoutGymSuffix.lowercased().replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
        }
        if let slug, !slug.isEmpty, let hit = items.first(where: { norm($0.exerciseName) == norm(slug) }) { return hit }
        if let name, !name.isEmpty {
            let n = norm(name)
            return items.first { norm($0.exerciseName) == n } ?? items.first { n.hasPrefix(norm($0.exerciseName)) }
        }
        return nil
    }
}

/// Which gym id the app uses after a pull: the server's value wins, the locally stored one covers offline.
public enum GymSync: Sendable {
    public static func resolve(local: Int?, server: Int?) -> Int? { server ?? local }
}

/// Plan-exercise rules for the machine setup UI (Today card, workout).
public enum SetupRules: Sendable {
    public static let baselineNote = "First session here: pick a weight for ~10 reps"

    public static func showsSetupButton(needsSetup: Bool?) -> Bool { needsSetup == true }
    public static func isBaseline(state: String?) -> Bool { state == "baseline" }

    /// Baseline sets start empty (the hint is only a placeholder); everything else prefills from target, then last.
    /// With last session hidden (`showLast` false) only the target prefills.
    public static func prefillWeight(target: Double?, last: Double?, baseline: Bool, showLast: Bool = true) -> Double? {
        baseline ? nil : (target ?? (showLast ? last : nil))
    }

    /// "50" for the placeholder, nil when there is no hint.
    public static func placeholder(hintWeightKg: Double?) -> String? { hintWeightKg.map { Fmt.number($0) } }
}

extension PlannedExerciseDTO {
    public var showsSetupButton: Bool { SetupRules.showsSetupButton(needsSetup: needsSetup) }
    public var isBaseline: Bool { SetupRules.isBaseline(state: progressionState) }
    public var setupNote: String? { isBaseline ? SetupRules.baselineNote : nil }
}

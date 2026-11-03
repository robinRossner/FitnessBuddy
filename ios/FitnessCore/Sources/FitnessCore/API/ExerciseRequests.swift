import Foundation

/// How an exercise progresses (docs/api/exercises.md "Progression mode"): `load` moves the weight, `reps` is reps-only
/// (the engine never suggests weight), `time` is a hold logged in seconds. `time` can't be switched to or from.
public enum Progression: String, Codable, Sendable, CaseIterable, Equatable {
    case load, reps, time

    /// Picker label.
    public var label: String {
        switch self {
        case .load: "Weight"
        case .reps: "Reps only"
        case .time: "Time"
        }
    }

    /// What the exercise form can change an existing exercise to (the server refuses `time` both ways).
    public static let editable: [Progression] = [.load, .reps]

    /// Older servers send no `progression`: it then follows `is_timed`. An unknown value counts as absent.
    public static func resolve(_ raw: String?, isTimed: Bool?) -> Progression {
        if let raw, let p = Progression(rawValue: raw) { return p }
        return isTimed == true ? .time : .load
    }
}

/// `POST /exercises`. `progression` and `timed` are both sent so older servers (which only know `timed`) still work.
public struct ExerciseCreateRequest: Encodable, Sendable {
    public var name: String
    public var muscleGroup: String
    public var equipmentType: String
    public var timed: Bool
    public var progression: Progression?
    public var gymId: Int?
    public var incrementKg: Double?

    public init(name: String, muscleGroup: String, equipmentType: String, timed: Bool, progression: Progression? = nil,
                gymId: Int?, incrementKg: Double?) {
        self.name = name; self.muscleGroup = muscleGroup; self.equipmentType = equipmentType
        self.timed = progression.map { $0 == .time } ?? timed
        self.progression = progression
        self.gymId = gymId; self.incrementKg = incrementKg
    }
}

/// `PATCH /exercises/{id}`: only the keys that are set are sent.
public struct ExercisePatchRequest: Encodable, Sendable {
    public var name: String?
    public var muscleGroup: String?
    public var progression: Progression?

    public init(name: String? = nil, muscleGroup: String? = nil, progression: Progression? = nil) {
        self.name = name
        self.muscleGroup = muscleGroup
        self.progression = progression
    }
}

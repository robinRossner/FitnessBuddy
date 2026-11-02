import Foundation

/// Every gym endpoint the app uses. The only place that knows the paths.
public enum GymEndpoints: Sendable {
    public static let gyms = "/api/v1/gyms"
    public static let current = "/api/v1/gyms/current"
    public static let ladderPreview = "/api/v1/weight-ladder/preview"
    public static func gym(_ id: Int) -> String { gyms + "/\(id)" }
    public static func machines(_ gymId: Int) -> String { gym(gymId) + "/machines" }
    public static func variant(_ id: Int) -> String { "/api/v1/variants/\(id)" }
}

/// Failure of a gym action, in words for the screen.
public enum GymError: LocalizedError, Equatable, Sendable {
    case needsConnection
    case notConfigured
    /// 409: duplicate name, gym with sessions, current gym, machine already exists.
    case conflict(String)
    case failed(String)

    public var errorDescription: String? {
        switch self {
        case .needsConnection: "Needs a connection"
        case .notConfigured: "Connect your backend in Settings first"
        case .conflict(let m), .failed(let m): m
        }
    }

    public static func map(_ error: Error) -> GymError {
        if let e = error as? GymError { return e }
        if let api = error as? APIError {
            switch api {
            case .transport: return .needsConnection
            case .notConfigured: return .notConfigured
            case .http(409, let body): return .conflict(CoachError.detail(body) ?? "This conflicts with existing data.")
            case .http(let code, let body): return .failed(CoachError.detail(body) ?? "Server error \(code)")
            default: return .failed(api.errorDescription ?? "Something went wrong")
            }
        }
        return .failed(error.localizedDescription)
    }
}

/// What the machine editor sends.
public struct MachineDraft: Equatable, Sendable {
    public var name: String
    public var equipmentType: String
    public var notes: String
    public var ladder: [Double]

    public var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// A save is allowed with a name, and (for stack machines) at least one step.
    public var isValid: Bool { !trimmedName.isEmpty && (!MachineLogic.needsLadder(equipmentType) || !ladder.isEmpty) }

    public init(name: String, equipmentType: String, notes: String, ladder: [Double]) {
        self.name = name
        self.equipmentType = equipmentType
        self.notes = notes
        self.ladder = ladder
    }
}

extension String {
    public var trimmedOrNil: String? {
        let t = trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}

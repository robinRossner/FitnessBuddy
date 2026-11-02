import Foundation

/// The app's one colour language (Apple Health style): every area has one accent and it always means the same thing.
/// Category headers, card labels, sparklines and chart marks of that area use `color` (a SwiftUI extension in the
/// app's `Theme.swift`). Everything is a system/adaptive colour so dark mode is free.
///
/// - `lifting` orange, `running` teal-green, `recovery` (= readiness) indigo: the training domains (`forKind`).
/// - `heart` pink (HRV, resting HR), `sleep` cyan, `body` blue (bodyweight), `activity` green (steps),
///   `coach` purple, `insights` teal (correlations).
public enum Domain: Hashable, Sendable {
    case lifting, running, recovery
    case heart, sleep, body, activity, coach, insights

    /// Readiness is the recovery domain.
    public static let readiness = Domain.recovery

    public var symbol: String {
        switch self {
        case .lifting: "dumbbell.fill"
        case .running: "figure.run"
        case .recovery: "bolt.heart.fill"
        case .heart: "heart.fill"
        case .sleep: "bed.double.fill"
        case .body: "figure"
        case .activity: "figure.walk"
        case .coach: "sparkles"
        case .insights: "point.3.connected.trianglepath.dotted"
        }
    }

    public static func forKind(_ kind: String) -> Domain {
        switch kind {
        case "upper", "lower", "full", "other": .lifting
        case "easy_run", "intervals", "long_run": .running
        default: .recovery
        }
    }
}

public enum ReadinessLevel: String, Sendable {
    case green, yellow, red, unknown

    public init(color: String?, known: Bool = true) {
        guard known, let color, let level = ReadinessLevel(rawValue: color.lowercased()) else {
            self = .unknown
            return
        }
        self = level
    }

    public var title: String {
        switch self {
        case .green: "Ready"
        case .yellow: "Take it easier"
        case .red: "Recover"
        case .unknown: "No data yet"
        }
    }

    public var symbol: String {
        switch self {
        case .green: "checkmark"
        case .yellow: "exclamationmark"
        case .red: "xmark"
        case .unknown: "questionmark"
        }
    }
}

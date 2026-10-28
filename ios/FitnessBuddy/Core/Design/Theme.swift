import SwiftUI

/// The app's one colour language (Apple Health style): every area has one accent and it always means the same thing.
/// Category headers, card labels, sparklines and chart marks of that area use `color`. Everything is a system/adaptive
/// colour so dark mode is free.
///
/// - `lifting` orange, `running` teal-green, `recovery` (= readiness) indigo: the training domains (`forKind`).
/// - `heart` pink (HRV, resting HR), `sleep` cyan, `body` blue (bodyweight), `activity` green (steps),
///   `coach` purple, `insights` teal (correlations).
enum Domain: Hashable, Sendable {
    case lifting, running, recovery
    case heart, sleep, body, activity, coach, insights

    /// Readiness is the recovery domain.
    static let readiness = Domain.recovery

    var color: Color {
        switch self {
        case .lifting: .orange
        case .running: Color(red: 0.0, green: 0.72, blue: 0.62)  // teal-green
        case .recovery: .indigo
        case .heart: .pink
        case .sleep: .cyan
        case .body: .blue
        case .activity: .green
        case .coach: .purple
        case .insights: .teal
        }
    }

    var symbol: String {
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

    static func forKind(_ kind: String) -> Domain {
        switch kind {
        case "upper", "lower", "full", "other": .lifting
        case "easy_run", "intervals", "long_run": .running
        default: .recovery
        }
    }
}

enum ReadinessLevel: String, Sendable {
    case green, yellow, red, unknown

    init(color: String?, known: Bool = true) {
        guard known, let color, let level = ReadinessLevel(rawValue: color.lowercased()) else {
            self = .unknown
            return
        }
        self = level
    }

    var color: Color {
        switch self {
        case .green: .green
        case .yellow: .yellow
        case .red: .red
        case .unknown: .gray
        }
    }

    var title: String {
        switch self {
        case .green: "Ready"
        case .yellow: "Take it easier"
        case .red: "Recover"
        case .unknown: "No data yet"
        }
    }

    var symbol: String {
        switch self {
        case .green: "checkmark"
        case .yellow: "exclamationmark"
        case .red: "xmark"
        case .unknown: "questionmark"
        }
    }
}

extension Font {
    /// Big rounded numerals (weights, timers, stats). Scales with Dynamic Type.
    static func metric(_ style: Font.TextStyle = .largeTitle, weight: Font.Weight = .bold) -> Font {
        .system(style, design: .rounded).weight(weight)
    }
}

extension View {
    func metricStyle(_ style: Font.TextStyle = .largeTitle, weight: Font.Weight = .bold) -> some View {
        font(.metric(style, weight: weight)).monospacedDigit()
    }
}

extension Color {
    static let cardBackground = Color(.secondarySystemGroupedBackground)
    static let screenBackground = Color(.systemGroupedBackground)
}

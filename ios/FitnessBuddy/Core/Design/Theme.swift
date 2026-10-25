import SwiftUI

/// Colour per training domain. Everything is a system/adaptive colour so dark mode is free.
enum Domain {
    case lifting, running, recovery

    var color: Color {
        switch self {
        case .lifting: .orange
        case .running: Color(red: 0.0, green: 0.72, blue: 0.62)  // teal-green
        case .recovery: .indigo
        }
    }

    var symbol: String {
        switch self {
        case .lifting: "dumbbell.fill"
        case .running: "figure.run"
        case .recovery: "heart.fill"
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

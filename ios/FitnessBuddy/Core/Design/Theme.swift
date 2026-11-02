import FitnessCore
import SwiftUI

extension Domain {
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
}

extension ReadinessLevel {
    var color: Color {
        switch self {
        case .green: .green
        case .yellow: .yellow
        case .red: .red
        case .unknown: .gray
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

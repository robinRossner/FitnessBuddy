// Health tab: accessibility identifiers of its screens (sheets, pushed screens) and of the controls the
// navigation test taps. Plain strings, no imports (the UI test target compiles this folder too).
extension AXID {
    enum Health {
        // Screens
        static let readiness = "health.readiness"
        static let logWeight = "health.logWeight"
        static let metricDetail = "health.metricDetail"
        // Controls
        static let readinessCard = "health.readinessCard"
        /// A signal card of the Health tab by metric id (`hrv`, `rhr`, `sleep`, `steps`, `bodyweight`).
        static func signalCard(_ metricId: String) -> String { "health.signal.\(metricId)" }
    }
}

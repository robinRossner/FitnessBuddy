// Accessibility identifiers the UI tests (screenpass) look for. Plain strings with no imports,
// because FitnessBuddyUITests compiles this folder too. Each tab adds its own identifiers in
// `AXID+<Tab>.swift` (`extension AXID { enum Coach { ... } }`).
enum AXID {
    /// Root of each tab, set on the tab's top-level view.
    enum Screen {
        static let summary = "screen.summary"
        static let workout = "screen.workout"
        static let progress = "screen.progress"
        static let health = "screen.health"
        static let coach = "screen.coach"
    }
}

// Summary tab: accessibility identifiers of its screens (sheets, pushed screens) and of the controls the
// navigation test taps. Plain strings, no imports (the UI test target compiles this folder too).
extension AXID {
    enum Summary {
        // Screens
        static let settings = "summary.settings"
        static let gymManage = "summary.gymManage"
        static let gymMachines = "summary.gymMachines"
        static let machineEditor = "summary.machineEditor"
        static let pinEditor = "summary.pinEditor"
        static let plannedLift = "summary.plannedLift"
        static let metricDetail = "summary.metricDetail"
        // Controls
        static let settingsButton = "summary.settingsButton"
        static let nextLiftCard = "summary.nextLiftCard"
        static let manageGymsRow = "summary.manageGymsRow"
        static let settingsDone = "summary.settingsDone"
    }
}

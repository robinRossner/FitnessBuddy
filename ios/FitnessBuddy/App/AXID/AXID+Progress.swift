// Progress tab screens and the controls the navigation test taps (see AXID.swift).
extension AXID {
    enum Progress {
        /// Set instead of the screen's own identifier while its data is still loading, so a wait never matches a spinner.
        static let loading = "progress.loading"

        // Segments
        static let running = "progress.running"
        static let insights = "progress.insights"

        // Pushed screens
        static let liftDetail = "progress.liftDetail"
        static let runAnalysis = "progress.runAnalysis"
        static let vdot = "progress.vdot"
        static let efficiency = "progress.efficiency"
        static let allRuns = "progress.allRuns"
        static let insightsScreen = "progress.insightsScreen"
        static let insightDetail = "progress.insightDetail"

        // Sheets
        static let allLifts = "progress.allLifts"
        static let runDetail = "progress.runDetail"

        // Controls
        static let allLiftsButton = "progress.allLiftsButton"
        static let allLiftsDone = "progress.allLiftsDone"
        /// A lift card of the Lifting segment, keyed by variant id.
        static func liftCard(_ variantId: Int) -> String { "progress.liftCard.\(variantId)" }
    }
}

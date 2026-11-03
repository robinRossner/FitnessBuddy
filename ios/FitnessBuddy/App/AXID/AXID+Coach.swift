// Coach tab screens and the controls the navigation test taps (see AXID.swift).
extension AXID {
    enum Coach {
        /// Set instead of the screen's own identifier while its data is still loading, so a wait never matches a spinner.
        static let loading = "coach.loading"

        // Pushed screens
        static let proposalDetail = "coach.proposalDetail"
        static let history = "coach.history"
        static let settings = "coach.settings"
        static let proposalList = "coach.proposalList"

        // Sheets
        static let askSheet = "coach.askSheet"

        // Controls
        static let settingsButton = "coach.settingsButton"
        static let askButton = "coach.askButton"
        static let askCancel = "coach.askCancel"
    }
}

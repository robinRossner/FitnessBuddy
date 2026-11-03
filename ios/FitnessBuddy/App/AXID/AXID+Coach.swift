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
        static let askField = "coach.askField"
        static let askSend = "coach.askSend"
        /// Approve / Decline on a card of the To Review section.
        static let cardApprove = "coach.cardApprove"
        static let cardDecline = "coach.cardDecline"
        /// The result card shown after a decision from the To Review section.
        static let decisionResult = "coach.decisionResult"
        /// "Approve (n)" in the proposal detail's action bar.
        static let detailApprove = "coach.detailApprove"
    }
}

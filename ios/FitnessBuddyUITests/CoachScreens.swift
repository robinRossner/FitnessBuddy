import XCTest

/// Coach tab: every pushed screen and the Ask sheet, opened with the DEBUG launch arguments of `CoachView`
/// (`-uiRoute` / `-uiSheet`, honoured only with `-uiTab coach`); `testNavigation` taps through for real instead.
@MainActor
final class CoachScreens: ScreenpassCase {
    private func shot(_ name: String, screen: String, extra: [String]) {
        launch(tab: "coach", extra: extra)
        waitForScreen(screen)
        snap(name)
    }

    /// The pending demo proposal (lift stall) with its diagnosis, changes and the Approve / Decline bar.
    func testProposalDetail() {
        shot("coach-proposal-detail", screen: AXID.Coach.proposalDetail, extra: ["-uiRoute", "proposal:demo-p1"])
    }

    func testHistory() {
        shot("coach-history", screen: AXID.Coach.history, extra: ["-uiRoute", "history"])
    }

    func testSettings() {
        shot("coach-settings", screen: AXID.Coach.settings, extra: ["-uiRoute", "settings"])
    }

    func testProposalList() {
        shot("coach-proposal-list", screen: AXID.Coach.proposalList, extra: ["-uiRoute", "proposals"])
    }

    /// The sheet without the keyboard: nothing in it takes focus until a suggestion or the field is tapped.
    func testAskSheet() {
        shot("coach-ask-sheet", screen: AXID.Coach.askSheet, extra: ["-uiSheet", "ask"])
    }

    /// Real taps from the tab root: the gear pushes Coach Settings, then "Ask the Coach" opens its sheet.
    func testNavigationSettingsAndAskSheet() {
        launch(tab: "coach")
        waitForScreen(AXID.Screen.coach)

        element(AXID.Coach.settingsButton).tap()
        waitForScreen(AXID.Coach.settings)
        app.navigationBars.buttons.element(boundBy: 0).tap()
        waitForScreen(AXID.Screen.coach)

        element(AXID.Coach.askButton).tap()
        waitForScreen(AXID.Coach.askSheet)
        element(AXID.Coach.askCancel).tap()
        XCTAssertTrue(element(AXID.Coach.askSheet).waitForNonExistence(timeout: 10), "Ask sheet did not close")
    }

    private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id].firstMatch
    }
}

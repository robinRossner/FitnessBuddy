import XCTest

/// Phase 1: the five tab roots. Later phases add SummaryScreens.swift etc. as further
/// ScreenpassCase subclasses; `-only-testing:FitnessBuddyUITests/<Class>[/<test>]` filters.
@MainActor
final class TabScreens: ScreenpassCase {
    func testSummary() {
        launch(tab: "summary")
        waitForScreen(AXID.Screen.summary)
        snap("tab-summary")
    }

    func testWorkout() {
        launch(tab: "workout")
        waitForScreen(AXID.Screen.workout)
        snap("tab-workout")
    }

    func testProgress() {
        launch(tab: "progress")
        waitForScreen(AXID.Screen.progress)
        snap("tab-progress")
    }

    func testHealth() {
        launch(tab: "health")
        waitForScreen(AXID.Screen.health)
        snap("tab-health")
    }

    func testCoach() {
        launch(tab: "coach")
        waitForScreen(AXID.Screen.coach)
        snap("tab-coach")
    }
}
